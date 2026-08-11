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
from collections import Counter, defaultdict

import numpy as np

from ..batch import (
    FrameBatch,
    FrameIdConsensusCorrection,
    FrameIdPacketAnomaly,
)


logger = logging.getLogger("openmotion.sdk.pipeline.stages.frame_classification")

_FRAME_ID_MODULUS = 256
_FRAME_ROLLOVER_THRESHOLD = 128


class _FrameUnwrapper:
    """8-bit rolling → monotonic. One instance per (side, cam_id).

    Robust against a non-monotonic frame stream. The sensor occasionally
    emits stale/garbage frames — leftover buffer contents at scan start
    (e.g. raw 1, 255, 173, 4, 5 …) or a mid-scan counter blip — when its
    histogram DMA buffer isn't flushed. The old unwrapper trusted every
    frame after the first, so a backward-looking value (255 after 1) was
    read as a huge forward jump and the next real frame (4) tripped the
    rollover test, injecting a permanent +256 epoch offset that shifted the
    whole positional dark/warmup schedule for the rest of the scan.

    Now each frame is gated by its *signed* 8-bit step from the last
    accepted frame: only forward steps (1..127) advance state; a backward
    or duplicate step (<= 0) is rejected as stale and does NOT advance the
    counter, so isolated garbage frames can't corrupt the epoch. The 8-bit
    counter can't disambiguate a genuine forward gap > 127 frames (a >3.2 s
    intra-scan dropout) from a backward step; such ambiguous frames are
    rejected — that data is already lost in a gap that large.
    """

    __slots__ = ("epoch", "last_raw", "seen_first", "first_was_stale")

    def __init__(self):
        self.epoch = 0
        self.last_raw = -1
        self.seen_first = False
        self.first_was_stale = False

    def unwrap(self, raw_frame_id: int) -> tuple[int, bool]:
        """Return (abs_frame_id, accepted).

        accepted=False marks a stale/non-monotonic frame: the abs_id is
        advisory only and the unwrapper state is left untouched so the
        next genuine frame resumes the sequence cleanly.
        """
        if not self.seen_first:
            self.seen_first = True
            self.first_was_stale = (raw_frame_id != 1)
            self.last_raw = raw_frame_id
            return raw_frame_id, True

        abs_id, accepted = self.preview(raw_frame_id)
        if not accepted:
            return abs_id, False
        if raw_frame_id <= self.last_raw:
            self.epoch += 1
        self.last_raw = raw_frame_id
        return abs_id, True

    def preview(self, raw_frame_id: int) -> tuple[int, bool]:
        """Return the unwrap result without advancing counter state."""
        if not self.seen_first:
            return raw_frame_id, True
        step = ((raw_frame_id - self.last_raw + 128) & 0xFF) - 128
        if step <= 0:
            return self.epoch * _FRAME_ID_MODULUS + raw_frame_id, False
        epoch = self.epoch + int(raw_frame_id <= self.last_raw)
        return epoch * _FRAME_ID_MODULUS + raw_frame_id, True

    @property
    def last_abs(self) -> int:
        return self.epoch * _FRAME_ID_MODULUS + self.last_raw


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
        effective_ids = self._packet_consensus_ids(batch)

        for i in range(n):
            cam_id = int(batch.cam_ids[i])
            raw_id = int(batch.frame_ids[i])
            effective_id = int(effective_ids[i])
            # Side is authoritatively set by the source (see FrameBatch.side_ids
            # docstring). Inferring from raw_histograms would misclassify any
            # zero-filled row — e.g. a firmware-dropped frame — as side 0.
            side_idx = int(batch.side_ids[i])

            key = (side_idx, cam_id)
            unwrapper = self._unwrappers.get(key)
            if unwrapper is None:
                unwrapper = _FrameUnwrapper()
                self._unwrappers[key] = unwrapper

            abs_id, accepted = unwrapper.unwrap(effective_id)
            abs_ids[i] = abs_id

            if not accepted:
                # Non-monotonic / stale leftover frame (e.g. unflushed
                # histogram buffer at scan start or a mid-scan counter blip).
                # Excluded downstream so it can't poison dark/timestamp align.
                types[i] = "stale"
                self._note_stale(side_idx, cam_id, raw_id,
                                 "non-monotonic frame id (backward/duplicate)")
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

    def _packet_consensus_ids(self, batch: FrameBatch) -> np.ndarray:
        """Return effective IDs for unwrapping while preserving wire values.

        Only a single outlier in a packet of at least three unique cameras is
        corrected, and only when the consensus is a valid forward continuation
        for that camera's existing unwrapper.
        """
        effective = batch.frame_ids.copy()
        groups: dict[tuple[int, float], list[int]] = defaultdict(list)
        for i in range(len(batch.cam_ids)):
            groups[(int(batch.side_ids[i]), float(batch.timestamp_s[i]))].append(i)

        for (side, timestamp_s), indices in groups.items():
            raw_ids = [int(batch.frame_ids[i]) for i in indices]
            if len(set(raw_ids)) == 1:
                continue

            packet = tuple(
                (int(batch.cam_ids[i]), int(batch.frame_ids[i]))
                for i in indices
            )
            camera_ids = [cam_id for cam_id, _ in packet]
            reason = "no_single_outlier_consensus"
            outlier_i = None
            consensus = None

            if len(set(camera_ids)) != len(camera_ids):
                reason = "duplicate_camera_ids"
            elif len(indices) < 3:
                reason = "fewer_than_three_cameras"
            else:
                counts = Counter(raw_ids)
                candidate, count = counts.most_common(1)[0]
                outliers = [i for i in indices
                            if int(batch.frame_ids[i]) != candidate]
                if count == len(indices) - 1 and len(outliers) == 1:
                    consensus = candidate
                    outlier_i = outliers[0]
                    cam_id = int(batch.cam_ids[outlier_i])
                    unwrapper = self._unwrappers.get((side, cam_id))
                    if unwrapper is None or not unwrapper.seen_first:
                        reason = "outlier_has_no_prior_state"
                    else:
                        corrected_abs, accepted = unwrapper.preview(consensus)
                        if accepted:
                            batch.events.append(FrameIdConsensusCorrection(
                                side=side,
                                timestamp_s=timestamp_s,
                                cam_id=cam_id,
                                observed_raw_frame_id=int(batch.frame_ids[outlier_i]),
                                consensus_raw_frame_id=consensus,
                                previous_raw_frame_id=unwrapper.last_raw,
                                previous_abs_frame_id=unwrapper.last_abs,
                                corrected_abs_frame_id=corrected_abs,
                                packet=packet,
                            ))
                            effective[outlier_i] = consensus
                            continue
                        reason = "consensus_is_not_forward_continuation"

            cam_id = (int(batch.cam_ids[outlier_i])
                      if outlier_i is not None else None)
            unwrapper = (self._unwrappers.get((side, cam_id))
                         if cam_id is not None else None)
            batch.events.append(FrameIdPacketAnomaly(
                side=side,
                timestamp_s=timestamp_s,
                reason=reason,
                packet=packet,
                cam_id=cam_id,
                observed_raw_frame_id=(int(batch.frame_ids[outlier_i])
                                       if outlier_i is not None else None),
                consensus_raw_frame_id=consensus,
                previous_raw_frame_id=(unwrapper.last_raw
                                       if unwrapper is not None else None),
                previous_abs_frame_id=(unwrapper.last_abs
                                       if unwrapper is not None else None),
            ))
        return effective

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
