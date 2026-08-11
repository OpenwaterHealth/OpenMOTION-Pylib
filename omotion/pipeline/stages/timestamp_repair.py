"""Repair timestamp EMI without letting a fabricated value become an anchor.

A frame is suspect when its timestamp departs from its camera's genuine
frame-ID cadence or when one coalesced packet contains different absolute
IDs. Suspect runs are re-anchored, persistent coherent shifts are accepted,
and genuine ID gaps receive synthetic rows. Detector inputs are buffered with
each window so the expected terminal stop artifact produces no false alarm.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..batch import (
    FrameBatch,
    FrameGapFillAnomaly,
    TimestampMisalignmentWindow,
    TimestampRepairInputAnomaly,
)

logger = logging.getLogger("openmotion.sdk.pipeline.stages.timestamp_repair")

_INITIAL_NOMINAL_PERIOD_S = 0.025
_DEFAULT_TOLERANCE_S = 0.008
_TOLERANCE_EPS_S = 1e-9
_EMA_ALPHA = 0.01
# Consecutive flagged frames whose original timestamps agree with each
# other before the stage accepts them as a genuine timeline shift (200 ms
# at 40 Hz) rather than transient EMI corruption.
_RESYNC_CONSISTENT_FRAMES = 8


@dataclass
class _WindowStats:
    """One coalesced per-side repair window and its original evidence."""
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
    input_anomalies: list[TimestampRepairInputAnomaly] = field(
        default_factory=list,
    )


@dataclass(frozen=True, slots=True)
class _FramePoint:
    """One genuine frame as received, before any repair mutation."""
    raw_id: int
    abs_id: int
    timestamp_s: float


@dataclass(frozen=True, slots=True)
class _CadenceCheck:
    """The evidence used by timestamp-deviation detection."""
    anchor: _FramePoint
    frame_id_gap: int
    expected_timestamp_s: float
    signed_residual_s: float


@dataclass(frozen=True, slots=True)
class _GapPoint:
    raw_id: int
    abs_id: int
    input_timestamp_s: float
    timeline_timestamp_s: float


class TimestampRepairStage:
    """Stateful, per-scan repair described in the module docstring."""

    name = "timestamp_repair"

    def __init__(self, *, tolerance_s: float = _DEFAULT_TOLERANCE_S):
        """Set the maximum allowed timestamp-cadence residual in seconds."""
        self._tolerance = float(tolerance_s)
        self._reset_state()

    def _reset_state(self) -> None:
        """Clear anchors, divergence windows, and scan totals."""
        self._nominal_period = _INITIAL_NOMINAL_PERIOD_S
        # Last GENUINE good frame per (side, cam) — never a fabricated
        # corrected timestamp. Both condition 1 and re-anchor validation
        # measure against this, so a bad burst can't drag the reference
        # off the true timeline.
        self._last_good: dict[tuple[int, int], _FramePoint] = {}
        # Divergent-run tracking per (side, cam) for timeline re-sync:
        # the last flagged frame's ORIGINAL (fid, ts) and the count of
        # consecutive mutually-consistent flagged frames.
        self._run_last: dict[tuple[int, int], tuple[int, float]] = {}
        self._run_len: dict[tuple[int, int], int] = {}
        self._open_window: dict[int, Optional[_WindowStats]] = {0: None, 1: None}
        # Cameras currently divergent per side — the open window closes
        # only when this set empties (a healthy interleaved camera must
        # not close a window another camera still holds open).
        self._divergent: dict[int, set] = {0: set(), 1: set()}
        self._scan_windows: list[_WindowStats] = []
        self._total_frames_seen = 0
        self._nan_last_seen: dict[tuple[int, int], _GapPoint] = {}
        self._total_corrected = 0
        self._total_nan = 0
        self._terminal_frames = 0

    # ── Main entry ──────────────────────────────────────────────────────

    def process(self, batch: FrameBatch) -> FrameBatch:
        """Repair suspect timestamps and insert rows for absolute-ID gaps."""
        if batch.abs_frame_ids is None or batch.frame_type is None:
            return batch

        n = len(batch.cam_ids)
        if n == 0:
            batch.quality = np.empty(0, dtype="<U14")
            return batch

        self._total_frames_seen += n
        quality = np.full(n, "ok", dtype="<U14")
        input_timestamps = batch.timestamp_s.copy()

        packet_by_row, bad_cond2 = self._packet_context(batch)

        # Per-camera row pool for re-anchoring. Condition 1 is validated
        # per candidate at interpolation time (needs _last_good context).
        cam_rows = self._collect_cam_rows(batch, bad_cond2)

        # Single pass: detect condition 1 inline, correct, track windows
        for i in range(n):
            ftype = str(batch.frame_type[i])
            if ftype in ("warmup", "stale"):
                continue

            cam_id = int(batch.cam_ids[i])
            side_idx = int(batch.side_ids[i])
            raw_fid = int(batch.frame_ids[i])
            abs_fid = int(batch.abs_frame_ids[i])
            ts = float(batch.timestamp_s[i])
            key = (side_idx, cam_id)

            # Condition 1: timestamp deviation vs the genuine anchor
            cadence = self._cadence_check(key, abs_fid, ts)
            cond1 = (cadence is not None and
                     abs(cadence.signed_residual_s)
                     > self._tolerance + _TOLERANCE_EPS_S)
            detector = ("absolute_frame_id_disagreement"
                        if i in bad_cond2 else "timestamp_deviation")
            is_bad = i in bad_cond2 or cond1

            if is_bad and self._note_divergent_run(key, abs_fid, ts):
                # RESYNC: enough consecutive flagged frames agree with
                # each other — a genuine timeline shift (e.g. recovery
                # after a long dropout), not transient EMI. Adopt it.
                logger.warning(
                    "Timestamp timeline re-anchored: side=%d cam=%d — %d "
                    "consecutive flagged frames were mutually "
                    "cadence-consistent (genuine timeline shift, not "
                    "transient EMI); accepting device timestamps from "
                    "frame %d (t=%.3fs) onward",
                    side_idx, cam_id, _RESYNC_CONSISTENT_FRAMES,
                    abs_fid, ts,
                )
                is_bad = False

            anomaly = None
            if i in bad_cond2 or cond1:
                anomaly = self._make_input_anomaly(
                    side_idx, cam_id, raw_fid, abs_fid, ts,
                    cadence, packet_by_row[i], detector,
                    "timestamp_corrected" if is_bad else "accepted_as_new_timeline",
                )

            if is_bad:
                corrected_ts = self._interpolate(key, abs_fid, cam_rows.get(key))
                batch.timestamp_s[i] = corrected_ts
                quality[i] = "ts_corrected"
                self._total_corrected += 1
                self._track_window_open(
                    side_idx, key, abs_fid, ts, anomaly,
                )
                # Deliberately NOT stored in _last_good: a fabricated
                # timestamp must never become the reference anchor.
            else:
                window = self._open_window[side_idx]
                if anomaly is not None and window is not None:
                    window.input_anomalies.append(anomaly)
                self._run_last.pop(key, None)
                self._run_len.pop(key, None)
                self._update_nominal_period(key, abs_fid, ts)
                self._last_good[key] = _FramePoint(raw_fid, abs_fid, ts)
                self._note_good(side_idx, key, batch.events)

        batch.quality = quality

        # Insert NaN-fill rows for missing abs_frame_id gaps
        nan_fills = self._collect_nan_fills(batch, input_timestamps)
        if nan_fills:
            batch = self._insert_nan_fills(batch, nan_fills)

        return batch

    # ── Detection ───────────────────────────────────────────────────────

    def _packet_context(
        self, batch: FrameBatch,
    ) -> tuple[dict[int, tuple[tuple[int, int, int], ...]], set[int]]:
        """Return packet evidence and rows with disagreeing absolute IDs."""
        groups: dict[tuple[int, float], list[int]] = defaultdict(list)
        for i in range(len(batch.cam_ids)):
            groups[(int(batch.side_ids[i]), float(batch.timestamp_s[i]))].append(i)
        contexts = {}
        bad = set()
        for indices in groups.values():
            packet = tuple(
                (int(batch.cam_ids[i]), int(batch.frame_ids[i]),
                 int(batch.abs_frame_ids[i]))
                for i in indices
            )
            contexts.update((i, packet) for i in indices)
            active = [i for i in indices
                      if str(batch.frame_type[i]) not in ("warmup", "stale")]
            if len({int(batch.abs_frame_ids[i]) for i in active}) > 1:
                bad.update(active)
        return contexts, bad

    def _cadence_check(
        self, key: tuple[int, int], abs_fid: int, ts: float,
    ) -> Optional[_CadenceCheck]:
        """Return expected timing and residual against the genuine anchor."""
        anchor = self._last_good.get(key)
        if anchor is None:
            return None
        fid_gap = abs_fid - anchor.abs_id
        if fid_gap <= 0:
            return None
        expected_ts = anchor.timestamp_s + fid_gap * self._nominal_period
        return _CadenceCheck(
            anchor=anchor,
            frame_id_gap=fid_gap,
            expected_timestamp_s=expected_ts,
            signed_residual_s=ts - expected_ts,
        )

    def _deviates(self, key: tuple[int, int], abs_fid: int, ts: float) -> bool:
        check = self._cadence_check(key, abs_fid, ts)
        return (check is not None and
                abs(check.signed_residual_s)
                > self._tolerance + _TOLERANCE_EPS_S)

    def _make_input_anomaly(
        self,
        side: int,
        cam_id: int,
        raw_fid: int,
        abs_fid: int,
        timestamp_s: float,
        cadence: Optional[_CadenceCheck],
        packet: tuple[tuple[int, int, int], ...],
        detector: str,
        action: str,
    ) -> TimestampRepairInputAnomaly:
        """Capture the detector inputs before timestamp mutation."""
        anchor = cadence.anchor if cadence is not None else self._last_good.get((side, cam_id))
        return TimestampRepairInputAnomaly(
            side=side,
            cam_id=cam_id,
            raw_frame_id=raw_fid,
            abs_frame_id=abs_fid,
            original_timestamp_s=timestamp_s,
            previous_raw_frame_id=anchor.raw_id if anchor else None,
            previous_abs_frame_id=anchor.abs_id if anchor else None,
            previous_timestamp_s=anchor.timestamp_s if anchor else None,
            nominal_period_s=self._nominal_period,
            frame_id_gap=cadence.frame_id_gap if cadence else None,
            expected_timestamp_s=(cadence.expected_timestamp_s
                                  if cadence else None),
            signed_residual_s=(cadence.signed_residual_s
                               if cadence else None),
            tolerance_s=self._tolerance,
            packet=packet,
            detector=detector,
            action=action,
        )

    def _note_divergent_run(self, key: tuple[int, int],
                            abs_fid: int, ts: float) -> bool:
        """Track this camera's run of consecutive flagged frames using their
        ORIGINAL (fid, ts). Returns True when the run reaches
        ``_RESYNC_CONSISTENT_FRAMES`` mutually cadence-consistent frames —
        the caller then accepts the frame as a genuine timeline shift. A
        flagged frame inconsistent with its flagged predecessor (e.g. a
        stuck timestamp counter, Δt=0) restarts the run at 1."""
        prev = self._run_last.get(key)
        if prev is not None:
            prev_fid, prev_ts = prev
            fid_gap = abs_fid - prev_fid
            expected_dt = fid_gap * self._nominal_period
            consistent = (fid_gap > 0 and
                          abs((ts - prev_ts) - expected_dt)
                          <= self._tolerance + _TOLERANCE_EPS_S)
            if consistent:
                self._run_len[key] = self._run_len.get(key, 1) + 1
            else:
                self._run_len[key] = 1
        else:
            self._run_len[key] = 1
        self._run_last[key] = (abs_fid, ts)
        if self._run_len[key] >= _RESYNC_CONSISTENT_FRAMES:
            self._run_last.pop(key, None)
            self._run_len.pop(key, None)
            return True
        return False

    # ── Re-anchoring ────────────────────────────────────────────────────

    def _collect_cam_rows(self, batch: FrameBatch,
                          bad_cond2: set[int]) -> dict[tuple[int, int], list]:
        """Collect, per (side, cam), every non-warmup/stale row's
        (abs_fid, original ts, cond2-flagged) in batch order — the candidate
        right-anchor pool that ``_interpolate`` searches (with condition-1
        validation per candidate) when re-anchoring a bad frame."""
        rows: dict[tuple[int, int], list] = defaultdict(list)
        for i in range(len(batch.cam_ids)):
            ft = str(batch.frame_type[i])
            if ft in ("warmup", "stale"):
                continue
            key = (int(batch.side_ids[i]), int(batch.cam_ids[i]))
            rows[key].append((int(batch.abs_frame_ids[i]),
                              float(batch.timestamp_s[i]),
                              i in bad_cond2))
        return rows

    def _interpolate(self, key: tuple[int, int], abs_fid: int,
                     rows: list | None) -> float:
        """Re-anchor a bad frame's timestamp by interpolation.

        Interpolates between the camera's genuine left anchor and the next
        VALID right anchor in the batch — a later frame that passes
        condition 2 and is cadence-consistent with the left anchor
        (condition 1), so a still-corrupted frame mid-burst can never pull
        the correction off the true timeline. Falls back to the left anchor
        plus nominal-period steps when no valid right anchor exists in the
        batch, and to abs_fid × nominal period when there's no anchor at
        all (start of scan)."""
        anchor = self._last_good.get(key)
        if anchor is None:
            return abs_fid * self._nominal_period

        if rows:
            for right_fid, right_ts, c2bad in rows:
                if right_fid <= abs_fid or c2bad:
                    continue
                if self._deviates(key, right_fid, right_ts):
                    continue  # still-corrupted — not a valid re-anchor
                fid_span = right_fid - anchor.abs_id
                if fid_span > 0:
                    fraction = (abs_fid - anchor.abs_id) / fid_span
                    return (anchor.timestamp_s
                            + fraction * (right_ts - anchor.timestamp_s))

        # Fallback: nominal period from left anchor
        return (anchor.timestamp_s
                + (abs_fid - anchor.abs_id) * self._nominal_period)

    # ── Nominal period tracking ─────────────────────────────────────────

    def _update_nominal_period(self, key: tuple[int, int],
                               abs_fid: int, ts: float) -> None:
        """Refine the true frame period via an EMA over clean single-step
        intervals (only when this frame is exactly one frame_id past the
        last good frame for ``key``). Tracks the real ~25.02 ms cadence so
        the expected-Δt check in process() doesn't drift off nominal."""
        if key not in self._last_good:
            return
        previous = self._last_good[key]
        if abs_fid - previous.abs_id == 1:
            dt = ts - previous.timestamp_s
            if dt > 0:
                self._nominal_period = (
                    (1 - _EMA_ALPHA) * self._nominal_period + _EMA_ALPHA * dt
                )

    # ── Window tracking (coalesced logging) ─────────────────────────────

    def _track_window_open(self, side: int, key: tuple[int, int],
                           abs_fid: int, ts: float,
                           anomaly: Optional[TimestampRepairInputAnomaly]) -> None:
        """Open this side's misalignment window (or extend it), mark the
        camera divergent, and count the re-timestamped frame for coalesced
        per-window logging. ``ts`` is the original pre-correction device
        timestamp."""
        w = self._open_window[side]
        if w is None:
            w = _WindowStats(side=side, onset_fid=abs_fid, onset_t=ts)
            self._open_window[side] = w
        w.end_fid = abs_fid
        w.end_t = ts
        w.n_corrected += 1
        w.frames.append((key, abs_fid))
        if anomaly is not None:
            w.input_anomalies.append(anomaly)
        self._divergent[side].add(key)

    def _note_good(self, side: int, key: tuple[int, int], events: list) -> None:
        """A good frame from ``key``: its divergence (if any) has ended. The
        side's window closes only once NO camera on the side is still
        divergent — a healthy camera interleaved with a diverging one must
        not close (and re-open) the window on every row (spec R3)."""
        divergent = self._divergent[side]
        if key in divergent:
            divergent.discard(key)
        if self._open_window[side] is not None and not divergent:
            self._close_window(side, events)

    def _close_window(self, side: int, events: list) -> None:
        """Close this side's open misalignment window, if any: record it,
        emit one coalesced WARNING for the whole window (per spec R3 — never
        one line per frame), and append a TimestampMisalignmentWindow event
        so the diagnostics channel / scan-DB summary record it."""
        w = self._open_window[side]
        if w is None:
            return
        self._open_window[side] = None
        self._divergent[side].clear()
        self._scan_windows.append(w)
        logger.warning(
            "Misalignment window: side=%d frames %d-%d (t=%.2f-%.2fs), "
            "%d frames re-timestamped, %d frames NaN-filled",
            w.side, w.onset_fid, w.end_fid, w.onset_t, w.end_t,
            w.n_corrected, w.n_nan,
        )
        events.extend(w.input_anomalies)
        events.append(TimestampMisalignmentWindow(
            side=w.side, onset_fid=w.onset_fid, end_fid=w.end_fid,
            onset_t=w.onset_t, end_t=w.end_t,
            n_corrected=w.n_corrected, n_nan=w.n_nan,
        ))

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
            if last is None or fid != last.abs_id:
                return False  # frames followed it — not the terminal frame
        return True

    # ── NaN-fill for missing frames ─────────────────────────────────────

    def _collect_nan_fills(
        self, batch: FrameBatch, input_timestamps: np.ndarray,
    ) -> list[tuple[int, dict]]:
        """Find gaps in abs_frame_id per (side, cam) and build fill descriptors.

        Returns [(insert_after_idx, fill_dict), ...] sorted by insert position.
        """
        fills = []
        for i in range(len(batch.cam_ids)):
            ft = str(batch.frame_type[i])
            if ft in ("warmup", "stale"):
                continue
            key = (int(batch.side_ids[i]), int(batch.cam_ids[i]))
            raw_fid = int(batch.frame_ids[i])
            abs_fid = int(batch.abs_frame_ids[i])
            ts = float(batch.timestamp_s[i])
            input_ts = float(input_timestamps[i])

            if key in self._nan_last_seen:
                previous = self._nan_last_seen[key]
                gap = abs_fid - previous.abs_id
                if gap > 1:
                    batch.events.append(FrameGapFillAnomaly(
                        side=key[0],
                        cam_id=key[1],
                        previous_raw_frame_id=previous.raw_id,
                        previous_abs_frame_id=previous.abs_id,
                        previous_timestamp_s=previous.input_timestamp_s,
                        current_raw_frame_id=raw_fid,
                        current_abs_frame_id=abs_fid,
                        current_timestamp_s=input_ts,
                        missing_count=gap - 1,
                        first_missing_abs_frame_id=previous.abs_id + 1,
                        last_missing_abs_frame_id=abs_fid - 1,
                    ))
                    for fid in range(previous.abs_id + 1, abs_fid):
                        frac = (fid - previous.abs_id) / gap
                        fills.append((i, {
                            "cam_id": key[1],
                            "frame_id": fid & 0xFF,
                            "side_idx": key[0],
                            "abs_frame_id": fid,
                            "timestamp_s": (previous.timeline_timestamp_s
                                            + frac * (ts - previous.timeline_timestamp_s)),
                            "frame_type": "light",
                            "quality": "nan_filled",
                        }))
                        self._total_nan += 1
                        w = self._open_window[key[0]]
                        if w is not None:
                            w.n_nan += 1

            self._nan_last_seen[key] = _GapPoint(
                raw_fid, abs_fid, input_ts, ts,
            )

        fills.sort(key=lambda x: x[0])
        return fills

    def _insert_nan_fills(self, batch: FrameBatch,
                          fills: list[tuple[int, dict]]) -> FrameBatch:
        """Rebuild the batch with NaN-fill rows inserted at the right positions.

        Telemetry stamps (pdc/tcm/tcl) survive the rebuild when present:
        original rows keep their values, fill rows get the no-sample
        sentinels (NaN / 0, matching TelemetryIngestStage — spec §4.6)."""
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
        new_pdc = (None if batch.pdc is None
                   else np.full(n_new, np.nan, dtype=batch.pdc.dtype))
        new_tcm = (None if batch.tcm is None
                   else np.zeros(n_new, dtype=batch.tcm.dtype))
        new_tcl = (None if batch.tcl is None
                   else np.zeros(n_new, dtype=batch.tcl.dtype))

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
            if new_pdc is not None:
                new_pdc[out] = batch.pdc[i]
            if new_tcm is not None:
                new_tcm[out] = batch.tcm[i]
            if new_tcl is not None:
                new_tcl[out] = batch.tcl[i]
            out += 1

        new_batch = FrameBatch(
            cam_ids=new_cam, frame_ids=new_fid, side_ids=new_sid,
            raw_histograms=new_hist, temperature_c=new_temp,
            timestamp_s=new_ts, pdc=new_pdc, tcm=new_tcm, tcl=new_tcl,
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
                self._divergent[side].clear()
                self._total_corrected -= w.n_corrected
                self._terminal_frames += w.n_corrected
                logger.info(
                    "Terminal stop frame re-timestamped on side=%d "
                    "(%d camera(s)) — expected firmware stop artifact "
                    "(laser-off frame fires ~150 ms off-grid); not counted "
                    "as misalignment", side, w.n_corrected,
                )
            else:
                self._close_window(side, batch.events)

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
