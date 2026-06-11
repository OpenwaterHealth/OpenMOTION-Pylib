"""Software-only tests for the MotionSensor IMU streaming guards.

Background (2026-06-11 bench wedge, fw 1.6.1-dev.1): OW_IMU_ON starts a
200 Hz timer ISR in sensor firmware that polls the ICM20948 over I2C and
printf()s on any error. With DEBUG_FLAG_USB_PRINTF enabled, a printf from
that ISR enters the USB command-endpoint log path and busy-waits on a
transmit-complete interrupt that can never preempt it (equal NVIC
priority) — the CPU never leaves the ISR and the sensor's command
interface is dead until power cycle. Register polls (OW_IMU_GET_*) while
the timer runs additionally race the ISR on the shared I2C bus.

These tests pin the SDK-side guards:
  * imu_on() clears DEBUG_FLAG_USB_PRINTF *before* sending OW_IMU_ON.
  * imu_on() starts the host-side IF2 stream reader before OW_IMU_ON.
  * imu_get_temperature/accelerometer/gyroscope raise while streaming.
  * imu_off() stops the reader, restores the printf flag, re-allows polls.
  * imu_init() reports failure when firmware answers OW_UNKNOWN.

No hardware required: the comm interface and IF2 stream reader are faked.
"""

import queue
import struct

import pytest

from omotion.MotionSensor import MotionSensor
from omotion.config import (
    DEBUG_FLAG_USB_PRINTF,
    OW_CMD_DEBUG_FLAGS,
    OW_ERROR,
    OW_IMU_GET_ACCEL,
    OW_IMU_GET_GYRO,
    OW_IMU_GET_TEMP,
    OW_IMU_INIT,
    OW_IMU_OFF,
    OW_IMU_ON,
    OW_RESP,
    OW_UNKNOWN,
)


class _FakeResponse:
    def __init__(self, packetType=OW_RESP, data=b""):
        self.packetType = packetType
        self.data = data
        self.data_len = len(data)


class _FakeComm:
    """Records every packet sent and answers like fw 1.6.x."""

    def __init__(self, debug_flags=0, imu_on_fails=False):
        self.debug_flags = debug_flags
        self.imu_on_fails = imu_on_fails
        self.sent = []  # (command, reserved, data) in send order

    def send_packet(self, id=None, **kw):
        command = kw.get("command")
        reserved = kw.get("reserved", 0)
        data = kw.get("data")
        self.sent.append((command, reserved, data))

        if command == OW_CMD_DEBUG_FLAGS:
            if reserved == 1:  # set
                self.debug_flags = struct.unpack("<I", data)[0]
            return _FakeResponse(data=struct.pack("<I", self.debug_flags))
        if command == OW_IMU_INIT:
            # fw <= 1.6.x does not implement OW_IMU_INIT
            return _FakeResponse(packetType=OW_UNKNOWN)
        if command == OW_IMU_ON:
            if self.imu_on_fails:
                return _FakeResponse(packetType=OW_ERROR)
            return _FakeResponse()
        if command == OW_IMU_OFF:
            return _FakeResponse()
        if command == OW_IMU_GET_TEMP:
            return _FakeResponse(data=struct.pack("<f", 31.5))
        if command in (OW_IMU_GET_ACCEL, OW_IMU_GET_GYRO):
            return _FakeResponse(data=struct.pack("<hhh", 1, 2, 3))
        return _FakeResponse()

    def commands_sent(self):
        return [c for (c, _, _) in self.sent]


class _FakeImuStream:
    def __init__(self):
        self.isStreaming = False
        self.start_calls = 0
        self.stop_calls = 0

    def start_streaming(self, queue_obj, expected_size):
        self.isStreaming = True
        self.start_calls += 1

    def stop_streaming(self):
        self.isStreaming = False
        self.stop_calls += 1


class _FakeComposite:
    def __init__(self, comm):
        self.comm = comm
        self.imu = _FakeImuStream()


def _make_sensor(debug_flags=0, imu_on_fails=False, monkeypatch=None):
    sensor = MotionSensor("right", vid=0x0483, pid=0x5A5A)
    comm = _FakeComm(debug_flags=debug_flags, imu_on_fails=imu_on_fails)
    sensor.uart = _FakeComposite(comm)
    if monkeypatch is not None:
        # Skip the 100 ms power-on settle in unit tests. MotionSensor calls
        # time.sleep via the global time module, so patch it there.
        import time as _time

        monkeypatch.setattr(_time, "sleep", lambda s: None)
    return sensor, comm


# ── imu_on guards ────────────────────────────────────────────────────────


def test_imu_on_clears_usb_printf_before_starting(monkeypatch):
    sensor, comm = _make_sensor(
        debug_flags=DEBUG_FLAG_USB_PRINTF, monkeypatch=monkeypatch
    )
    assert sensor.imu_on() is True
    cmds = comm.commands_sent()
    # The set-flags write must happen before OW_IMU_ON hits the wire.
    set_idx = [
        i for i, (c, r, _) in enumerate(comm.sent)
        if c == OW_CMD_DEBUG_FLAGS and r == 1
    ]
    assert set_idx, "imu_on never cleared the USB printf debug flag"
    assert set_idx[0] < cmds.index(OW_IMU_ON)
    assert comm.debug_flags & DEBUG_FLAG_USB_PRINTF == 0


def test_imu_on_skips_flag_write_when_printf_already_off(monkeypatch):
    sensor, comm = _make_sensor(debug_flags=0, monkeypatch=monkeypatch)
    assert sensor.imu_on() is True
    assert not any(
        c == OW_CMD_DEBUG_FLAGS and r == 1 for (c, r, _) in comm.sent
    )


def test_imu_on_starts_stream_reader_before_firmware(monkeypatch):
    sensor, comm = _make_sensor(monkeypatch=monkeypatch)

    streaming_when_on_sent = []
    orig = comm.send_packet

    def spy(id=None, **kw):
        if kw.get("command") == OW_IMU_ON:
            streaming_when_on_sent.append(sensor.uart.imu.isStreaming)
        return orig(id=id, **kw)

    comm.send_packet = spy
    assert sensor.imu_on() is True
    assert streaming_when_on_sent == [True]
    assert isinstance(sensor.imu_queue, queue.Queue)


def test_imu_on_failure_rolls_back_reader_and_polling(monkeypatch):
    sensor, comm = _make_sensor(imu_on_fails=True, monkeypatch=monkeypatch)
    assert sensor.imu_on() is False
    assert sensor.uart.imu.isStreaming is False
    assert sensor.imu_queue is None
    # Polling must still be allowed — streaming never started.
    assert sensor.imu_get_temperature() == 31.5


# ── polling guards while streaming ───────────────────────────────────────


@pytest.mark.parametrize(
    "method",
    ["imu_get_temperature", "imu_get_accelerometer", "imu_get_gyroscope"],
)
def test_imu_polls_raise_while_streaming(method, monkeypatch):
    sensor, comm = _make_sensor(monkeypatch=monkeypatch)
    assert sensor.imu_on() is True
    with pytest.raises(RuntimeError, match="imu_off"):
        getattr(sensor, method)()
    # The guard must trip before anything reaches the wire.
    assert not any(
        c in (OW_IMU_GET_TEMP, OW_IMU_GET_ACCEL, OW_IMU_GET_GYRO)
        for c in comm.commands_sent()
    )


def test_imu_polls_work_when_not_streaming():
    sensor, comm = _make_sensor()
    assert sensor.imu_get_temperature() == 31.5
    assert sensor.imu_get_accelerometer() == [1, 2, 3]
    assert sensor.imu_get_gyroscope() == [1, 2, 3]


# ── imu_off restores state ───────────────────────────────────────────────


def test_imu_off_stops_reader_and_restores_printf(monkeypatch):
    sensor, comm = _make_sensor(
        debug_flags=DEBUG_FLAG_USB_PRINTF, monkeypatch=monkeypatch
    )
    assert sensor.imu_on() is True
    assert comm.debug_flags & DEBUG_FLAG_USB_PRINTF == 0
    assert sensor.imu_off() is True
    assert sensor.uart.imu.isStreaming is False
    assert sensor.uart.imu.stop_calls == 1
    assert sensor.imu_queue is None
    # Flag suspended by imu_on comes back after imu_off.
    assert comm.debug_flags & DEBUG_FLAG_USB_PRINTF
    # Polling is allowed again.
    assert sensor.imu_get_temperature() == 31.5


def test_imu_off_does_not_enable_printf_it_never_suspended(monkeypatch):
    sensor, comm = _make_sensor(debug_flags=0, monkeypatch=monkeypatch)
    assert sensor.imu_on() is True
    assert sensor.imu_off() is True
    assert comm.debug_flags & DEBUG_FLAG_USB_PRINTF == 0


# ── imu_init honesty ─────────────────────────────────────────────────────


def test_imu_init_reports_unimplemented_firmware_command():
    sensor, comm = _make_sensor()
    assert sensor.imu_init() is False
