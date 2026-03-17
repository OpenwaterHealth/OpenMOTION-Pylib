"""
Sensor module tests (Section 3 of the test plan).

Tests are parametrised over the 'any_sensor' fixture so they run
against both left and right sensors automatically.  Side-specific
tests use the 'sensor_left' / 'sensor_right' fixtures directly.
"""

import math
import struct
import time

import numpy as np
import pytest

pytestmark = pytest.mark.sensor



# ===========================================================================
# 3.1 Basic connectivity
# ===========================================================================

def test_sensor_ping(any_sensor):
    assert any_sensor.ping() is True


def test_sensor_version(any_sensor):
    import re
    v = any_sensor.get_version()
    assert isinstance(v, str) and len(v) > 0
    assert re.match(r"\d+\.\d+\.\d+", v), f"Unexpected version format: {v!r}"


def test_sensor_hardware_id(any_sensor):
    hw = any_sensor.get_hardware_id()
    assert isinstance(hw, str) and len(hw) > 0


def test_sensor_echo(any_sensor):
    payload = b"test"
    data, length = any_sensor.echo(payload)
    assert data == payload
    assert length == len(payload)


def test_sensor_toggle_led(any_sensor):
    assert any_sensor.toggle_led() is True
    assert any_sensor.toggle_led() is True


# ===========================================================================
# 3.2 IMU
# ===========================================================================

@pytest.fixture(scope="function", autouse=False)
def imu_enabled(any_sensor):
    """Power the IMU on for the duration of one test, then turn it off.

    Function-scoped so the IMU is explicitly disabled after each test that
    needs it, preventing the enabled state from leaking into unrelated tests.
    """
    any_sensor.imu_init()
    any_sensor.imu_on()
    yield
    try:
        any_sensor.imu_off()
    except Exception:
        pass


def test_imu_temperature(any_sensor):
    t = any_sensor.imu_get_temperature()
    assert isinstance(t, float)
    assert -40.0 <= t <= 85.0, f"IMU temperature {t} °C out of physical range"


def test_imu_accelerometer(any_sensor, imu_enabled):
    accel = any_sensor.imu_get_accelerometer()
    assert isinstance(accel, list) and len(accel) == 3
    for v in accel:
        assert isinstance(v, int)
    magnitude = math.sqrt(sum(v ** 2 for v in accel))
    assert magnitude > 0, "Accelerometer magnitude is zero — sensor may be unresponsive"


def test_imu_gyroscope(any_sensor, imu_enabled):
    gyro = any_sensor.imu_get_gyroscope()
    assert isinstance(gyro, list) and len(gyro) == 3
    for v in gyro:
        assert isinstance(v, int)
        # Raw LSB values depend on firmware full-scale range (~16000 for this hardware)
        assert -32768 <= v <= 32767, f"Gyro axis {v} out of signed 16-bit range"


# ===========================================================================
# 3.3 Fan control
# ===========================================================================

def test_sensor_fan_on(any_sensor):
    assert any_sensor.set_fan_control(True) is True


def test_sensor_fan_off(any_sensor):
    assert any_sensor.set_fan_control(False) is True


def test_sensor_fan_status_roundtrip(any_sensor):
    any_sensor.set_fan_control(True)
    assert any_sensor.get_fan_control_status() is True
    any_sensor.set_fan_control(False)
    assert any_sensor.get_fan_control_status() is False


# ===========================================================================
# 3.4 Debug flags
# ===========================================================================

def test_debug_flags_roundtrip(any_sensor):
    original = any_sensor.get_debug_flags()
    try:
        any_sensor.set_debug_flags(0x03)
        readback = any_sensor.get_debug_flags()
        assert readback == 0x03, f"Debug flags readback {readback:#04x}, expected 0x03"
    finally:
        any_sensor.set_debug_flags(original)


# ===========================================================================
# 3.5 Camera power
# ===========================================================================

def test_camera_power_on_off(any_sensor):
    assert any_sensor.enable_camera_power(0xFF) is True
    time.sleep(0.1)
    assert any_sensor.disable_camera_power(0xFF) is True


def test_camera_power_status(any_sensor):
    any_sensor.enable_camera_power(0x01)
    try:
        status = any_sensor.get_camera_power_status()
        assert isinstance(status, list) and len(status) > 0
        assert status[0], "Camera 0 should be powered on"
    finally:
        any_sensor.disable_camera_power(0x01)


def test_camera_power_selective(any_sensor):
    any_sensor.enable_camera_power(0x01)
    try:
        status = any_sensor.get_camera_power_status()
        assert status[0], "Camera 0 should be on"
        if len(status) > 1:
            assert not status[1], "Camera 1 should be off"
    finally:
        any_sensor.disable_camera_power(0x01)


# ===========================================================================
# 3.6 FPGA control
# ===========================================================================

def _power_up(sensor, mask=0x01):
    """Power on cameras and fail fast if enable_camera_power returns False."""
    ok = sensor.enable_camera_power(mask)
    if ok is False:
        pytest.fail(f"enable_camera_power(0x{mask:02X}) returned False")
    time.sleep(0.5)  # match ScanWorkflow settle time


@pytest.mark.slow
def test_fpga_enable_disable(any_sensor):
    _power_up(any_sensor)
    try:
        assert any_sensor.enable_camera_fpga(0x01) is True
        assert any_sensor.disable_camera_fpga(0x01) is True
    finally:
        any_sensor.disable_camera_power(0x01)


@pytest.mark.slow
def test_fpga_check_after_enable(any_sensor):
    _power_up(any_sensor)
    try:
        assert any_sensor.enable_camera_fpga(0x01) is True
        any_sensor.camera_configure_registers(0x01)
        assert any_sensor.check_camera_fpga(0x01) is True
    finally:
        any_sensor.disable_camera_fpga(0x01)
        any_sensor.disable_camera_power(0x01)


@pytest.mark.slow
def test_fpga_status(any_sensor):
    _power_up(any_sensor)
    try:
        assert any_sensor.enable_camera_fpga(0x01) is True
        any_sensor.camera_configure_registers(0x01)
        result = any_sensor.get_status_fpga(0x01)
        assert result is not None
    finally:
        any_sensor.disable_camera_fpga(0x01)
        any_sensor.disable_camera_power(0x01)


@pytest.mark.slow
def test_fpga_usercode(any_sensor):
    _power_up(any_sensor)
    try:
        assert any_sensor.enable_camera_fpga(0x01) is True
        any_sensor.camera_configure_registers(0x01)
        result = any_sensor.get_usercode_fpga(0x01)
        assert result is not None
    finally:
        any_sensor.disable_camera_fpga(0x01)
        any_sensor.disable_camera_power(0x01)


@pytest.mark.slow
def test_fpga_activate(any_sensor):
    _power_up(any_sensor)
    try:
        assert any_sensor.enable_camera_fpga(0x01) is True
        any_sensor.camera_configure_registers(0x01)
        assert any_sensor.activate_camera_fpga(0x01) is True
    finally:
        any_sensor.disable_camera_fpga(0x01)
        any_sensor.disable_camera_power(0x01)


@pytest.mark.slow
def test_fpga_reset(any_sensor):
    """Reset is called after full bring-up; power-cycle wipes FPGA + registers."""
    _bring_up_camera(any_sensor)
    try:
        assert any_sensor.reset_camera_sensor(0x01) is True
    finally:
        any_sensor.disable_camera_fpga(0x01)
        any_sensor.disable_camera_power(0x01)


# ===========================================================================
# 3.7 Camera configuration
# ===========================================================================

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


def _bring_up_camera(sensor, mask=0x01, configure=True):
    """Full camera bring-up matching the production ScanWorkflow sequence:
      1. enable_camera_power  →  500 ms settle (rails + FPGA supply)
      2. program_fpga          →  loads bitstream into SRAM  (blocks up to ~16 s)
                               →  100 ms settle after completion
      3. camera_configure_registers  →  writes camera sensor registers

    NOTE: program_fpga can take up to 16 seconds; any test calling this
    helper will be inherently slow and should be marked @pytest.mark.slow.
    Power cycling wipes both the FPGA bitstream and camera register state,
    so all three steps are required before any camera-level operation.
    """
    ok = sensor.enable_camera_power(mask)
    if ok is False:
        pytest.fail(f"enable_camera_power(0x{mask:02X}) returned False")
    time.sleep(0.5)  # match ScanWorkflow settle time

    ok = sensor.program_fpga(camera_position=mask, manual_process=False)
    if ok is False:
        pytest.fail(f"program_fpga(0x{mask:02X}) returned False")
    time.sleep(0.1)  # settle after bitstream load completes

    if configure:
        ok = sensor.camera_configure_registers(mask)
        if ok is False:
            pytest.fail(f"camera_configure_registers(0x{mask:02X}) returned False")


def _tear_down_camera(sensor, cam=0, mask=0x01):
    try:
        sensor.disable_camera_fpga(mask)  # disable_camera_fpga takes a bitmask
    finally:
        sensor.disable_camera_power(mask)


@pytest.mark.slow
def test_camera_configure_registers(any_sensor):
    _bring_up_camera(any_sensor)
    try:
        assert any_sensor.camera_configure_registers(0x01) is True
    finally:
        _tear_down_camera(any_sensor)


@pytest.mark.slow
def test_camera_configure_test_pattern(any_sensor):
    """Normal configure first, then overlay test pattern — FPGA must be loaded."""
    _bring_up_camera(any_sensor)
    try:
        assert any_sensor.camera_configure_test_pattern(camera_position=0x01, test_pattern=1) is True
    finally:
        _tear_down_camera(any_sensor)


@pytest.mark.slow
def test_camera_status(any_sensor):
    _bring_up_camera(any_sensor)
    try:
        status = any_sensor.get_camera_status(0x01)
        assert status is not None
    finally:
        _tear_down_camera(any_sensor)


@pytest.mark.slow
def test_camera_security_uid(any_sensor):
    _bring_up_camera(any_sensor)
    try:
        uid = any_sensor.read_camera_security_uid(0)
        assert isinstance(uid, (bytes, bytearray)) and len(uid) > 0
    finally:
        _tear_down_camera(any_sensor)


@pytest.mark.slow
def test_cached_security_uid(any_sensor):
    _bring_up_camera(any_sensor)
    try:
        any_sensor.refresh_id_cache()
        uid_str = any_sensor.get_cached_camera_security_uid(0)
        assert isinstance(uid_str, str) and len(uid_str) > 0
    finally:
        any_sensor.clear_id_cache()
        _tear_down_camera(any_sensor)


def test_camera_switch(any_sensor):
    any_sensor.switch_camera(1)
    any_sensor.switch_camera(0)


# ===========================================================================
# 3.8 Frame sync
# ===========================================================================

@pytest.mark.slow
def test_fsin_enable_disable(any_sensor):
    """FSIN aggregator requires full bring-up: power → FPGA → configure."""
    _bring_up_camera(any_sensor)
    try:
        assert any_sensor.enable_aggregator_fsin() is True
        assert any_sensor.disable_aggregator_fsin() is True
    finally:
        _tear_down_camera(any_sensor)


@pytest.mark.slow
def test_fsin_external_enable_disable(any_sensor):
    """External FSIN requires full bring-up: power → FPGA → configure."""
    _bring_up_camera(any_sensor)
    try:
        assert any_sensor.enable_camera_fsin_ext() is True
        assert any_sensor.disable_camera_fsin_ext() is True
    finally:
        _tear_down_camera(any_sensor)


@pytest.mark.slow
def test_camera_stream_enable_disable(any_sensor):
    """Streaming requires full bring-up: power → FPGA → configure."""
    _bring_up_camera(any_sensor)
    try:
        assert any_sensor.enable_camera(0x01) is True
        assert any_sensor.disable_camera(0x01) is True
    finally:
        _tear_down_camera(any_sensor)


# ===========================================================================
# 3.9 Single-frame histogram capture
# ===========================================================================

@pytest.mark.slow
def test_single_histogram_raw_bytes(any_sensor):
    """Full bring-up: power → program_fpga → configure → FSIN → capture → get histogram."""
    _bring_up_camera(any_sensor)
    any_sensor.enable_aggregator_fsin()
    try:
        assert any_sensor.camera_capture_histogram(0x01) is True
        raw = any_sensor.camera_get_histogram(0x01)
        assert isinstance(raw, (bytes, bytearray))
        assert len(raw) == 4100, f"Expected 4100 bytes, got {len(raw)}"
    finally:
        any_sensor.disable_aggregator_fsin()
        _tear_down_camera(any_sensor)


@pytest.mark.slow
def test_single_histogram_parsed(any_sensor):
    _bring_up_camera(any_sensor)
    any_sensor.enable_aggregator_fsin()
    try:
        any_sensor.camera_capture_histogram(0x01)
        raw = any_sensor.camera_get_histogram(0x01)
        assert raw is not None and len(raw) == 4100, (
            f"camera_get_histogram returned {len(raw) if raw else 0} bytes "
            "(expected 4100) — check FSIN / FPGA state"
        )
        histogram, temperature_c = _decode_raw_histogram(raw)
        assert len(histogram) == 1024
        assert histogram.dtype == np.uint32
        assert isinstance(temperature_c, float)
    finally:
        any_sensor.disable_aggregator_fsin()
        _tear_down_camera(any_sensor)


