"""Integration tests for CalibrationWorkflow with mocked ScanWorkflow.

Patches MotionInterface.scan_workflow.start_scan so sub-scans don't hit
hardware, and MotionInterface.write_calibration to record arguments.
Sub-scans run via run_collection_scan, which calls start_scan(request) and
polls scan_workflow.running — there is no on_complete_fn / ScanResult callback.
"""
import os
import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

from omotion import MotionInterface
from omotion.Calibration import Calibration
from omotion.CalibrationWorkflow import (
    CalibrationRequest,
    CalibrationResult,
    CalibrationThresholds,
)


_FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
_LEFT  = os.path.join(_FIXTURE_DIR, "scan_owC18EHALL_20251217_160949_left_maskFF.csv")
_RIGHT = os.path.join(_FIXTURE_DIR, "scan_owC18EHALL_20251217_160949_right_maskFF.csv")


def _have_fixtures() -> bool:
    return os.path.exists(_LEFT) and os.path.exists(_RIGHT)


@pytest.fixture
def thresholds():
    # The fixture CSV is truncated mid-scan, so the corrected stream's
    # dark-baseline endpoint is not a real laser-off frame and BFI/BVI
    # values come out unrealistic. Use very permissive thresholds — this
    # test exercises workflow plumbing, not the science values.
    return CalibrationThresholds(
        min_mean_per_camera=[0.0]*8,
        min_contrast_per_camera=[0.0]*8,
        min_bfi_per_camera=[-1e9]*8,
        min_bvi_per_camera=[-1e9]*8,
    )


@pytest.fixture
def request_obj(tmp_path, thresholds):
    return CalibrationRequest(
        operator_id="opX",
        output_dir=str(tmp_path),
        left_camera_mask=0xFF,
        right_camera_mask=0xFF,
        thresholds=thresholds,
        duration_sec=2,
        scan_delay_sec=0,
        max_duration_sec=60,
    )


@pytest.fixture
def interface():
    iface = MotionInterface(demo_mode=True)

    # CalibrationWorkflow now flashes sensors at phase 0 via
    # start_configure_camera_sensors. In demo mode there are no real
    # sensors to configure, so we stub it to immediately succeed.
    from omotion.ScanWorkflow import ConfigureResult

    def _fake_configure(req, *, on_complete_fn=None, on_log_fn=None, **kw):
        def _run():
            time.sleep(0.02)
            if on_complete_fn:
                on_complete_fn(ConfigureResult(ok=True, error=""))
        threading.Thread(target=_run, daemon=True).start()
        return True

    iface.start_configure_camera_sensors = _fake_configure
    return iface


def _make_fake_scan_workflow(interface, left, right):
    """Patch interface.scan_workflow so start_scan synthesises corrected
    samples through the sink contract used by the new pipeline (Phase E).

    Drives the _CalibrationCollectorSink directly: on_scan_start ->
    consume("final", EnrichedCorrectedInterval) -> on_complete.
    Sets scan_workflow.running True briefly then False so the polling
    loop in _run_subscan_capture exits with last_scan_error=None.
    """
    from omotion.pipeline.stages.dark import (
        EnrichedCorrectedFrame, EnrichedCorrectedInterval,
    )

    def _make_interval():
        frames = []
        for side in ("left", "right"):
            for cam_id in range(8):
                for fid in range(50, 100):
                    frames.append(EnrichedCorrectedFrame(
                        abs_frame_id=fid, t=fid / 40.0, side=side, cam_id=cam_id,
                        mean=200.0, std=80.0, contrast=0.4, bfi=5.0, bvi=5.0,
                    ))
        return EnrichedCorrectedInterval(left_abs=10, right_abs=240, frames=frames)

    sw = interface.scan_workflow
    done_evt = threading.Event()

    def _fake_start_scan(req):
        sw._running = True
        sw._last_scan_error = None
        sw._last_scan_canceled = False

        def _run():
            time.sleep(0.05)
            for sink in req.sinks:
                try:
                    sink.on_scan_start(None)
                except Exception:
                    pass
                try:
                    sink.consume("final", _make_interval())
                except Exception:
                    pass
                try:
                    sink.on_complete()
                except Exception:
                    pass
            sw._running = False
            done_evt.set()

        threading.Thread(target=_run, daemon=True).start()
        return True

    def _fake_await(*, timeout_sec=None):
        done_evt.wait(timeout=timeout_sec)

    sw.start_scan = _fake_start_scan
    sw.await_complete = _fake_await
    # last_scan_error / last_scan_canceled / running come straight from
    # the real ScanWorkflow attrs we just twiddled above.


def test_happy_path_produces_csv_and_passes(interface, request_obj):
    if not _have_fixtures():
        pytest.skip("fixture CSVs missing")

    _make_fake_scan_workflow(interface, _LEFT, _RIGHT)
    interface.write_calibration = MagicMock(
        return_value=Calibration(
            c_min=np.zeros((2, 8)), c_max=np.full((2, 8), 0.5),
            i_min=np.zeros((2, 8)), i_max=np.full((2, 8), 200.0),
            source="console",
        )
    )

    done = threading.Event()
    result_box: list[CalibrationResult] = []
    interface.start_calibration(
        request_obj,
        on_complete_fn=lambda r: (result_box.append(r), done.set()),
    )
    assert done.wait(timeout=60.0), "calibration didn't complete"
    r = result_box[0]
    assert r.ok is True
    assert r.passed is True
    assert r.canceled is False
    assert os.path.exists(r.csv_path)
    assert r.calibration is not None
    interface.write_calibration.assert_called_once()


def test_cancel_during_phase_1(interface, request_obj):
    if not _have_fixtures():
        pytest.skip("fixture CSVs missing")

    sw = interface.scan_workflow
    started = threading.Event()
    cancel_called = threading.Event()

    def _slow_scan(req):
        sw._running = True
        sw._last_scan_error = None
        sw._last_scan_canceled = False

        def _run():
            started.set()
            cancel_called.wait(timeout=5.0)
            sw._last_scan_canceled = True
            sw._running = False

        threading.Thread(target=_run, daemon=True).start()
        return True

    sw.start_scan = _slow_scan
    sw.await_complete = lambda *, timeout_sec=None: (
        time.sleep(min(0.1, timeout_sec)) if timeout_sec else None
    )
    sw.cancel_scan = MagicMock(side_effect=lambda **kw: cancel_called.set())
    interface.write_calibration = MagicMock()

    done = threading.Event()
    box: list[CalibrationResult] = []
    interface.start_calibration(
        request_obj,
        on_complete_fn=lambda r: (box.append(r), done.set()),
    )
    assert started.wait(timeout=5.0)
    interface.cancel_calibration()
    assert done.wait(timeout=15.0)
    r = box[0]
    assert r.ok is False
    assert r.canceled is True
    assert r.csv_path == ""
    interface.write_calibration.assert_not_called()


def test_phase1_scan_failure_aborts_before_write(interface, request_obj):
    """When the underlying scan worker raises, _run_subscan_capture should
    surface the error message via scan_workflow.last_scan_error so the
    workflow aborts before write_calibration is touched."""
    sw = interface.scan_workflow

    def _fail_scan(req):
        sw._running = True
        sw._last_scan_error = None
        sw._last_scan_canceled = False

        def _run():
            time.sleep(0.02)
            sw._last_scan_error = "USB lost"
            sw._running = False

        threading.Thread(target=_run, daemon=True).start()
        return True

    done_join = threading.Event()
    sw.start_scan = _fail_scan
    sw.await_complete = lambda *, timeout_sec=None: done_join.wait(timeout=timeout_sec)

    interface.write_calibration = MagicMock()

    done = threading.Event()
    box: list[CalibrationResult] = []
    interface.start_calibration(
        request_obj,
        on_complete_fn=lambda r: (box.append(r), done.set()),
    )
    # Let the fake scan thread finish before the polling loop awaits.
    time.sleep(0.05)
    done_join.set()
    assert done.wait(timeout=10.0)
    r = box[0]
    assert r.ok is False
    assert "USB lost" in r.error
    interface.write_calibration.assert_not_called()


# ---------------------------------------------------------------------------
# Task 13: _run_subscan_capture passes collector sink + skip_default_storage
# ---------------------------------------------------------------------------

def test_subscan_uses_collector_sink_and_skip_default_storage(interface, request_obj):
    """_run_subscan_capture (called by start_calibration) must attach a
    _CalibrationCollectorSink to each ScanRequest and set
    skip_default_storage=True.  Verifies the new sink-based API shape
    added in Phase D of the pipeline cutover (Task 13).
    """
    from omotion.CalibrationWorkflow import _CalibrationCollectorSink

    captured_requests: list = []

    def _capture_and_complete(req, **kw):
        # run_collection_scan calls start_scan(request) then polls
        # scan_workflow.running (False here — no worker is started), so just
        # capture the request and report a successful launch.
        captured_requests.append(req)
        return True

    interface.scan_workflow.start_scan = _capture_and_complete
    interface.write_calibration = MagicMock(
        return_value=Calibration(
            c_min=np.zeros((2, 8)), c_max=np.full((2, 8), 0.5),
            i_min=np.zeros((2, 8)), i_max=np.full((2, 8), 200.0),
            source="console",
        )
    )

    done = threading.Event()
    box: list[CalibrationResult] = []

    interface.start_calibration(
        request_obj,
        on_complete_fn=lambda r: (box.append(r), done.set()),
    )
    assert done.wait(timeout=15.0), "calibration did not complete"

    # The calibration workflow makes at least 2 sub-scans (phase 1 + phase 4).
    assert len(captured_requests) >= 1, "No ScanRequests were captured"

    for req in captured_requests:
        assert req.skip_default_storage is True, (
            f"ScanRequest.skip_default_storage should be True; got {req.skip_default_storage}"
        )
        collector_sinks = [
            s for s in req.sinks
            if isinstance(s, _CalibrationCollectorSink)
        ]
        assert len(collector_sinks) >= 1, (
            f"Expected a _CalibrationCollectorSink in req.sinks; got {req.sinks}"
        )


# ---------------------------------------------------------------------------
# Task 16: start_test_scan also uses collector sink + skip_default_storage
# ---------------------------------------------------------------------------

def test_start_test_scan_uses_collector_sink_and_skip_default_storage(
    interface, request_obj
):
    """start_test_scan's sub-scan must also carry the collector sink and
    skip_default_storage=True.  Same shape as the calibration sub-scan
    (Task 16 of the pipeline cutover)."""
    from omotion.CalibrationWorkflow import _CalibrationCollectorSink, TestScanResult

    captured_requests: list = []

    def _capture_and_complete(req, **kw):
        captured_requests.append(req)
        return True

    interface.scan_workflow.start_scan = _capture_and_complete

    done = threading.Event()
    box: list[TestScanResult] = []
    interface.start_test_scan(
        request_obj,
        on_complete_fn=lambda r: (box.append(r), done.set()),
    )
    assert done.wait(timeout=15.0), "test scan did not complete"

    assert len(captured_requests) >= 1, "No ScanRequests were captured"

    for req in captured_requests:
        assert req.skip_default_storage is True, (
            f"ScanRequest.skip_default_storage should be True; got {req.skip_default_storage}"
        )
        collector_sinks = [
            s for s in req.sinks
            if isinstance(s, _CalibrationCollectorSink)
        ]
        assert len(collector_sinks) >= 1, (
            f"Expected a _CalibrationCollectorSink in req.sinks; got {req.sinks}"
        )


# ---------------------------------------------------------------------------
# Task 2: outcome wired through both workers (Refs #199)
# ---------------------------------------------------------------------------

def test_happy_path_outcome_is_passed(interface, request_obj):
    from omotion.CalibrationWorkflow import CalibrationOutcome
    _make_fake_scan_workflow(interface, _LEFT, _RIGHT)
    interface.write_calibration = MagicMock(
        side_effect=lambda cmin, cmax, imin, imax: Calibration(
            c_min=cmin, c_max=cmax, i_min=imin, i_max=imax, source="test"))
    done = threading.Event()
    holder = {}
    interface.start_calibration(
        request_obj, on_complete_fn=lambda r: (holder.update(r=r), done.set()))
    assert done.wait(30)
    assert holder["r"].outcome is CalibrationOutcome.PASSED


def test_cancel_outcome_is_canceled_not_timed_out(interface, request_obj):
    """Regression for the old finally-block back-fill that stamped ANY
    stop_evt as 'exceeded max_duration_sec' when the canceled flag hadn't
    been picked up by a phase boundary yet."""
    if not _have_fixtures():
        pytest.skip("fixture CSVs missing")

    from omotion.CalibrationWorkflow import CalibrationOutcome

    # (mirror the existing test_cancel_during_phase_1 setup, then:)
    sw = interface.scan_workflow
    started = threading.Event()
    cancel_called = threading.Event()

    def _slow_scan(req):
        sw._running = True
        sw._last_scan_error = None
        sw._last_scan_canceled = False

        def _run():
            started.set()
            cancel_called.wait(timeout=5.0)
            sw._last_scan_canceled = True
            sw._running = False

        threading.Thread(target=_run, daemon=True).start()
        return True

    sw.start_scan = _slow_scan
    sw.await_complete = lambda *, timeout_sec=None: (
        time.sleep(min(0.1, timeout_sec)) if timeout_sec else None
    )
    sw.cancel_scan = MagicMock(side_effect=lambda **kw: cancel_called.set())
    interface.write_calibration = MagicMock()

    # ... start_calibration, cancel mid-phase-1, wait for completion ...
    done = threading.Event()
    holder = {}
    interface.start_calibration(
        request_obj,
        on_complete_fn=lambda r: (holder.update(r=r), done.set()),
    )
    assert started.wait(timeout=5.0)
    interface.cancel_calibration()
    assert done.wait(timeout=15.0)
    assert holder["r"].outcome is CalibrationOutcome.CANCELED
    assert "max_duration_sec" not in holder["r"].error


def test_fail_verdict_rolls_back_eeprom(interface, request_obj, thresholds):
    """A FAILED calibration must not leave the unvalidated calibration on
    the console: write_calibration is called a second time with the
    pre-run values and the result reports rolled_back=True."""
    from dataclasses import replace
    from omotion.CalibrationWorkflow import CalibrationThresholds
    _make_fake_scan_workflow(interface, _LEFT, _RIGHT)
    prior = interface.get_calibration()
    # Snapshot before starting — the demo interface's cached calibration
    # object could in principle be refreshed by a later write, so compare
    # against copies taken now rather than re-reading `prior` after the
    # run completes.
    prior_c_min = prior.c_min.copy()
    prior_c_max = prior.c_max.copy()
    strict = CalibrationThresholds(
        min_mean_per_camera=[1e9] * 8,      # nothing can pass
        min_contrast_per_camera=[0.0] * 8,
        min_bfi_per_camera=[-1e9] * 8,
        min_bvi_per_camera=[-1e9] * 8,
    )
    req = replace(request_obj, thresholds=strict)
    calls = []
    def _record_write(cmin, cmax, imin, imax):
        calls.append((cmin, cmax, imin, imax))
        return Calibration(c_min=cmin, c_max=cmax, i_min=imin, i_max=imax,
                           source="test")
    interface.write_calibration = MagicMock(side_effect=_record_write)
    done = threading.Event(); holder = {}
    interface.start_calibration(
        req, on_complete_fn=lambda r: (holder.update(r=r), done.set()))
    assert done.wait(30)
    r = holder["r"]
    assert r.ok and not r.passed
    assert r.rolled_back is True
    assert len(calls) == 2                       # new write + restore
    np.testing.assert_array_equal(calls[1][0], prior_c_min)
    np.testing.assert_array_equal(calls[1][1], prior_c_max)


def test_pass_verdict_does_not_roll_back(interface, request_obj):
    # happy-path arrangement from test_happy_path_produces_csv_and_passes
    if not _have_fixtures():
        pytest.skip("fixture CSVs missing")

    _make_fake_scan_workflow(interface, _LEFT, _RIGHT)
    interface.write_calibration = MagicMock(
        return_value=Calibration(
            c_min=np.zeros((2, 8)), c_max=np.full((2, 8), 0.5),
            i_min=np.zeros((2, 8)), i_max=np.full((2, 8), 200.0),
            source="console",
        )
    )

    done = threading.Event()
    holder = {}
    interface.start_calibration(
        request_obj,
        on_complete_fn=lambda r: (holder.update(r=r), done.set()),
    )
    assert done.wait(timeout=60.0), "calibration didn't complete"
    assert holder["r"].rolled_back is False
    interface.write_calibration.assert_called_once()


def test_rollback_failure_is_best_effort(interface, request_obj):
    """If the restore write itself raises (e.g. console USB died), the
    result still completes, rolled_back stays False, and the error text
    carries the rollback failure."""
    # strict thresholds as above; write_calibration succeeds on call 1,
    # raises RuntimeError("usb gone") on call 2.
    from dataclasses import replace
    from omotion.CalibrationWorkflow import CalibrationThresholds
    _make_fake_scan_workflow(interface, _LEFT, _RIGHT)
    strict = CalibrationThresholds(
        min_mean_per_camera=[1e9] * 8,      # nothing can pass
        min_contrast_per_camera=[0.0] * 8,
        min_bfi_per_camera=[-1e9] * 8,
        min_bvi_per_camera=[-1e9] * 8,
    )
    req = replace(request_obj, thresholds=strict)

    calls = []
    def _write_side_effect(cmin, cmax, imin, imax):
        calls.append((cmin, cmax, imin, imax))
        if len(calls) == 1:
            return Calibration(c_min=cmin, c_max=cmax, i_min=imin, i_max=imax,
                               source="test")
        raise RuntimeError("usb gone")
    interface.write_calibration = MagicMock(side_effect=_write_side_effect)

    done = threading.Event(); holder = {}
    interface.start_calibration(
        req, on_complete_fn=lambda r: (holder.update(r=r), done.set()))
    assert done.wait(30)
    r = holder["r"]
    assert r.rolled_back is False
    assert "rollback failed" in r.error


def test_watchdog_timeout_outcome_is_timed_out(interface, request_obj):
    from dataclasses import replace
    from omotion.CalibrationWorkflow import CalibrationOutcome
    # A fake scan workflow that never completes, and a 1-second watchdog.
    req = replace(request_obj, max_duration_sec=1)

    # (fake start_scan sets running=True and never flips it back; cancel_scan
    #  flips running=False so _run_subscan_capture's poll loop exits)
    sw = interface.scan_workflow

    def _never_completing_scan(r):
        sw._running = True
        sw._last_scan_error = None
        sw._last_scan_canceled = False
        return True

    def _fake_cancel_scan(**kw):
        sw._running = False
        sw._last_scan_canceled = True

    sw.start_scan = _never_completing_scan
    sw.cancel_scan = MagicMock(side_effect=_fake_cancel_scan)
    sw.await_complete = lambda *, timeout_sec=None: (
        time.sleep(min(0.05, timeout_sec)) if timeout_sec else None
    )
    interface.write_calibration = MagicMock()

    # ... start_calibration(req, ...), wait ...
    done = threading.Event()
    holder = {}
    interface.start_calibration(
        req, on_complete_fn=lambda r: (holder.update(r=r), done.set()))
    assert done.wait(timeout=15.0)
    assert holder["r"].outcome is CalibrationOutcome.TIMED_OUT
    assert "max_duration_sec" in holder["r"].error
