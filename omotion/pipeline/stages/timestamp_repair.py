"""TimestampRepairStage — EMI timestamp correction + NaN-fill.

Division of labour (sdk#220): a corrupted frame COUNTER is quarantined
upstream by FrameClassificationStage's unwrapper (counter step vs clock
cross-check) and never reaches this stage. This stage owns the other
corruption — frames whose counter is honest but whose TIMESTAMP is not —
detected via two conditions:
  1. Cadence deviation: |actual_Δt - expected_Δt| > tolerance, with the
     expected period EMA-tracked from clean single-step intervals. An
     abs_frame_id regression (impossible for unwrapper-accepted frames)
     is flagged too, never trusted.
  2. In-packet frame_id disagreement: cameras in one packet share the
     capture timestamp, so their frame_ids must agree. Majority vote —
     only the minority rows are flagged; with two cameras or a tie the
     whole group is flagged (nothing to adjudicate with).

Bad frames get their timestamps corrected in-place by interpolating
between the camera's last good frame and the next good frame in the
batch; a right-anchor candidate is only trusted when its own timestamp
is consistent with the left anchor at the nominal cadence. With no
usable right anchor the fallback is left anchor + gap x nominal period.
Missing abs_frame_id gaps (USB loss or quarantined frames) get synthetic
NaN-fill rows inserted (the only case that rebuilds the batch), with
timestamps interpolated across the real gap.

Misalignment windows are tracked PER SIDE and coalesced. Every window is
dispatched as a TimestampMisalignmentWindow diagnostics event (the scan
DB summary must be complete); the WARNING log line is throttled to one
per side per _WINDOW_LOG_MIN_INTERVAL_S with a suppressed-window count,
because sustained intermittent corruption churns windows on nearly every
bad→good alternation and would otherwise flood the log. Window closes
are flushed at end-of-batch, after NaN-fill collection, so fill counts
attribute to the window they belong to. The firmware's terminal stop
frame — the laser-off frame fired ~150 ms off the 25 ms grid at every
scan stop — is recognised at on_scan_stop and reclassified as an
expected artifact (INFO, excluded from the misalignment record).

Regression suite: tests/test_pipeline/test_etch_a_sketch_repro.py.
Design history: the original timestamp-repair design doc (removed from docs/)
is retrievable from git history at
docs/superpowers/specs/2026-06-05-eft-timestamp-repair-design.md.
"""

from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..batch import (
    FrameBatch, FrameGapFillAnomaly, TimestampMisalignmentWindow,
    TimestampRepairInputAnomaly,
)

logger = logging.getLogger("openmotion.sdk.pipeline.stages.timestamp_repair")

_INITIAL_NOMINAL_PERIOD_S = 0.025
_DEFAULT_TOLERANCE_S = 0.008
_TOLERANCE_EPS_S = 1e-9
_EMA_ALPHA = 0.01
# Minimum wall-clock spacing between per-side "Misalignment window" WARNINGs.
# Under sustained intermittent corruption windows open and close on nearly
# every bad→good alternation; unthrottled that degrades the coalesced-window
# design into a per-frame log flood (the sdk#220 sustained-corruption presentation).
# Suppressed windows are still recorded and dispatched as diagnostics events —
# only the log line is withheld, and the next emitted line reports how many
# were suppressed.
_WINDOW_LOG_MIN_INTERVAL_S = 2.0


@dataclass
class _WindowStats:
    """Running stats for one contiguous per-side misalignment window, used
    for coalesced logging: the frame-id/time span it spans, how many frames
    within it were re-timestamped vs NaN-filled, and which (side, cam)
    frames it flagged (for the terminal stop-frame check)."""
    side: int
    onset_fid: int
    onset_t: float
    end_fid: int = 0
    end_t: float = 0.0
    n_corrected: int = 0
    n_nan: int = 0
    # (key, abs_fid) of each flagged frame — consulted at scan stop to
    # recognise the firmware's terminal stop-frame artifact.
    frames: list = field(default_factory=list)


class TimestampRepairStage:
    """Pipeline stage that repairs EMI-corrupted capture timestamps.

    See the module docstring for the algorithm. One instance per scan;
    cross-batch state (nominal-period estimate, per-camera last-good
    anchors, the open misalignment window) persists across process() calls
    and is cleared by reset()."""

    name = "timestamp_repair"

    def __init__(self, *, tolerance_s: float = _DEFAULT_TOLERANCE_S,
                 max_buffer_frames: int = 16):
        """Configure the stage.

        Args:
            tolerance_s: max |actual Δt − expected Δt| (seconds) before a
                frame's timestamp is treated as EMI-corrupted (condition 1).
            max_buffer_frames: reserved bound on the look-ahead used when
                re-anchoring a divergent run.
        """
        self._tolerance = float(tolerance_s)
        self._max_buffer = int(max_buffer_frames)
        self._reset_state()

    def _reset_state(self) -> None:
        """Clear all per-scan state — nominal-period estimate, per-camera
        last-good anchors, the open per-side windows and window list, and the
        running re-timestamped / NaN-filled totals. Called from __init__ and
        reset()."""
        self._nominal_period = _INITIAL_NOMINAL_PERIOD_S
        self._last_good: dict[tuple[int, int], tuple[int, float]] = {}
        self._open_window: dict[int, Optional[_WindowStats]] = {0: None, 1: None}
        self._scan_windows: list[_WindowStats] = []
        self._total_frames_seen = 0
        self._nan_last_seen: dict[tuple[int, int], tuple[int, float]] = {}
        self._total_corrected = 0
        self._total_nan = 0
        self._terminal_frames = 0
        # Windows closed during the current process() call, held so NaN-fill
        # attribution (which runs after the detection pass) can still reach
        # them; logged/dispatched by _flush_pending_closes at end of batch.
        self._pending_closed: dict[int, list] = {0: [], 1: []}
        # Frozen-clock evidence (sensor-fw#123 timestamp_freeze): per-camera
        # last WIRE timestamp (pre-correction), the in-batch tally of frames
        # carrying a reused packet timestamp, and the per-scan set of
        # (side, ts) values already reported so each frozen value emits one
        # TimestampRepairInputAnomaly.
        self._wire_prev: dict[tuple[int, int], tuple[int, float]] = {}
        self._frozen_pending: dict[tuple[int, float], int] = {}
        self._frozen_emitted: set[tuple[int, float]] = set()
        # Per-side WARNING throttle state (see _WINDOW_LOG_MIN_INTERVAL_S).
        self._log_last_s: dict[int, float] = {0: float("-inf"), 1: float("-inf")}
        self._log_suppressed: dict[int, int] = {0: 0, 1: 0}

    # ── Main entry ──────────────────────────────────────────────────────

    def process(self, batch: FrameBatch) -> FrameBatch:
        """Detect and repair EMI timestamp corruption for one batch.

        Flags each non-warmup/stale frame via condition 2 (in-packet
        frame_id disagreement) or condition 1 (timestamp deviation vs the
        frame_id cadence), rewrites bad timestamps in place by re-anchoring
        interpolation, and inserts synthetic NaN-fill rows for missing
        abs_frame_id gaps. Sets ``batch.quality`` and returns the batch
        (a new, larger batch when NaN-fills were inserted)."""
        if batch.abs_frame_ids is None or batch.frame_type is None:
            return batch

        n = len(batch.cam_ids)
        if n == 0:
            batch.quality = np.empty(0, dtype="<U14")
            return batch

        self._total_frames_seen += n
        quality = np.full(n, "ok", dtype="<U14")

        # Condition 2 (batch-wide): in-packet frame_id disagreement
        bad_cond2 = self._detect_frame_id_disagreement(batch)

        # Pre-build good-frame lookahead for re-anchoring.
        # A frame is "provisionally good" if it passes condition 2.
        # Condition 1 is checked inline below (needs _last_good context).
        good_ahead = self._build_good_lookahead(batch, bad_cond2)

        # Single pass: detect condition 1 inline, correct, track windows
        for i in range(n):
            ftype = str(batch.frame_type[i])
            if ftype in ("warmup", "stale"):
                continue

            cam_id = int(batch.cam_ids[i])
            side_idx = int(batch.side_ids[i])
            abs_fid = int(batch.abs_frame_ids[i])
            ts = float(batch.timestamp_s[i])
            key = (side_idx, cam_id)

            # Frozen-clock signature (sensor-fw#123 timestamp_freeze): the
            # frame counter advanced but the packet timestamp is a reuse of
            # this camera's previous WIRE value. Tracked on wire timestamps
            # (pre-correction) so consecutive frozen packets all register.
            wire_prev = self._wire_prev.get(key)
            if (wire_prev is not None and abs_fid > wire_prev[0]
                    and abs(ts - wire_prev[1]) < 1e-9):
                fkey = (side_idx, round(ts, 6))
                self._frozen_pending[fkey] = self._frozen_pending.get(fkey, 0) + 1
            self._wire_prev[key] = (abs_fid, ts)

            # Condition 1: timestamp deviation (checked inline with _last_good)
            is_bad = i in bad_cond2
            if not is_bad and key in self._last_good:
                prev_fid, prev_ts = self._last_good[key]
                fid_gap = abs_fid - prev_fid
                if fid_gap > 0:
                    expected_dt = fid_gap * self._nominal_period
                    actual_dt = ts - prev_ts
                    if abs(actual_dt - expected_dt) > self._tolerance + _TOLERANCE_EPS_S:
                        is_bad = True
                else:
                    # abs_frame_id regression/duplicate. The unwrapper never
                    # emits these for accepted frames, so reaching here means
                    # corrupted state slipped through — flag it; treating it
                    # as good would silently re-anchor _last_good backwards
                    # and close a live window on a bogus frame.
                    is_bad = True

            if is_bad:
                corrected_ts = self._interpolate(key, abs_fid, good_ahead.get(key))
                batch.timestamp_s[i] = corrected_ts
                quality[i] = "ts_corrected"
                self._total_corrected += 1
                self._track_window_open(side_idx, key, abs_fid, ts)
                self._last_good[key] = (abs_fid, corrected_ts)
            else:
                self._track_window_close(side_idx)
                self._update_nominal_period(key, abs_fid, ts)
                self._last_good[key] = (abs_fid, ts)

        batch.quality = quality

        # Insert NaN-fill rows for missing abs_frame_id gaps. Runs before the
        # pending-close flush so fills are attributed to the window they
        # belong to even when it closed earlier in this same batch.
        nan_fills = self._collect_nan_fills(batch)
        if nan_fills:
            batch = self._insert_nan_fills(batch, nan_fills)

        self._flush_frozen(batch.events)
        self._flush_pending_closes(batch.events)
        return batch

    def _flush_frozen(self, events: list) -> None:
        """Emit one TimestampRepairInputAnomaly per newly seen frozen
        (side, timestamp) value; later frames carrying an already-reported
        value add nothing (the evidence is out)."""
        for (side, ts), count in self._frozen_pending.items():
            if (side, ts) in self._frozen_emitted:
                continue
            self._frozen_emitted.add((side, ts))
            events.append(TimestampRepairInputAnomaly(
                side=side, timestamp_s=ts, n_frames=count))
        self._frozen_pending = {}

    # ── Detection ───────────────────────────────────────────────────────

    def _detect_frame_id_disagreement(self, batch: FrameBatch) -> set[int]:
        """Condition 2: cameras at the same timestamp with different frame_ids.

        Compares UNWRAPPED ids: FrameClassificationStage's packet consensus
        repairs wire-level disagreement before unwrapping, so a row it
        corrected agrees here and is not flagged twice.

        Majority vote: cameras in one packet share the capture instant, so
        when their frame ids disagree the minority is the liar — flag only
        those rows. Flagging the whole group (the old behavior) let one
        corrupted camera condemn every innocent sibling in the packet
        (sdk#220: 18 frames "re-timestamped" when ~3 were corrupt). With no
        strict majority (two cameras, or a tie) there is nothing to
        adjudicate with, so the whole group is flagged as before."""
        bad: set[int] = set()
        groups: dict[tuple[int, float], list[int]] = defaultdict(list)
        for i in range(len(batch.cam_ids)):
            ft = str(batch.frame_type[i])
            if ft in ("warmup", "stale"):
                continue
            groups[(int(batch.side_ids[i]), float(batch.timestamp_s[i]))].append(i)
        for indices in groups.values():
            if len(indices) < 2:
                continue
            fids = [int(batch.abs_frame_ids[j]) for j in indices]
            if len(set(fids)) <= 1:
                continue
            majority_fid, majority_n = Counter(fids).most_common(1)[0]
            if majority_n * 2 > len(indices):
                bad.update(j for j, f in zip(indices, fids)
                           if f != majority_fid)
            else:
                bad.update(indices)
        return bad

    # ── Look-ahead for re-anchoring ─────────────────────────────────────

    def _build_good_lookahead(self, batch: FrameBatch,
                              bad_set: set[int]) -> dict[tuple[int, int], list]:
        """Collect, per (side, cam), the (abs_fid, ts) of frames not flagged
        by condition 2 and not warmup/stale — the candidate right-anchors
        that ``_interpolate`` searches when re-anchoring a bad frame."""
        ahead: dict[tuple[int, int], list] = defaultdict(list)
        for i in range(len(batch.cam_ids)):
            if i in bad_set:
                continue
            ft = str(batch.frame_type[i])
            if ft in ("warmup", "stale"):
                continue
            key = (int(batch.side_ids[i]), int(batch.cam_ids[i]))
            ahead[key].append((int(batch.abs_frame_ids[i]), float(batch.timestamp_s[i])))
        return ahead

    def _interpolate(self, key: tuple[int, int], abs_fid: int,
                     good_frames: list | None) -> float:
        """Re-anchor a bad frame's timestamp by interpolation.

        Interpolates between the last good timestamp for this (side, cam)
        and the next good one in ``good_frames`` (the within-batch
        look-ahead), distributed by frame_id count. Falls back to the last
        good anchor plus the nominal period when there's no right anchor,
        and to abs_fid × nominal period when there's no anchor at all
        (start of scan)."""
        if key in self._last_good:
            left_fid, left_ts = self._last_good[key]
        else:
            return abs_fid * self._nominal_period

        # Try to find a right anchor from the look-ahead. The look-ahead only
        # excludes condition-2-flagged rows — a row that condition 1 will
        # flag later in this same pass is still in the list, carrying a
        # corrupted timestamp. Guard against anchoring on it: a candidate is
        # only trusted when its timestamp is self-consistent with the left
        # anchor at the nominal cadence.
        if good_frames:
            for right_fid, right_ts in good_frames:
                if right_fid <= abs_fid:
                    continue
                fid_span = right_fid - left_fid
                if fid_span <= 0:
                    continue
                expected_span_s = fid_span * self._nominal_period
                anchor_slack_s = max(2 * self._tolerance,
                                     0.1 * expected_span_s)
                if abs((right_ts - left_ts) - expected_span_s) > anchor_slack_s:
                    continue  # candidate's own timestamp looks corrupt
                return left_ts + (abs_fid - left_fid) / fid_span * (right_ts - left_ts)

        # Fallback: nominal period from left anchor
        return left_ts + (abs_fid - left_fid) * self._nominal_period

    # ── Nominal period tracking ─────────────────────────────────────────

    def _update_nominal_period(self, key: tuple[int, int],
                               abs_fid: int, ts: float) -> None:
        """Refine the true frame period via an EMA over clean single-step
        intervals (only when this frame is exactly one frame_id past the
        last good frame for ``key``). Tracks the real ~25.02 ms cadence so
        the expected-Δt check in process() doesn't drift off nominal."""
        if key not in self._last_good:
            return
        prev_fid, prev_ts = self._last_good[key]
        if abs_fid - prev_fid == 1:
            dt = ts - prev_ts
            if dt > 0:
                self._nominal_period = (
                    (1 - _EMA_ALPHA) * self._nominal_period + _EMA_ALPHA * dt
                )

    # ── Window tracking (coalesced logging) ─────────────────────────────

    def _track_window_open(self, side: int, key: tuple[int, int],
                           abs_fid: int, ts: float) -> None:
        """Open this side's misalignment window (or extend it) and count the
        re-timestamped frame, for coalesced per-window logging. ``ts`` is the
        original pre-correction device timestamp."""
        w = self._open_window[side]
        if w is None:
            w = _WindowStats(side=side, onset_fid=abs_fid, onset_t=ts)
            self._open_window[side] = w
        w.end_fid = abs_fid
        w.end_t = ts
        w.n_corrected += 1
        w.frames.append((key, abs_fid))

    def _track_window_close(self, side: int) -> None:
        """Close this side's open misalignment window, if any, by moving it
        to the pending list. Called on the first good same-side frame after
        a divergent run. Recording/logging/event dispatch happen in
        _flush_pending_closes at the end of the batch — after NaN-fill
        collection, so fills land in the window they belong to."""
        w = self._open_window[side]
        if w is None:
            return
        self._open_window[side] = None
        self._pending_closed[side].append(w)

    def _flush_pending_closes(self, events: list, *, force_log: bool = False) -> None:
        """Record each pending closed window, dispatch its
        TimestampMisalignmentWindow diagnostics event (always — the scan DB
        summary must be complete), and emit the coalesced WARNING, throttled
        to one per side per _WINDOW_LOG_MIN_INTERVAL_S. Under sustained
        intermittent corruption, windows churn on nearly every bad→good
        alternation and unthrottled logging degrades to a per-frame flood;
        the throttle keeps the terminal readable while the suppressed count
        keeps the volume honest. ``force_log`` (scan stop) bypasses the
        throttle so the final window is never silent."""
        for side in (0, 1):
            pending = self._pending_closed[side]
            if not pending:
                continue
            self._pending_closed[side] = []
            for w in pending:
                self._scan_windows.append(w)
                events.append(TimestampMisalignmentWindow(
                    side=w.side, onset_fid=w.onset_fid, end_fid=w.end_fid,
                    onset_t=w.onset_t, end_t=w.end_t,
                    n_corrected=w.n_corrected, n_nan=w.n_nan,
                ))
                now_s = time.monotonic()
                if (not force_log and
                        now_s - self._log_last_s[side] < _WINDOW_LOG_MIN_INTERVAL_S):
                    self._log_suppressed[side] += 1
                    continue
                suppressed = self._log_suppressed[side]
                self._log_suppressed[side] = 0
                self._log_last_s[side] = now_s
                logger.warning(
                    "Misalignment window: side=%d frames %d-%d (t=%.2f-%.2fs), "
                    "%d frames re-timestamped, %d frames NaN-filled%s",
                    w.side, w.onset_fid, w.end_fid, w.onset_t, w.end_t,
                    w.n_corrected, w.n_nan,
                    (f" (+{suppressed} more window(s) since last report)"
                     if suppressed else ""),
                )

    def _is_terminal_artifact(self, w: _WindowStats) -> bool:
        """True when a window still open at scan stop is the firmware's
        terminal stop frame: every flagged frame is the LAST frame its
        (side, cam) ever produced, and each camera was flagged exactly once.
        (The laser-off stop frame fires ~150 ms off the 25 ms grid by
        protocol — its timestamp is truthful, just not on the capture
        schedule.) A genuine end-of-scan EMI burst flags multiple frames per
        camera and stays a real window."""
        if not w.frames:
            return False
        seen_keys: set = set()
        for key, fid in w.frames:
            if key in seen_keys:
                return False  # >1 flagged frame for this camera — real run
            seen_keys.add(key)
            last = self._nan_last_seen.get(key)
            if last is None or fid != last[0]:
                return False  # frames followed it — not the terminal frame
        return True

    # ── NaN-fill for missing frames ─────────────────────────────────────

    def _collect_nan_fills(self, batch: FrameBatch) -> list[tuple[int, dict]]:
        """Find gaps in abs_frame_id per (side, cam) and build fill descriptors.

        Returns [(insert_after_idx, fill_dict), ...] sorted by insert position.
        """
        fills = []
        for i in range(len(batch.cam_ids)):
            ft = str(batch.frame_type[i])
            if ft in ("warmup", "stale"):
                continue
            key = (int(batch.side_ids[i]), int(batch.cam_ids[i]))
            abs_fid = int(batch.abs_frame_ids[i])
            ts = float(batch.timestamp_s[i])

            if key in self._nan_last_seen:
                prev_fid, prev_ts = self._nan_last_seen[key]
                gap = abs_fid - prev_fid
                if gap > 1:
                    # sensor-fw#123 packet_drop evidence: the gap becomes
                    # visible when this (gap-closing) frame arrives.
                    batch.events.append(FrameGapFillAnomaly(
                        side=key[0], cam_id=key[1],
                        gap_start_fid=prev_fid + 1, gap_end_fid=abs_fid - 1,
                        n_filled=gap - 1, timestamp_s=ts,
                    ))
                    for fid in range(prev_fid + 1, abs_fid):
                        frac = (fid - prev_fid) / gap
                        fills.append((i, {
                            "cam_id": key[1],
                            "frame_id": fid & 0xFF,
                            "side_idx": key[0],
                            "abs_frame_id": fid,
                            "timestamp_s": prev_ts + frac * (ts - prev_ts),
                            "frame_type": "light",
                            "quality": "nan_filled",
                        }))
                        self._total_nan += 1
                        # Attribute the fill to this side's live window: the
                        # still-open one, else the most recent window closed
                        # earlier in this batch (held in _pending_closed until
                        # the end-of-batch flush precisely so this attribution
                        # can reach it — the old post-pass lookup credited
                        # fills to whichever window happened to be open after
                        # the pass, or dropped them from the count entirely).
                        w = self._open_window[key[0]]
                        if w is None and self._pending_closed[key[0]]:
                            w = self._pending_closed[key[0]][-1]
                        if w is not None:
                            w.n_nan += 1

            self._nan_last_seen[key] = (abs_fid, ts)

        fills.sort(key=lambda x: x[0])
        return fills

    def _insert_nan_fills(self, batch: FrameBatch,
                          fills: list[tuple[int, dict]]) -> FrameBatch:
        """Rebuild the batch with NaN-fill rows inserted at the right positions."""
        n_orig = len(batch.cam_ids)
        n_fills = len(fills)
        n_new = n_orig + n_fills

        new_cam = np.zeros(n_new, dtype=np.int8)
        new_fid = np.zeros(n_new, dtype=np.uint8)
        new_sid = np.zeros(n_new, dtype=np.int8)
        new_abs = np.zeros(n_new, dtype=np.int64)
        new_ts = np.zeros(n_new, dtype=np.float64)
        new_ft = np.empty(n_new, dtype="<U14")
        new_q = np.empty(n_new, dtype="<U14")
        new_hist = np.zeros((n_new, 2, 8, 1024), dtype=np.uint32)
        new_temp = np.zeros((n_new, 2, 8), dtype=np.float32)

        # Build insertion map: for each original index, which fills precede it
        fill_before: dict[int, list[dict]] = defaultdict(list)
        for insert_at, fd in fills:
            fill_before[insert_at].append(fd)

        out = 0
        for i in range(n_orig):
            for fd in fill_before.get(i, []):
                new_cam[out] = fd["cam_id"]
                new_fid[out] = fd["frame_id"]
                new_sid[out] = fd["side_idx"]
                new_abs[out] = fd["abs_frame_id"]
                new_ts[out] = fd["timestamp_s"]
                new_ft[out] = fd["frame_type"]
                new_q[out] = fd["quality"]
                out += 1
            new_cam[out] = batch.cam_ids[i]
            new_fid[out] = batch.frame_ids[i]
            new_sid[out] = batch.side_ids[i]
            new_abs[out] = batch.abs_frame_ids[i]
            new_ts[out] = batch.timestamp_s[i]
            new_ft[out] = str(batch.frame_type[i])
            new_q[out] = str(batch.quality[i])
            new_hist[out] = batch.raw_histograms[i]
            new_temp[out] = batch.temperature_c[i]
            out += 1

        new_batch = FrameBatch(
            cam_ids=new_cam, frame_ids=new_fid, side_ids=new_sid,
            raw_histograms=new_hist, temperature_c=new_temp,
            timestamp_s=new_ts, pdc=None, tcm=None, tcl=None,
        )
        new_batch.abs_frame_ids = new_abs
        new_batch.frame_type = new_ft
        new_batch.quality = new_q
        new_batch.events = batch.events
        return new_batch

    # ── Lifecycle ───────────────────────────────────────────────────────

    def on_scan_stop(self, batch: FrameBatch) -> None:
        """End-of-scan lifecycle hook.

        Windows still open at the final frame are either reclassified as the
        expected terminal stop-frame artifact (INFO, excluded from the
        misalignment record — the firmware's laser-off frame fires ~150 ms
        off-grid on every scan) or closed normally with their WARNING +
        diagnostics event. Then the one-line scan summary (real window count,
        frames re-timestamped, NaN-filled, % of scan affected) is emitted
        whenever any genuine correction or fill occurred."""
        for side in (0, 1):
            w = self._open_window[side]
            if w is None:
                continue
            if self._is_terminal_artifact(w):
                self._open_window[side] = None
                self._total_corrected -= w.n_corrected
                self._terminal_frames += w.n_corrected
                logger.info(
                    "Terminal stop frame re-timestamped on side=%d "
                    "(%d camera(s)) — expected firmware stop artifact "
                    "(laser-off frame fires ~150 ms off-grid); not counted "
                    "as misalignment", side, w.n_corrected,
                )
            else:
                self._track_window_close(side)
        # Scan stop bypasses the log throttle: the final windows (and any
        # suppressed-count remainder) must never end the scan silently.
        self._flush_frozen(batch.events)
        self._flush_pending_closes(batch.events, force_log=True)

        if self._total_corrected or self._total_nan:
            pct = (self._total_corrected + self._total_nan) / max(1, self._total_frames_seen) * 100
            logger.warning(
                "Scan summary: %d misalignment window(s), %d frames "
                "re-timestamped, %d frames NaN-filled (%.1f%% of scan affected)",
                len(self._scan_windows), self._total_corrected, self._total_nan, pct,
            )

    def reset(self) -> None:
        """Pipeline lifecycle hook — drop all per-scan state so the stage is
        ready for a fresh scan or replay."""
        self._reset_state()
