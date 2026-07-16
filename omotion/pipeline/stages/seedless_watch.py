"""SEEDLESS transition watch (SDK issue #146).

Sits directly after FrameClassificationStage in the pipeline. When the
unwrapped absolute frame id reaches the end of the seedless region it fires
the supplied callback exactly once — ScanWorkflow wires this to
SeedlessController.schedule_restore(), which does its register writes on its
own thread, so the callback returns immediately and never blocks the runner.

Detection latency: batches flush every 10 frames / 0.25 s, so the callback
fires up to ~12 frames after frame N. That lag lands inside the 80-frame
seedless_tx guard band (see classify.SEEDLESS_TX_GUARD_FRAMES).
"""

from __future__ import annotations

import logging
from typing import Callable

from ..batch import FrameBatch

logger = logging.getLogger("openmotion.sdk.pipeline.stages.seedless_watch")


class SeedlessWatchStage:
    name = "seedless_watch"

    def __init__(self, *, n_frames: int, callback: Callable[[], None]):
        self.n_frames = int(n_frames)
        self._callback = callback
        self._fired = False

    def process(self, batch: FrameBatch) -> FrameBatch:
        if not self._fired and batch.abs_frame_ids is not None \
                and (batch.abs_frame_ids >= self.n_frames).any():
            self._fired = True
            logger.info(
                "SeedlessWatchStage: frame %d reached (max abs id %d) — "
                "firing restore callback",
                self.n_frames, int(batch.abs_frame_ids.max()),
            )
            try:
                self._callback()
            except Exception:
                logger.exception("seedless transition callback raised")
        return batch

    def reset(self) -> None:
        """Re-arm the one-shot latch for a reused pipeline (scan-start /
        replay-reuse), so the restore callback can fire again next scan."""
        self._fired = False
