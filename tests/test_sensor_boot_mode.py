"""Unit tests for MotionSensor.get_boot_mode over normal comms (no DFU).

Sends OW_CMD_BOOT_INFO and classifies the reply. Old firmware NAKs the unknown
command, which must read as UNKNOWN, not raise.
"""
import struct
from types import SimpleNamespace
from unittest.mock import MagicMock

from omotion.boot_mode import BootMode
from omotion.MotionSensor import MotionSensor
from omotion.config import OW_CMD, OW_CMD_BOOT_INFO, OW_RESP, OW_UNKNOWN


def _make_sensor(response):
    s = MotionSensor.__new__(MotionSensor)
    s._send = MagicMock(return_value=response)
    s.demo_mode = False
    return s


def _resp(data=b"", packet_type=OW_RESP):
    data = bytes(data)
    return SimpleNamespace(packetType=packet_type, data=data, data_len=len(data))


def _boot_info(vtor):
    return struct.pack("<B3sII", 1, b"\x00\x00\x00", vtor, vtor)


def test_get_boot_mode_bare_metal():
    sensor = _make_sensor(_resp(_boot_info(0x08000000)))
    assert sensor.get_boot_mode() is BootMode.BARE_METAL
    kwargs = sensor._send.call_args.kwargs
    assert kwargs["packetType"] == OW_CMD
    assert kwargs["command"] == OW_CMD_BOOT_INFO


def test_get_boot_mode_bootloader():
    sensor = _make_sensor(_resp(_boot_info(0x08020400)))
    assert sensor.get_boot_mode() is BootMode.BOOTLOADER


def test_get_boot_mode_old_firmware_naks_unknown_command():
    """Firmware without 0x09 replies OW_UNKNOWN — read as UNKNOWN, don't raise."""
    sensor = _make_sensor(_resp(b"", packet_type=OW_UNKNOWN))
    assert sensor.get_boot_mode() is BootMode.UNKNOWN


def test_get_boot_mode_no_response_is_unknown():
    sensor = _make_sensor(None)
    assert sensor.get_boot_mode() is BootMode.UNKNOWN


def test_get_boot_mode_garbled_payload_is_unknown():
    sensor = _make_sensor(_resp(b"\x01\x02"))
    assert sensor.get_boot_mode() is BootMode.UNKNOWN


def test_get_boot_mode_demo_mode_is_bare_metal():
    s = MotionSensor.__new__(MotionSensor)
    s.demo_mode = True
    s._send = MagicMock(side_effect=AssertionError("must not hit the wire in demo mode"))
    assert s.get_boot_mode() is BootMode.BARE_METAL
