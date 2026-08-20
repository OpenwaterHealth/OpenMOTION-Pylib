"""FrameClassificationStage — abs_frame_id unwrap + frame_type labeling.

Per (side, cam_id) pair, the stage maintains a FrameUnwrapper (8-bit →
monotonic absolute index) and a "first frame seen" guard. Each row is
labeled with one of: "warmup", "dark", "light", "stale".

Dark frames are determined strictly by position — matching the firmware's
LaserPulseSkipInterval schedule. Content-based detection is not used;
the terminal dark frame (firmware laser-off at scan stop) is handled
separately by DarkCorrectionStage.on_scan_stop.

See docs/SciencePipeline.md §3 (unwrapping) and §4 (classification).
"""

from __future__ import annotations

import logging

import numpy as np

from ..batch import FrameBatch


logger = logging.getLogger("openmotion.sdk.pipeline.stages.frame_classification")

_FRAME_ID_MODULUS = 256
_FRAME_ROLLOVER_THRESHOLD = 128


_NOMINAL_PERIOD_S = 0.025          # 40 fps capture cadence
# A forward frame_id step of K frames claims K x 25 ms of elapsed time.
# When the claim exceeds the capture timestamps' account by more than this
# slack, the frame_id is lying (EFT-corrupted byte, sdk#220) and the frame
# is quarantined. Slack = 1.5 periods absolute (FSIN jitter, timestamp
# rounding) or 10% of the claim (period drift on long genuine dropouts),
# whichever is larger.
_STEP_SLACK_ABS_S = 1.5 * _NOMINAL_PERIOD_S
_STEP_SLACK_FRAC = 0.10


class _FrameUnwrapper:
    """8-bit rolling → monotonic. One instance per (side, cam_id).

    The frame counter and the capture timestamp are two witnesses to the
    same event, and either can be corrupted in flight (EFT testing corrupts
    bytes on the FPGA→MCU link — sdk#220 hit the frame_id byte). This class
    only ever advances its state on frames whose counter step is CONSISTENT
    with the clock; everything else is rejected without side effects, so a
    single corrupted byte costs exactly one frame instead of poisoning the
    sequence.

    Acceptance rules, in order:

    1. **Backward or duplicate step (<= 0)** — rejected as stale. Covers
       leftover buffer contents at scan start (raw 1, 255, 173, 4, 5 …),
       mid-scan counter blips, and corrupted frame_ids that happen to read
       backward. State is untouched so the next genuine frame resumes the
       sequence cleanly.
    2. **Forward step that over-claims time (sdk#220)** — a step of K
       frames must be backed by ~K x 25 ms of elapsed capture timestamp.
       A corrupted frame_id reading "+64 frames" while the clock says one
       frame passed is quarantined, NOT believed: accepting it would bump
       the epoch early and then reject the next ~64 REAL frames as
       backward (the "etch-a-sketch" failure). Under-claiming is fine —
       frames that arrive late (the firmware's ~150 ms off-grid terminal
       stop frame, stalls) keep an honest counter, and timestamp anomalies
       are TimestampRepairStage's job, not this one's.
    3. **Forward step 1..127 consistent with the clock** — accepted; a
       numeric wrap (raw <= last_raw) increments the epoch.

    A genuine forward gap > 127 frames (a >3.2 s intra-scan dropout) is
    indistinguishable from a backward step in 8 bits and is rejected —
    that data is already lost in a gap that large.
    """

    __slots__ = ("epoch", "last_raw", "last_ts", "seen_first",
                 "first_was_stale")

    def __init__(self):
        self.epoch = 0
        self.last_raw = -1
        self.last_ts: float | None = None
        self.seen_first = False
        self.first_was_stale = False

    def unwrap(self, raw_frame_id: int,
               timestamp_s: float) -> tuple[int, bool, str | None]:
        """Return (abs_frame_id, accepted, reject_reason).

        accepted=False marks a stale/quarantined frame: the abs_id is
        advisory only and the unwrapper state is left untouched so the
        next genuine frame resumes the sequence cleanly. reject_reason is
        None when accepted, else a short human-readable cause.
        """
        if not self.seen_first:
            self.seen_first = True
            self.first_was_stale = (raw_frame_id != 1)
            self.last_raw = raw_frame_id
            self.last_ts = float(timestamp_s)
            return raw_frame_id, True, None

        # Signed step in [-128, 127]: positive = forward, <= 0 = backward
        # (stale leftover) or duplicate.
        step = ((raw_frame_id - self.last_raw + 128) & 0xFF) - 128
        if step <= 0:
            return (self.epoch * _FRAME_ID_MODULUS + raw_frame_id, False,
                    "non-monotonic frame id (backward/duplicate)")

        # Forward step (1..127): cross-check the claim against the clock.
        if self.last_ts is not None:
            claimed_s = step * _NOMINAL_PERIOD_S
            elapsed_s = float(timestamp_s) - self.last_ts
            slack_s = max(_STEP_SLACK_ABS_S, _STEP_SLACK_FRAC * claimed_s)
            if claimed_s - elapsed_s > slack_s:
                return (self.epoch * _FRAME_ID_MODULUS + raw_frame_id, False,
                        f"frame id claims +{step} frames in "
                        f"{elapsed_s * 1e3:.0f} ms — corrupt frame_id "
                        "quarantined")

        # A wrap shows up as raw <= last_raw.
        if raw_frame_id <= self.last_raw:
            self.epoch += 1
        self.last_raw = raw_frame_id
        self.last_ts = float(timestamp_s)
        return self.epoch * _FRAME_ID_MODULUS + raw_frame_id, True, None


class FrameClassificationStage:
    name = "frame_classification"

    def __init__(self, discard_count: int = 9, dark_interval: int = 600):
        self.discard_count = int(discard_count)
        self.dark_interval = int(dark_interval)
        self._unwrappers: dict[tuple[int, int], _FrameUnwrapper] = {}
        # Per-(side, cam) count of stale/non-monotonic frames dropped this
        # scan. A non-zero count is a hardware-health signal — the sensor
        # shipped a leftover/garbage frame (e.g. unflushed histogram buffer)
        # that we excluded. First occurrence per camera is logged live; the
        # totals are summarised at on_scan_stop.
        self._stale_counts: dict[tuple[int, int], int] = {}
        self._stale_logged: set[tuple[int, int]] = set()

    def process(self, batch: FrameBatch) -> FrameBatch:
        n = batch.frame_ids.shape[0]
        abs_ids = np.zeros(n, dtype=np.int64)
        types = np.empty(n, dtype="<U8")

        for i in range(n):
            cam_id = int(batch.cam_ids[i])
            raw_id = int(batch.frame_ids[i])
            # Side is authoritatively set by the source (see FrameBatch.side_ids
            # docstring). Inferring from raw_histograms would misclassify any
            # zero-filled row — e.g. a firmware-dropped frame — as side 0.
            side_idx = int(batch.side_ids[i])

            key = (side_idx, cam_id)
            unwrapper = self._unwrappers.get(key)
            if unwrapper is None:
                unwrapper = _FrameUnwrapper()
                self._unwrappers[key] = unwrapper

            abs_id, accepted, reject_reason = unwrapper.unwrap(
                raw_id, float(batch.timestamp_s[i]))
            abs_ids[i] = abs_id

            if not accepted:
                # Stale leftover frame (unflushed histogram buffer at scan
                # start, a mid-scan counter blip) or a quarantined corrupt
                # frame_id (sdk#220). Excluded downstream either way so it
                # can't poison the dark/timestamp alignment.
                types[i] = "stale"
                self._note_stale(side_idx, cam_id, raw_id, reject_reason)
            elif unwrapper.first_was_stale and abs_id == raw_id:
                types[i] = "stale"
                self._note_stale(side_idx, cam_id, raw_id,
                                 "leading stale frame (stream did not start at 1)")
            elif abs_id <= self.discard_count:
                types[i] = "warmup"
            elif self._is_dark(abs_id):
                types[i] = "dark"
            else:
                types[i] = "light"

        batch.abs_frame_ids = abs_ids
        batch.frame_type = types
        return batch

    def _note_stale(self, side_idx: int, cam_id: int, raw_id: int,
                    reason: str) -> None:
        """Count a dropped stale frame and log the first one per camera."""
        key = (side_idx, cam_id)
        self._stale_counts[key] = self._stale_counts.get(key, 0) + 1
        if key not in self._stale_logged:
            self._stale_logged.add(key)
            logger.warning(
                "dropping stale frame: side=%d cam=%d raw_frame_id=%d — %s; "
                "excluded so it can't desync the dark/timestamp schedule. "
                "Likely an unflushed sensor histogram buffer "
                "(leftover/duplicate frame); per-camera totals at scan stop.",
                side_idx, cam_id, raw_id, reason,
            )

    def on_scan_stop(self, batch: FrameBatch) -> None:
        """End-of-scan summary of stale/non-monotonic frames dropped.

        A non-zero count means the sensor shipped leftover/garbage frames
        (e.g. an unflushed histogram buffer) that were excluded to protect
        the dark/timestamp alignment — a hardware-health signal worth
        surfacing even when the per-scan damage was contained."""
        total = sum(self._stale_counts.values())
        if total:
            per_cam = {f"s{s}c{c}": n
                       for (s, c), n in sorted(self._stale_counts.items())}
            logger.warning(
                "Scan summary: dropped %d stale/non-monotonic frame(s) across "
                "%d camera(s): %s", total, len(self._stale_counts), per_cam,
            )

    def _is_dark(self, abs_id: int) -> bool:
        """Per SciencePipeline.md §4.2:
            n == discard_count + 1 OR (n > discard_count + 1 AND (n-1) mod dark_interval == 0)
        """
        if abs_id == self.discard_count + 1:
            return True
        if abs_id <= self.discard_count + 1:
            return False
        return (abs_id - 1) % self.dark_interval == 0

    def reset(self) -> None:
        self._unwrappers.clear()
        self._stale_counts.clear()
        self._stale_logged.clear()
