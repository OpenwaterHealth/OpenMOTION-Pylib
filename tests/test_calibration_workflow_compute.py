"""Unit tests for CalibrationWorkflow pure helpers (no hardware)."""

import os

import numpy as np
import pytest

from omotion.Calibration import Calibration
from omotion.CalibrationWorkflow import (
    CalibrationRequest,
    CalibrationResult,
    CalibrationResultRow,
    CalibrationThresholds,
)


def _thresholds():
    return CalibrationThresholds(
        min_mean_per_camera=[100.0]*8,
        min_contrast_per_camera=[0.2]*8,
        min_bfi_per_camera=[3.0]*8,
        min_bvi_per_camera=[3.0]*8,
    )


def test_request_requires_duration_sec():
    with pytest.raises(TypeError):
        # duration_sec is required, no default.
        CalibrationRequest(
            operator_id="op",
            output_dir="/tmp/x",
            left_camera_mask=0xFF,
            right_camera_mask=0xFF,
            thresholds=_thresholds(),
        )


def test_request_defaults():
    req = CalibrationRequest(
        operator_id="op",
        output_dir="/tmp/x",
        left_camera_mask=0xFF,
        right_camera_mask=0xFF,
        thresholds=_thresholds(),
        duration_sec=5,
    )
    assert req.scan_delay_sec == 1
    assert req.max_duration_sec == 600
    assert req.notes == ""


def test_thresholds_lengths_are_eight():
    t = _thresholds()
    assert len(t.min_mean_per_camera) == 8
    assert len(t.min_contrast_per_camera) == 8
    assert len(t.min_bfi_per_camera) == 8
    assert len(t.min_bvi_per_camera) == 8


def test_result_default_state_is_failed():
    r = CalibrationResult(
        ok=False, passed=False, canceled=False, error="",
        csv_path="", calibration=None, rows=[],
        calibration_scan_left_path="", calibration_scan_right_path="",
        validation_scan_left_path="", validation_scan_right_path="",
        started_timestamp="",
    )
    assert r.ok is False
    assert r.passed is False


# ----- _collect_samples_from_csvs -----

from omotion.CalibrationWorkflow import _collect_samples_from_csvs


_FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
_LEFT_FIXTURE  = os.path.join(_FIXTURE_DIR, "scan_owC18EHALL_20251217_160949_left_maskFF.csv")
_RIGHT_FIXTURE = os.path.join(_FIXTURE_DIR, "scan_owC18EHALL_20251217_160949_right_maskFF.csv")


def _have_fixtures() -> bool:
    return os.path.exists(_LEFT_FIXTURE) and os.path.exists(_RIGHT_FIXTURE)


def test_collect_samples_loads_left_csv():
    if not _have_fixtures():
        pytest.skip("fixture CSVs missing")
    samples = _collect_samples_from_csvs(
        left_csv=_LEFT_FIXTURE, right_csv=None,
        skip_leading_frames=0,
    )
    assert len(samples) > 0
    assert all(s.side == "left" for s in samples)
    assert all(0 <= s.cam_id < 8 for s in samples)


def test_collect_samples_skips_leading_frames():
    if not _have_fixtures():
        pytest.skip("fixture CSVs missing")
    full = _collect_samples_from_csvs(
        left_csv=_LEFT_FIXTURE, right_csv=None, skip_leading_frames=0,
    )
    skipped = _collect_samples_from_csvs(
        left_csv=_LEFT_FIXTURE, right_csv=None, skip_leading_frames=40,
    )
    skipped_min_frame = min(s.absolute_frame_id for s in skipped)
    assert skipped_min_frame >= 40
    assert len(skipped) < len(full)


# ----- compute_calibration_from_csvs -----

from omotion.CalibrationWorkflow import (
    DegenerateCalibrationError,
    compute_calibration_from_csvs,
)


def test_compute_calibration_against_fixture():
    if not _have_fixtures():
        pytest.skip("fixture CSVs missing")
    cal = compute_calibration_from_csvs(
        left_csv=_LEFT_FIXTURE, right_csv=_RIGHT_FIXTURE,
        left_camera_mask=0xFF, right_camera_mask=0xFF,
        skip_leading_frames=0,
    )
    assert cal.c_min.shape == (2, 8)
    assert cal.c_max.shape == (2, 8)
    assert np.all(cal.c_min == 0.0)
    assert np.all(cal.i_min == 0.0)
    assert np.all(cal.c_max > 0)
    assert np.all(cal.i_max > 0)
    assert cal.source == "console"


def test_compute_calibration_inactive_cameras_use_defaults():
    """Inactive cameras (mask bit clear) fall back to defaults so the
    (2, 8) array always satisfies monotonicity."""
    if not _have_fixtures():
        pytest.skip("fixture CSV missing")
    cal = compute_calibration_from_csvs(
        left_csv=_LEFT_FIXTURE, right_csv=None,
        left_camera_mask=0x0F,        # only cams 0..3 active on left
        right_camera_mask=0x00,       # right entirely inactive
        skip_leading_frames=0,
    )
    defaults = Calibration.default()
    np.testing.assert_array_equal(cal.c_max[1], defaults.c_max[1])
    np.testing.assert_array_equal(cal.i_max[1], defaults.i_max[1])
    np.testing.assert_array_equal(cal.c_max[0, 4:], defaults.c_max[0, 4:])
    np.testing.assert_array_equal(cal.i_max[0, 4:], defaults.i_max[0, 4:])


def test_compute_calibration_degenerate_active_cam_raises():
    """Active cam with no samples → DegenerateCalibrationError."""
    with pytest.raises(DegenerateCalibrationError):
        compute_calibration_from_csvs(
            left_csv=None, right_csv=None,
            left_camera_mask=0x01,        # cam 0 active, no CSV
            right_camera_mask=0x00,
            skip_leading_frames=0,
        )
