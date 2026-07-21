"""BfiBviStage — affine calibration map from (contrast, mean) to (BFI, BVI)."""

import numpy as np
from dataclasses import dataclass
from omotion.pipeline.batch import FrameBatch, IntervalClosed
from omotion.pipeline.stages.bfi_bvi import BfiBviStage
from omotion.pipeline.stages.dark import CorrectedFrame, CorrectedInterval


@dataclass
class _Calibration:
    c_min: np.ndarray
    c_max: np.ndarray
    i_min: np.ndarray
    i_max: np.ndarray


def _trivial_calibration():
    return _Calibration(
        c_min=np.zeros((2, 8), dtype=np.float32),
        c_max=np.ones((2, 8), dtype=np.float32),
        i_min=np.zeros((2, 8), dtype=np.float32),
        i_max=np.full((2, 8), 100.0, dtype=np.float32),
    )


def _batch_with_live_values(mean_dc, contrast_sn):
    n = mean_dc.shape[0]
    return FrameBatch(
        cam_ids=np.zeros(n, dtype=np.int8),
        frame_ids=np.arange(n, dtype=np.uint8),
        raw_histograms=np.zeros((n, 2, 8, 1024), dtype=np.uint32),
        temperature_c=np.zeros((n, 2, 8), dtype=np.float32),
        timestamp_s=np.zeros(n, dtype=np.float64),
        pdc=None, tcm=None, tcl=None,
        mean_dc_rt=mean_dc,
        contrast_sn_rt=contrast_sn,
    )


def test_calibration_maps_full_range_to_zero_to_ten():
    """K == C_max → BFI = 0 (the calibrated minimum). No sanity filter:
    the affine map's full output range is preserved verbatim."""
    mean = np.full((1, 2, 8), 50.0, dtype=np.float32)
    contrast = np.full((1, 2, 8), 1.0, dtype=np.float32)
    batch = _batch_with_live_values(mean, contrast)
    BfiBviStage(calibration=_trivial_calibration()).process(batch)
    np.testing.assert_allclose(batch.bfi_live, 0.0, atol=1e-5)


def test_midpoint_contrast_maps_to_bfi_5():
    mean = np.full((1, 2, 8), 50.0, dtype=np.float32)
    contrast = np.full((1, 2, 8), 0.5, dtype=np.float32)
    batch = _batch_with_live_values(mean, contrast)
    BfiBviStage(calibration=_trivial_calibration()).process(batch)
    np.testing.assert_allclose(batch.bfi_live, 5.0, atol=1e-5)


def test_bvi_uses_mean_with_i_min_i_max():
    mean = np.full((1, 2, 8), 50.0, dtype=np.float32)
    contrast = np.full((1, 2, 8), 0.5, dtype=np.float32)
    batch = _batch_with_live_values(mean, contrast)
    BfiBviStage(calibration=_trivial_calibration()).process(batch)
    np.testing.assert_allclose(batch.bvi_live, 5.0, atol=1e-5)


def test_calibration_extremes_pass_through_unfiltered():
    """No sanity clamp: K == C_min → BFI = 10.0 (calibrated max) passes
    through verbatim, as do out-of-calibration values. The terminal
    dark-frame spike is handled on the live datapath (repeat the last
    light frame in place of a dark frame), NOT by dropping data here."""
    cal = _trivial_calibration()
    mean = np.full((1, 2, 8), 50.0, dtype=np.float32)
    # K == C_min (0) → BFI = 10.0 exactly
    batch = _batch_with_live_values(mean, np.zeros((1, 2, 8), dtype=np.float32))
    BfiBviStage(calibration=cal).process(batch)
    np.testing.assert_allclose(batch.bfi_live, 10.0, atol=1e-5)
    # K above C_max → BFI goes negative, still preserved (no clamp)
    batch2 = _batch_with_live_values(mean, np.full((1, 2, 8), 2.0, dtype=np.float32))
    BfiBviStage(calibration=cal).process(batch2)
    np.testing.assert_allclose(batch2.bfi_live, -10.0, atol=1e-5)


# ── Batch path: invalid contrast must stay invalid (issue #114) ──────────────


def _batch_with_corrected_frames(frames):
    b = FrameBatch(
        cam_ids=np.zeros(0, dtype=np.int8), frame_ids=np.zeros(0, dtype=np.uint8),
        raw_histograms=np.zeros((0, 2, 8, 1024), dtype=np.uint32),
        temperature_c=np.zeros((0, 2, 8), dtype=np.float32),
        timestamp_s=np.zeros(0, dtype=np.float64), pdc=None, tcm=None, tcl=None,
    )
    b.events.append(IntervalClosed(corrected_batch=CorrectedInterval(
        left_abs=10, right_abs=20, frames=frames,
    )))
    return b


def _corrected(mean, std, contrast, *, quality="ok"):
    return CorrectedFrame(
        abs_frame_id=12, t=5.0, side="left", cam_id=0,
        mean=mean, std=std, raw_u1=mean, raw_var=0.0, dark_var=0.0,
        contrast=contrast, quality=quality,
    )


def test_batch_nan_contrast_yields_nan_bfi():
    nan = float("nan")
    batch = _batch_with_corrected_frames([_corrected(nan, nan, nan, quality="nan_filled")])
    BfiBviStage(calibration=_trivial_calibration()).process(batch)
    f = batch.events[0].corrected_batch.frames[0]
    assert np.isnan(f.bfi) and np.isnan(f.bvi)
    assert f.quality == "nan_filled"


def test_batch_missing_contrast_yields_nan_bfi_not_top_of_scale():
    """contrast=None means shot-noise correction never produced a value.
    Defaulting it to 0.0 mapped it onto the calibrated maximum (BFI 10.0) —
    a plausible-looking flow reading manufactured from missing data."""
    batch = _batch_with_corrected_frames([_corrected(50.0, 10.0, None)])
    BfiBviStage(calibration=_trivial_calibration()).process(batch)
    assert np.isnan(batch.events[0].corrected_batch.frames[0].bfi)


def test_batch_genuine_zero_contrast_still_maps_to_calibrated_maximum():
    """A real measurement of K = C_min is top-of-scale flow, not invalid data —
    it must keep passing through the affine map unfiltered."""
    batch = _batch_with_corrected_frames([_corrected(50.0, 0.0, 0.0)])
    BfiBviStage(calibration=_trivial_calibration()).process(batch)
    assert batch.events[0].corrected_batch.frames[0].bfi == 10.0
