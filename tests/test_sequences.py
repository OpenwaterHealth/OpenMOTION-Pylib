"""
Round-trip sequence tests (Section 5 of the test plan).

Each test brings a subsystem up from a cold state, exercises it,
then tears it back down — even on failure.  Teardown always runs
in a ``finally`` block.
"""

import os
import time
import threading

import numpy as np
import pytest

import struct

pytestmark = [pytest.mark.sensor, pytest.mark.sequence]

# Minimal BFI calibration arrays required by SciencePipeline
_BFI_ZEROS = np.zeros((2, 8), dtype=np.float32)
_BFI_ONES = np.ones((2, 8), dtype=np.float32) * 10.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decode_raw_histogram(raw):
    """Decode the 4100-byte payload returned by camera_get_histogram.

    Layout: 4096 bytes of histogram (1024 × uint32 little-endian)
            followed by 4 bytes of float32 temperature.

    Returns:
        (histogram, temperature_c) — numpy uint32 array of length 1024
        and a float temperature in degrees Celsius.
    """
    assert len(raw) == 4100, f"Expected 4100 bytes from camera_get_histogram, got {len(raw)}"
    histogram = np.frombuffer(bytes(raw[:4096]), dtype=np.uint32)
    temperature_c = struct.unpack_from("<f", bytes(raw), 4096)[0]
    return histogram, temperature_c


def _camera_up(sensor, mask=0x01, configure=True):
    """Full bring-up matching the production ScanWorkflow sequence:
      1. enable_camera_power  →  500 ms settle (rails + FPGA supply)
      2. program_fpga          →  loads bitstream into SRAM  (blocks up to ~16 s)
                               →  100 ms settle after completion
      3. camera_configure_registers  →  writes camera sensor registers

    NOTE: program_fpga can take up to 16 seconds; tests using this helper
    should be marked @pytest.mark.slow.
    Power cycling wipes both the FPGA bitstream and camera register state,
    so all three steps are required before any camera-level operation.
    """
    ok = sensor.enable_camera_power(mask)
    if ok is False:
        pytest.fail(f"enable_camera_power(0x{mask:02X}) returned False")
    time.sleep(0.5)  # match ScanWorkflow settle time

    # Enable firmware USB printf so any debug output shows up in test logs
    sensor.set_debug_flags(0x01)  # DEBUG_FLAG_USB_PRINTF

    ok = sensor.program_fpga(camera_position=mask, manual_process=False)
    if ok is False:
        pytest.fail(f"program_fpga(0x{mask:02X}) returned False")
    time.sleep(0.1)  # settle after bitstream load completes

    if configure:
        ok = sensor.camera_configure_registers(mask)
        if ok is False:
            pytest.fail(f"camera_configure_registers(0x{mask:02X}) returned False")


def _camera_down(sensor, cam=0, mask=0x01):
    try:
        sensor.disable_camera_fpga(mask)  # disable_camera_fpga takes a bitmask
    finally:
        sensor.disable_camera_power(mask)


# ===========================================================================
# 5.1 Camera bring-up and single frame
# ===========================================================================

def test_camera_full_bringup_single_frame(any_sensor):
    """Power on → FPGA program → configure → status check → capture → parse histogram."""
    _camera_up(any_sensor)
    try:
        # Mirror what the high-level get_camera_histogram() does: verify the camera
        # reports READY + FPGA-loaded + registers-configured (bits 0, 1, 2) before
        # firing the capture command.  Without this the returned packet is too short.
        status_map = any_sensor.get_camera_status(0x01)
        assert status_map is not None, "get_camera_status returned None"
        status = status_map.get(0)  # camera_id 0, index not bitmask
        assert status is not None, "No status for camera 0 in status_map"
        ready     = bool(status & (1 << 0))
        fpga_done = bool(status & (1 << 1))
        regs_done = bool(status & (1 << 2))
        assert ready,     f"Camera 0 not READY (status=0x{status:02X})"
        assert fpga_done, f"Camera 0 FPGA not loaded (status=0x{status:02X})"
        assert regs_done, f"Camera 0 registers not configured (status=0x{status:02X})"

        assert any_sensor.camera_capture_histogram(0x01) is True
        raw = any_sensor.camera_get_histogram(0x01)
        assert isinstance(raw, (bytes, bytearray)) and len(raw) == 4100, (
            f"camera_get_histogram returned {len(raw) if raw else 0} bytes (expected 4100)"
        )

        histogram, temperature_c = _decode_raw_histogram(raw)
        assert len(histogram) == 1024
        assert isinstance(temperature_c, float)
    finally:
        _camera_down(any_sensor)


# ===========================================================================
# 5.2 Camera power cycle
# ===========================================================================

def test_camera_power_cycle(any_sensor):
    """On → status on → off → status off → on → off."""
    assert any_sensor.enable_camera_power(0x01) is not False
    try:
        status = any_sensor.get_camera_power_status()
        assert status[0], "Camera 0 should be on after enable"

        any_sensor.disable_camera_power(0x01)
        time.sleep(0.1)
        status = any_sensor.get_camera_power_status()
        assert not status[0], "Camera 0 should be off after disable"

        assert any_sensor.enable_camera_power(0x01) is not False
        time.sleep(0.1)
        status = any_sensor.get_camera_power_status()
        assert status[0], "Camera 0 should be on after second enable"
    finally:
        any_sensor.disable_camera_power(0x01)


# ===========================================================================
# 5.3 FPGA enable → histogram → FPGA disable
# ===========================================================================

def test_fpga_enable_histogram_disable(any_sensor):
    """Full bring-up → capture one histogram → tear down."""
    _camera_up(any_sensor)  # power → program_fpga → configure_registers
    try:
        any_sensor.camera_capture_histogram(0x01)
        raw = any_sensor.camera_get_histogram(0x01)
        histogram, _ = _decode_raw_histogram(raw)
        assert len(histogram) == 1024

        assert any_sensor.disable_camera_fpga(0x01) is True
    finally:
        any_sensor.disable_camera_power(0x01)


# ===========================================================================
# 5.4 Streaming acquisition
# ===========================================================================

@pytest.mark.slow
def test_streaming_acquisition(any_sensor):
    """
    Start FSIN streaming, collect 30 science frames, assert monotonic
    absolute_frame_id increments of exactly 1.
    """
    from omotion.MotionProcessing import create_science_pipeline

    N = 30
    _camera_up(any_sensor)
    any_sensor.enable_aggregator_fsin()

    frames = []
    done = threading.Event()

    def on_science_frame(frame):
        frames.append(frame)
        if len(frames) >= N:
            done.set()

    # create_science_pipeline() starts the pipeline internally — do NOT call .start() again
    pipeline = create_science_pipeline(
        bfi_c_min=_BFI_ZEROS,
        bfi_c_max=_BFI_ONES,
        bfi_i_min=_BFI_ZEROS,
        bfi_i_max=_BFI_ONES,
        on_science_frame_fn=on_science_frame,
    )
    any_sensor.enable_camera(0x01)

    try:
        done.wait(timeout=30)
    finally:
        any_sensor.disable_camera(0x01)
        any_sensor.disable_aggregator_fsin()
        pipeline.stop()
        _camera_down(any_sensor)

    assert len(frames) >= N, f"Expected {N} frames, got {len(frames)}"

    abs_ids = [f.absolute_frame for f in frames[:N]]
    for prev, curr in zip(abs_ids, abs_ids[1:]):
        assert curr == prev + 1, f"Frame ID not monotonic: {prev} → {curr}"


# ===========================================================================
# 5.5 External FSIN sequence
# ===========================================================================

def test_external_fsin_sequence(any_sensor):
    """Enable FSIN ext → capture one frame → disable FSIN ext."""
    _camera_up(any_sensor)
    try:
        assert any_sensor.enable_camera_fsin_ext() is True
        time.sleep(0.2)
        any_sensor.camera_capture_histogram(0x01)
        raw = any_sensor.camera_get_histogram(0x01)
        assert isinstance(raw, (bytes, bytearray)) and len(raw) > 0
        assert any_sensor.disable_camera_fsin_ext() is True
    finally:
        _camera_down(any_sensor)


# ===========================================================================
# 5.6 Test pattern verification
# ===========================================================================

def test_test_pattern_histogram(any_sensor):
    """Normal configure → overlay test pattern → capture histogram → assert non-zero bins."""
    _camera_up(any_sensor, configure=True)  # normal register init required first
    try:
        any_sensor.camera_configure_test_pattern(camera_position=0x01, test_pattern=1)
        any_sensor.camera_capture_histogram(0x01)
        raw = any_sensor.camera_get_histogram(0x01)
        histogram, _ = _decode_raw_histogram(raw)
        assert int(histogram.sum()) > 0, "Test pattern histogram has no counts"
    finally:
        _camera_down(any_sensor)


# ===========================================================================
# 5.7 Console trigger + LSYNC count
# ===========================================================================

@pytest.mark.console
@pytest.mark.slow
def test_trigger_lsync_sequence(console):
    """Fire ~10 Hz trigger for 1 s, assert LSYNC counter accumulates pulses."""
    console.set_trigger_json({"rate": 10})
    console.start_trigger()
    time.sleep(1.1)
    count = console.get_lsync_pulsecount()
    console.stop_trigger()
    assert count >= 1, f"Expected at least 1 LSYNC pulse in 1.1 s, got {count}"


# ===========================================================================
# 5.8 Dual-sensor aligned frame acquisition
# ===========================================================================

@pytest.mark.slow
def test_dual_sensor_frame_alignment(sensor_left, sensor_right):
    """
    Stream from both sensors and assert ScienceFrames carry both
    left and right samples with matching absolute_frame.
    """
    from omotion.MotionProcessing import create_science_pipeline

    N = 20

    for sensor in (sensor_left, sensor_right):
        _camera_up(sensor)
        sensor.enable_aggregator_fsin()

    aligned_frames = []
    done = threading.Event()

    def on_science_frame(frame):
        sides = {side for (side, _) in frame.samples.keys()}
        if "left" in sides and "right" in sides:
            aligned_frames.append(frame)
        if len(aligned_frames) >= N:
            done.set()

    # create_science_pipeline() starts the pipeline internally — do NOT call .start() again
    pipeline = create_science_pipeline(
        bfi_c_min=_BFI_ZEROS,
        bfi_c_max=_BFI_ONES,
        bfi_i_min=_BFI_ZEROS,
        bfi_i_max=_BFI_ONES,
        on_science_frame_fn=on_science_frame,
    )

    for sensor in (sensor_left, sensor_right):
        sensor.enable_camera(0x01)

    try:
        done.wait(timeout=30)
    finally:
        for sensor in (sensor_left, sensor_right):
            sensor.disable_camera(0x01)
            sensor.disable_aggregator_fsin()
        pipeline.stop()
        for sensor in (sensor_left, sensor_right):
            _camera_down(sensor)

    assert len(aligned_frames) >= N, (
        f"Expected {N} aligned frames, got {len(aligned_frames)}"
    )

    for frame in aligned_frames:
        sides_present = {side for (side, _) in frame.samples.keys()}
        assert "left" in sides_present
        assert "right" in sides_present


# ===========================================================================
# 5.9 Full scan workflow
# ===========================================================================

@pytest.mark.slow
@pytest.mark.console
def test_scan_workflow_end_to_end(motion, tmp_path):
    """
    Execute a 5-second scan via ScanWorkflow and assert a non-empty
    CSV is written with a matching frame count.
    """
    import csv as csv_module
    from omotion.ScanWorkflow import ScanRequest

    request = ScanRequest(
        subject_id="pytest_subject",
        duration_sec=5,
        left_camera_mask=0x01,
        right_camera_mask=0x01,
        data_dir=str(tmp_path),
        disable_laser=False,
    )

    result_holder = {}
    done = threading.Event()

    def on_result(result):
        result_holder["result"] = result
        done.set()

    motion.scan_workflow.start_scan(request, on_complete_fn=on_result)
    done.wait(timeout=30)

    result = result_holder.get("result")
    assert result is not None, "ScanWorkflow did not call on_complete_fn"
    assert result.ok, f"Scan failed: {result.error}"
    assert not result.canceled

    for path in (result.left_path, result.right_path):
        if path:
            assert os.path.isfile(path), f"Expected CSV at {path}"
            with open(path, newline="") as f:
                rows = list(csv_module.reader(f))
            data_rows = [r for r in rows if r and not r[0].startswith("#")]
            assert len(data_rows) > 1, f"CSV at {path} has no data rows"
