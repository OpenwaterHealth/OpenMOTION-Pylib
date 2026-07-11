"""DarkCorrectionStage bypass mode (issue #134) — engineering bench sources
that never turn off. Scheduled laser-skip frames are NOT dark, so bypass
replaces dark subtraction with a static pedestal baseline, silences the
integrity guard, and closes the terminal interval unconditionally.
Default pedestals are 64.0/64.0 (SensorPedestals default in the stage).
"""

import logging

import numpy as np
import pytest
from omotion.pipeline.batch import (
    DarkIntegrityWarning, FrameBatch, IntervalClosed, TerminalDarkResult,
)
from omotion.pipeline.stages.dark import (
    DarkCorrectionStage, HybridRealtimePredictor, LinearInterpolation,
)


def _batch(n_frames, frame_types, abs_ids, *, mean_raw, std_raw):
    """Minimal FrameBatch — only side=0 cam=0 populated (mirrors
    test_dark_correction_stage._batch)."""
    n = n_frames
    raw_hist = np.zeros((n, 2, 8, 1024), dtype=np.uint32)
    if n:
        raw_hist[:, 0, 0, 0] = 1
    return FrameBatch(
        cam_ids=np.zeros(n, dtype=np.int8),
        frame_ids=np.arange(n, dtype=np.uint8),
        side_ids=np.zeros(n, dtype=np.int8),
        raw_histograms=raw_hist,
        temperature_c=np.zeros((n, 2, 8), dtype=np.float32),
        timestamp_s=np.arange(n, dtype=np.float64) * 0.025,
        pdc=None, tcm=None, tcl=None,
        abs_frame_ids=np.array(abs_ids, dtype=np.int64),
        frame_type=np.array(frame_types, dtype="<U8"),
        mean_raw=mean_raw, std_raw=std_raw,
    )


def _stage(bypass):
    return DarkCorrectionStage(
        realtime_estimator=HybridRealtimePredictor(),
        batch_estimator=LinearInterpolation(),
        bypass=bypass,
    )


def _uniform(n, mean, std):
    return (np.full((n, 2, 8), mean, dtype=np.float32),
            np.full((n, 2, 8), std, dtype=np.float32))


def test_bypass_realtime_emits_from_first_light_frame():
    """No warmup window: baseline is the static pedestal (64), so the very
    first light frame emits mean_dc_rt = u1 - 64 and std_dc_rt = raw std."""
    mean, std = _uniform(3, 500.0, 10.0)
    batch = _batch(3, ["light"] * 3, [11, 12, 13], mean_raw=mean, std_raw=std)
    _stage(bypass=True).process(batch)
    assert batch.mean_dc_rt[0, 0, 0] == pytest.approx(436.0)
    assert batch.std_dc_rt[0, 0, 0] == pytest.approx(10.0)
    assert batch.dark_baseline_rt[0, 0, 0] == pytest.approx(64.0)


def test_bypass_no_integrity_warning_on_lit_dark_frames(caplog):
    """A lit scheduled dark frame (u1=500 >> pedestal+5) raises no
    DarkIntegrityWarning event and no 'brighter than expected' log."""
    mean, std = _uniform(3, 500.0, 10.0)
    batch = _batch(3, ["dark", "light", "dark"], [10, 11, 12],
                   mean_raw=mean, std_raw=std)
    with caplog.at_level(logging.WARNING,
                         logger="openmotion.sdk.pipeline.stages.dark"):
        _stage(bypass=True).process(batch)
    assert [e for e in batch.events
            if isinstance(e, DarkIntegrityWarning)] == []
    assert "brighter than expected" not in caplog.text


def test_bypass_interval_baseline_is_pedestal():
    """Interval closes on the positional darks, but correction uses the
    synthetic zero-dark boundary: mean = u1 - pedestal, std = raw std."""
    n = 4
    mean = np.array([500.0, 500.0, 510.0, 500.0], dtype=np.float32) \
        .reshape(4, 1, 1) * np.ones((1, 2, 8), dtype=np.float32)
    std = np.array([10.0, 20.0, 22.0, 10.0], dtype=np.float32) \
        .reshape(4, 1, 1) * np.ones((1, 2, 8), dtype=np.float32)
    batch = _batch(n, ["dark", "light", "light", "dark"], [10, 11, 12, 14],
                   mean_raw=mean.astype(np.float32),
                   std_raw=std.astype(np.float32))
    _stage(bypass=True).process(batch)

    closed = [e for e in batch.events if isinstance(e, IntervalClosed)]
    assert len(closed) == 1
    frames = {f.abs_frame_id: f for f in closed[0].corrected_batch.frames}
    assert frames[11].mean == pytest.approx(500.0 - 64.0)
    assert frames[11].std == pytest.approx(20.0)
    assert frames[12].mean == pytest.approx(510.0 - 64.0)
    assert frames[12].std == pytest.approx(22.0)


def test_bypass_dark_like_light_suppression_still_active():
    """A genuinely unlit light frame (covered sensor) still suppresses
    realtime emission and flags low_light_rt, exactly as in normal mode."""
    mean = np.array([500.0, 66.0], dtype=np.float32) \
        .reshape(2, 1, 1) * np.ones((1, 2, 8), dtype=np.float32)
    std = np.array([20.0, 11.0], dtype=np.float32) \
        .reshape(2, 1, 1) * np.ones((1, 2, 8), dtype=np.float32)
    batch = _batch(2, ["light", "light"], [11, 12],
                   mean_raw=mean.astype(np.float32),
                   std_raw=std.astype(np.float32))
    _stage(bypass=True).process(batch)
    assert batch.mean_dc_rt[0, 0, 0] == pytest.approx(436.0)
    assert np.isnan(batch.mean_dc_rt[1, 0, 0])
    assert batch.low_light_rt[1, 0, 0]


def test_bypass_terminal_flush_closes_without_errors(caplog):
    """Scan stop with a fully lit tail: the last buffered frame becomes the
    terminal boundary (synthetic obs), the interval closes, and there are no
    TERMINAL DARK errors. TerminalDarkResult reports found=True/bypass."""
    mean, std = _uniform(3, 500.0, 20.0)
    stage = _stage(bypass=True)
    stage.process(_batch(3, ["dark", "light", "light"], [10, 11, 12],
                         mean_raw=mean, std_raw=std))

    stop_batch = _batch(0, [], [],
                        mean_raw=np.zeros((0, 2, 8), dtype=np.float32),
                        std_raw=np.zeros((0, 2, 8), dtype=np.float32))
    with caplog.at_level(logging.ERROR,
                         logger="openmotion.sdk.pipeline.stages.dark"):
        stage.on_scan_stop(stop_batch)

    assert "TERMINAL DARK" not in caplog.text
    closed = [e for e in stop_batch.events if isinstance(e, IntervalClosed)]
    assert len(closed) == 1
    # Interval [10, 12] with light 11: mean = 500 - 64.
    frames = {f.abs_frame_id: f for f in closed[0].corrected_batch.frames}
    assert frames[11].mean == pytest.approx(436.0)
    tdr = [e for e in stop_batch.events if isinstance(e, TerminalDarkResult)]
    assert len(tdr) == 1
    assert tdr[0].found is True
    assert tdr[0].identified_by == "bypass"


def test_default_stage_is_not_bypassed():
    """bypass defaults False; normal-mode behavior is covered by the
    existing test_dark_correction_stage.py suite."""
    stage = DarkCorrectionStage(
        realtime_estimator=HybridRealtimePredictor(),
        batch_estimator=LinearInterpolation(),
    )
    assert stage._bypass is False
