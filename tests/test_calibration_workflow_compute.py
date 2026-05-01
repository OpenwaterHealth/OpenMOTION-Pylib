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


# ----- build_result_rows + evaluate_passed -----

from omotion.CalibrationWorkflow import (
    build_result_rows,
    evaluate_passed,
)


def test_build_result_rows_pass_when_all_metrics_above_threshold():
    if not _have_fixtures():
        pytest.skip("fixture CSVs missing")
    thr = CalibrationThresholds(
        min_mean_per_camera=[1.0]*8,
        min_contrast_per_camera=[0.0]*8,
        min_bfi_per_camera=[-100.0]*8,
        min_bvi_per_camera=[-100.0]*8,
    )
    rows = build_result_rows(
        left_csv=_LEFT_FIXTURE, right_csv=_RIGHT_FIXTURE,
        left_camera_mask=0xFF, right_camera_mask=0xFF,
        skip_leading_frames=0,
        thresholds=thr,
        sensor_left=None, sensor_right=None,
        calibration=None,
    )
    assert len(rows) == 16
    assert all(r.mean_test == "PASS" for r in rows)
    assert all(r.contrast_test == "PASS" for r in rows)
    assert evaluate_passed(rows) is True


def test_build_result_rows_fail_when_mean_below_threshold():
    if not _have_fixtures():
        pytest.skip("fixture CSV missing")
    thr = CalibrationThresholds(
        min_mean_per_camera=[1e9]*8,
        min_contrast_per_camera=[0.0]*8,
        min_bfi_per_camera=[-100.0]*8,
        min_bvi_per_camera=[-100.0]*8,
    )
    rows = build_result_rows(
        left_csv=_LEFT_FIXTURE, right_csv=None,
        left_camera_mask=0xFF, right_camera_mask=0x00,
        skip_leading_frames=0,
        thresholds=thr,
        sensor_left=None, sensor_right=None,
        calibration=None,
    )
    assert len(rows) == 8
    assert all(r.mean_test == "FAIL" for r in rows)
    assert evaluate_passed(rows) is False


def test_build_result_rows_short_threshold_list_treated_as_pass():
    if not _have_fixtures():
        pytest.skip("fixture CSV missing")
    thr = CalibrationThresholds(
        min_mean_per_camera=[1e9, 1e9],   # only first two cams have a real bound
        min_contrast_per_camera=[],
        min_bfi_per_camera=[],
        min_bvi_per_camera=[],
    )
    rows = build_result_rows(
        left_csv=_LEFT_FIXTURE, right_csv=None,
        left_camera_mask=0xFF, right_camera_mask=0x00,
        skip_leading_frames=0,
        thresholds=thr,
        sensor_left=None, sensor_right=None,
        calibration=None,
    )
    rows_by_cam = {r.cam_id: r for r in rows}
    assert rows_by_cam[0].mean_test == "FAIL"
    assert rows_by_cam[1].mean_test == "FAIL"
    for cam in range(2, 8):
        assert rows_by_cam[cam].mean_test == "PASS"


def test_evaluate_passed_empty_rows_returns_false():
    assert evaluate_passed([]) is False


# ----- write_result_csv -----

from omotion.CalibrationWorkflow import write_result_csv


def test_write_result_csv_round_trip(tmp_path):
    rows = [
        CalibrationResultRow(
            camera_index=0, side="left", cam_id=0,
            mean=200.0, avg_contrast=0.4, bfi=5.0, bvi=5.5,
            mean_test="PASS", contrast_test="PASS",
            bfi_test="PASS", bvi_test="FAIL",
            security_id="sec-0", hwid="hw-x",
        ),
    ]
    out = tmp_path / "calibration-test.csv"
    write_result_csv(str(out), rows)
    assert out.exists()
    content = out.read_text(encoding="utf-8").splitlines()
    assert len(content) == 2
    header = content[0].split(",")
    assert header == [
        "camera_index", "side", "cam_id",
        "mean", "avg_contrast", "bfi", "bvi",
        "mean_test", "contrast_test", "bfi_test", "bvi_test",
        "security_id", "hwid",
    ]
    assert "left" in content[1]
    assert "FAIL" in content[1]
