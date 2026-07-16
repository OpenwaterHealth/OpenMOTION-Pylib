"""SeedlessWatchStage — fires the transition callback once at frame N."""

import numpy as np
from omotion.pipeline.batch import FrameBatch
from omotion.pipeline.stages.seedless_watch import SeedlessWatchStage


def _batch_with_abs_ids(abs_ids):
    n = len(abs_ids)
    batch = FrameBatch(
        cam_ids=np.zeros(n, dtype=np.int8),
        frame_ids=np.zeros(n, dtype=np.uint8),
        side_ids=np.zeros(n, dtype=np.int8),
        raw_histograms=np.zeros((n, 2, 8, 1024), dtype=np.uint32),
        temperature_c=np.zeros((n, 2, 8), dtype=np.float32),
        timestamp_s=np.arange(n, dtype=np.float64),
        pdc=None, tcm=None, tcl=None,
    )
    batch.abs_frame_ids = np.array(abs_ids, dtype=np.int64)
    return batch


def test_no_fire_below_threshold():
    fired = []
    stage = SeedlessWatchStage(n_frames=10, callback=lambda: fired.append(1))
    stage.process(_batch_with_abs_ids([1, 2, 3]))
    assert fired == []


def test_fires_once_at_threshold_and_never_again():
    fired = []
    stage = SeedlessWatchStage(n_frames=10, callback=lambda: fired.append(1))
    stage.process(_batch_with_abs_ids([8, 9, 10]))
    stage.process(_batch_with_abs_ids([11, 12]))
    assert fired == [1]


def test_passes_batch_through_unmodified():
    stage = SeedlessWatchStage(n_frames=10, callback=lambda: None)
    batch = _batch_with_abs_ids([1, 2])
    assert stage.process(batch) is batch


def test_callback_exception_does_not_break_pipeline():
    def boom():
        raise RuntimeError("restore failed")
    stage = SeedlessWatchStage(n_frames=1, callback=boom)
    batch = _batch_with_abs_ids([1, 2])
    assert stage.process(batch) is batch   # must not raise


def test_reset_rearms_the_latch():
    fired = []
    stage = SeedlessWatchStage(n_frames=5, callback=lambda: fired.append(1))
    stage.process(_batch_with_abs_ids([5, 6]))
    assert fired == [1]
    stage.reset()
    stage.process(_batch_with_abs_ids([5, 6]))
    assert fired == [1, 1]   # fires again after reset
