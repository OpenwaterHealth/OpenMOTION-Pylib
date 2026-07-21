"""Tests for ContactQualityWorkflow — SDK-owned CQ check procedure.

These tests are pure-software and require no hardware. Thresholds and
results are in **background-subtracted DN** scale (subtracted_mean), matching
the legacy ContactQuality module semantics.
"""

import math

import numpy as np
import pytest
from unittest.mock import MagicMock

from omotion.ContactQualityWorkflow import (
    ContactQualityWorkflow,
    CamCQResult,
    ContactQualityResult,
    _ContactQualitySink,
)
from omotion.pipeline.batch import FrameBatch
from omotion.pulse.synth import synth_bfi


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dn_batch(
    n_frames: int,
    dn_value: float,
    frame_types=None,
) -> FrameBatch:
    """Return a real FrameBatch with uniform DN across all cams.

    The live pipeline delivers one (side, cam) per row, so each logical
    frame expands to 16 rows (2 sides × 8 cams) sharing the frame_type.
    Both subtracted_mean and mean_dc_rt get the same value so the test stays
    valid regardless of which the sink reads for a given frame_type.
    """
    if frame_types is None:
        frame_types = ["light"] * n_frames
    rows = n_frames * 16
    cam_ids = np.tile(np.arange(8, dtype=np.int8), n_frames * 2)
    side_ids = np.tile(np.repeat(np.array([0, 1], dtype=np.int8), 8), n_frames)
    arr = np.full((rows, 2, 8), dn_value, dtype=np.float32)
    return FrameBatch(
        cam_ids=cam_ids,
        frame_ids=np.tile(np.arange(n_frames, dtype=np.uint8).repeat(16), 1),
        side_ids=side_ids,
        raw_histograms=None,
        temperature_c=None,
        timestamp_s=np.zeros(rows, dtype=np.float64),
        pdc=None, tcm=None, tcl=None,
        frame_type=np.repeat(np.array(frame_types, dtype="<U8"), 16),
        subtracted_mean=arr,
        mean_dc_rt=arr.copy(),
        std_raw=np.full((rows, 2, 8), 2.5, dtype=np.float32),
    )


# ---------------------------------------------------------------------------
# _ContactQualitySink unit tests
# ---------------------------------------------------------------------------

def test_cq_sink_marks_camera_ok_when_light_above_threshold_and_no_dark():
    sink = _ContactQualitySink(
        dark_thresholds=[3.0] * 8,
        light_thresholds=[15.0] * 8,
    )
    sink.on_scan_start(None)
    sink.consume("live", _dn_batch(4, 20.0))   # well above light threshold
    result = sink.result(left_mask=(1 << 2), right_mask=0, duration_sec=1.0)

    assert ("left", 2) in result.per_camera
    cam = result.per_camera[("left", 2)]
    assert cam.passed is True
    assert cam.reason == "ok"
    assert cam.light_avg_dn == pytest.approx(20.0, abs=1e-4)
    assert result.passed is True


def test_cq_sink_fails_camera_below_light_threshold():
    sink = _ContactQualitySink(
        dark_thresholds=[3.0] * 8,
        light_thresholds=[15.0] * 8,
    )
    sink.on_scan_start(None)
    sink.consume("live", _dn_batch(4, 5.0))    # below light threshold 15.0
    result = sink.result(left_mask=0x01, right_mask=0, duration_sec=1.0)

    cam = result.per_camera[("left", 0)]
    assert cam.passed is False
    assert cam.reason == "poor_contact"


def test_cq_sink_records_light_std_without_thresholding_it():
    sink = _ContactQualitySink(
        dark_thresholds=[3.0] * 8,
        light_thresholds=[15.0] * 8,
    )
    sink.on_scan_start(None)
    batch = _dn_batch(2, 20.0)
    batch.std_raw[:] = 500.0
    sink.consume("live", batch)

    result = sink.result(left_mask=0x01, right_mask=0, duration_sec=1.0)

    cam = result.per_camera[("left", 0)]
    assert cam.passed is True
    assert cam.reason == "ok"
    assert cam.light_std_dn == pytest.approx(500.0)


def test_cq_sink_fails_camera_when_dark_frame_exceeds_threshold():
    sink = _ContactQualitySink(
        dark_thresholds=[3.0] * 8,
        light_thresholds=[15.0] * 8,
    )
    sink.on_scan_start(None)
    # First good light frames, then a dark frame above threshold:
    sink.consume("live", _dn_batch(4, 20.0))
    sink.consume("live", _dn_batch(2, 8.0, frame_types=["dark", "dark"]))
    result = sink.result(left_mask=0x01, right_mask=0, duration_sec=1.0)

    cam = result.per_camera[("left", 0)]
    assert cam.passed is False
    assert cam.reason == "ambient_light"
    assert cam.dark_max_dn == pytest.approx(8.0, abs=1e-4)


def test_cq_sink_no_signal_when_no_data_collected():
    sink = _ContactQualitySink(
        dark_thresholds=[3.0] * 8,
        light_thresholds=[15.0] * 8,
    )
    sink.on_scan_start(None)
    result = sink.result(left_mask=0x01, right_mask=0, duration_sec=1.0)

    cam = result.per_camera[("left", 0)]
    assert cam.passed is False
    assert cam.reason == "no_signal"


def test_cq_sink_skips_warmup_and_stale_frames():
    sink = _ContactQualitySink(
        dark_thresholds=[3.0] * 8,
        light_thresholds=[15.0] * 8,
    )
    sink.on_scan_start(None)
    batch = _dn_batch(4, 20.0, frame_types=["warmup", "stale", "warmup", "stale"])
    sink.consume("live", batch)
    result = sink.result(left_mask=0x01, right_mask=0, duration_sec=1.0)

    cam = result.per_camera[("left", 0)]
    assert cam.reason == "no_signal"


def test_cq_sink_ignores_non_live_channel():
    sink = _ContactQualitySink(
        dark_thresholds=[3.0] * 8,
        light_thresholds=[15.0] * 8,
    )
    sink.on_scan_start(None)
    sink.consume("raw",   _dn_batch(4, 20.0))
    sink.consume("final", _dn_batch(4, 20.0))
    result = sink.result(left_mask=0x01, right_mask=0, duration_sec=1.0)

    cam = result.per_camera[("left", 0)]
    assert cam.reason == "no_signal"


def test_cq_sink_uses_rolling_window_not_cumulative_mean():
    """Light rolling window with size 3: only the last 3 light frames count."""
    sink = _ContactQualitySink(
        dark_thresholds=[100.0] * 8,
        light_thresholds=[15.0] * 8,
        rolling_window=3,
    )
    sink.on_scan_start(None)
    # Feed: 5.0, 5.0, 5.0, 25.0, 25.0, 25.0 — rolling avg should be 25.0 not 15.0
    for v in (5.0, 5.0, 5.0, 25.0, 25.0, 25.0):
        sink.consume("live", _dn_batch(1, v))
    result = sink.result(left_mask=0x01, right_mask=0, duration_sec=1.0)
    cam = result.per_camera[("left", 0)]
    assert cam.light_avg_dn == pytest.approx(25.0, abs=1e-4)
    assert cam.reason == "ok"


def test_cq_sink_overall_passed_requires_all_cams():
    sink = _ContactQualitySink(
        dark_thresholds=[3.0] * 8,
        light_thresholds=[15.0] * 8,
    )
    sink.on_scan_start(None)
    batch = _dn_batch(1, 20.0)
    batch.subtracted_mean[:, 0, 1] = 5.0   # left cam 1 below light threshold
    batch.mean_dc_rt[:, 0, 1] = 5.0
    sink.consume("live", batch)
    result = sink.result(left_mask=0x03, right_mask=0, duration_sec=1.0)

    assert result.per_camera[("left", 0)].passed is True
    assert result.per_camera[("left", 1)].passed is False
    assert result.passed is False


def test_cq_sink_on_scan_start_clears_accumulated_data():
    sink = _ContactQualitySink(
        dark_thresholds=[3.0] * 8,
        light_thresholds=[15.0] * 8,
    )
    sink.on_scan_start(None)
    sink.consume("live", _dn_batch(4, 20.0))
    sink.on_scan_start(None)
    result = sink.result(left_mask=0x01, right_mask=0, duration_sec=1.0)
    cam = result.per_camera[("left", 0)]
    assert cam.reason == "no_signal"


# ---------------------------------------------------------------------------
# ContactQualityWorkflow end-to-end tests
# ---------------------------------------------------------------------------

def test_cq_workflow_check_drives_scan_and_returns_result():
    fake_scan = MagicMock()
    fake_scan.await_complete = MagicMock()

    def _drive_scan(request):
        sink = request.sinks[0]
        sink.on_scan_start(None)
        sink.consume("live", _dn_batch(10, 20.0))   # well above light threshold
        sink.on_complete()
        return True

    fake_scan.start_scan.side_effect = _drive_scan

    cq = ContactQualityWorkflow(scan_workflow=fake_scan)
    result = cq.check(
        duration_sec=1.0,
        rolling_window=10,
        dark_threshold_per_camera=[3.0] * 8,
        light_threshold_per_camera=[15.0] * 8,
        left_camera_mask=0xFF,
        right_camera_mask=0,
    )

    assert isinstance(result, ContactQualityResult)
    assert result.passed is True
    assert len(result.per_camera) == 8
    for key, cam in result.per_camera.items():
        assert cam.side == "left"
        assert cam.passed is True
        assert cam.reason == "ok"


def test_cq_workflow_check_uses_skip_default_storage():
    from omotion.ContactQualityWorkflow import _ContactQualitySink as CQSink

    captured: dict = {}

    def _capture(request):
        captured["req"] = request
        request.sinks[0].on_scan_start(None)
        request.sinks[0].on_complete()
        return True

    fake_scan = MagicMock()
    fake_scan.start_scan.side_effect = _capture
    fake_scan.await_complete = MagicMock()

    cq = ContactQualityWorkflow(scan_workflow=fake_scan)
    cq.check(
        duration_sec=0.5,
        rolling_window=5,
        dark_threshold_per_camera=[3.0] * 8,
        light_threshold_per_camera=[15.0] * 8,
        left_camera_mask=0x01,
        right_camera_mask=0,
    )

    req = captured.get("req")
    assert req is not None
    assert req.skip_default_storage is True
    cq_sinks = [s for s in req.sinks if isinstance(s, CQSink)]
    assert len(cq_sinks) == 1


def test_cq_workflow_calls_await_complete():
    fake_scan = MagicMock()
    fake_scan.start_scan = MagicMock(return_value=True)

    cq = ContactQualityWorkflow(scan_workflow=fake_scan)
    cq.check(
        duration_sec=0.5,
        rolling_window=5,
        dark_threshold_per_camera=[3.0] * 8,
        light_threshold_per_camera=[15.0] * 8,
        left_camera_mask=0x01,
        right_camera_mask=0,
    )
    fake_scan.await_complete.assert_called_once()


def test_cq_workflow_fails_when_below_light_threshold():
    fake_scan = MagicMock()
    fake_scan.await_complete = MagicMock()

    def _drive_scan(request):
        sink = request.sinks[0]
        sink.on_scan_start(None)
        sink.consume("live", _dn_batch(5, 5.0))   # below light=15.0
        sink.on_complete()
        return True

    fake_scan.start_scan.side_effect = _drive_scan

    cq = ContactQualityWorkflow(scan_workflow=fake_scan)
    result = cq.check(
        duration_sec=1.0,
        rolling_window=5,
        dark_threshold_per_camera=[3.0] * 8,
        light_threshold_per_camera=[15.0] * 8,
        left_camera_mask=0x01,
        right_camera_mask=0,
    )

    assert result.passed is False


# ---------------------------------------------------------------------------
# Pulse-validity criterion (issue #126)
# ---------------------------------------------------------------------------

def test_cam_cq_result_has_pulse_fields_defaulting_unevaluated():
    r = CamCQResult(
        side="left", cam_id=0, passed=True,
        light_avg_dn=20.0, light_std_dn=2.5, dark_max_dn=1.0, dark_std_dn=2.5,
        reason="ok",
    )
    assert r.pulse_valid is False
    assert math.isnan(r.pulse_coverage)
    assert math.isnan(r.pulse_hr_bpm)
    assert math.isnan(r.pulse_periodicity)


def _pulse_light_batch(t, bfi_2x8, mean_dc=20.0):
    """FrameBatch of len(t) light frames.

    ``bfi_2x8`` is (n_frames, 2, 8) per-(side,cam) bfi_live. mean_dc_rt and
    subtracted_mean are set to a constant that passes the signal-level checks,
    so the pulse criterion is what decides the verdict.
    """
    n = len(t)
    rows = n * 16
    row_frame = np.repeat(np.arange(n), 16)
    cam_ids = np.tile(np.arange(8, dtype=np.int8), n * 2)
    side_ids = np.tile(np.repeat(np.array([0, 1], dtype=np.int8), 8), n)
    mean = np.full((rows, 2, 8), mean_dc, dtype=np.float32)
    bfi_rows = bfi_2x8[row_frame].astype(np.float32)      # (rows, 2, 8)
    return FrameBatch(
        cam_ids=cam_ids,
        frame_ids=np.repeat(np.arange(n, dtype=np.uint8), 16),
        side_ids=side_ids,
        raw_histograms=None, temperature_c=None,
        timestamp_s=np.repeat(np.asarray(t, dtype=np.float64), 16),
        pdc=None, tcm=None, tcl=None,
        frame_type=np.repeat(np.array(["light"], dtype="<U8"), rows),
        subtracted_mean=mean.copy(),
        mean_dc_rt=mean.copy(),
        bfi_live=bfi_rows,
        std_raw=np.full((rows, 2, 8), 2.5, dtype=np.float32),
    )


def _pulse_bfi_2x8(duration_s=15.0, seed=0):
    t, v = synth_bfi(duration_s=duration_s, bpm=72, amp=2.0, baseline=5.0,
                     noise=0.05, seed=seed)
    bfi = np.repeat(v[:, None, None], 8, axis=2)          # broadcast to (n,1,8)
    bfi = np.repeat(bfi, 2, axis=1)                        # (n, 2, 8)
    return t, bfi


def test_cq_sink_marks_channel_ok_with_valid_pulse():
    sink = _ContactQualitySink(
        dark_thresholds=[3.0] * 8, light_thresholds=[15.0] * 8,
        evaluate_pulse=True, pulse_min_coverage=0.75,
    )
    sink.on_scan_start(None)
    t, bfi = _pulse_bfi_2x8()
    sink.consume("live", _pulse_light_batch(t, bfi))
    res = sink.result(left_mask=0x01, right_mask=0, duration_sec=15.0)
    cam = res.per_camera[("left", 0)]
    assert cam.reason == "ok"
    assert cam.passed is True
    assert cam.pulse_valid is True
    assert cam.pulse_coverage > 0.75


def test_cq_sink_marks_no_pulse_when_signal_ok_but_flat():
    sink = _ContactQualitySink(
        dark_thresholds=[3.0] * 8, light_thresholds=[15.0] * 8,
        evaluate_pulse=True, pulse_min_coverage=0.75,
    )
    sink.on_scan_start(None)
    n = 600
    t = np.arange(n) * 0.025
    flat = np.full((n, 2, 8), 5.0)                        # signal ok, no pulse
    sink.consume("live", _pulse_light_batch(t, flat))
    res = sink.result(left_mask=0x01, right_mask=0, duration_sec=15.0)
    cam = res.per_camera[("left", 0)]
    assert cam.reason == "no_pulse"
    assert cam.passed is False
    assert cam.pulse_valid is False


def test_cq_sink_poor_contact_takes_precedence_over_pulse():
    sink = _ContactQualitySink(
        dark_thresholds=[3.0] * 8, light_thresholds=[15.0] * 8,
        evaluate_pulse=True, pulse_min_coverage=0.75,
    )
    sink.on_scan_start(None)
    t, bfi = _pulse_bfi_2x8()
    # mean_dc below the light threshold -> poor_contact, regardless of pulse.
    sink.consume("live", _pulse_light_batch(t, bfi, mean_dc=5.0))
    res = sink.result(left_mask=0x01, right_mask=0, duration_sec=15.0)
    cam = res.per_camera[("left", 0)]
    assert cam.reason == "poor_contact"
    assert math.isnan(cam.pulse_coverage)   # pulse not evaluated on a failed channel


def test_cq_sink_evaluate_pulse_off_leaves_pulse_unevaluated():
    sink = _ContactQualitySink(
        dark_thresholds=[3.0] * 8, light_thresholds=[15.0] * 8,
    )  # evaluate_pulse defaults False
    sink.on_scan_start(None)
    n = 600
    t = np.arange(n) * 0.025
    flat = np.full((n, 2, 8), 5.0)
    sink.consume("live", _pulse_light_batch(t, flat))
    res = sink.result(left_mask=0x01, right_mask=0, duration_sec=15.0)
    cam = res.per_camera[("left", 0)]
    assert cam.reason == "ok"                # unchanged legacy behavior
    assert cam.passed is True
    assert cam.pulse_valid is False
    assert math.isnan(cam.pulse_coverage)


def test_cq_workflow_check_evaluate_pulse_end_to_end():
    t, bfi = _pulse_bfi_2x8()

    def _drive(request):
        sink = request.sinks[0]
        sink.on_scan_start(None)
        sink.consume("live", _pulse_light_batch(t, bfi))
        sink.on_complete()
        return True

    fake = MagicMock()
    fake.await_complete = MagicMock()
    fake.start_scan.side_effect = _drive
    cq = ContactQualityWorkflow(scan_workflow=fake)
    res = cq.check(
        duration_sec=15.0, rolling_window=10,
        dark_threshold_per_camera=[3.0] * 8,
        light_threshold_per_camera=[15.0] * 8,
        left_camera_mask=0x01, right_camera_mask=0,
        evaluate_pulse=True,
    )
    cam = res.per_camera[("left", 0)]
    assert cam.pulse_valid is True
    assert cam.reason == "ok"
