"""Unit tests for MotionConsole.get_boot_mode over normal comms (no DFU).

Mirrors MotionSensor.get_boot_mode, but the console command is OW_CMD_BOOT_INFO
= 0x0B (0x09 is OW_CMD_MESSAGES there). Reply payload is byte-identical, so the
same parse_boot_info handles both. Old firmware NAKs the unknown command, which
must read as UNKNOWN, not raise.
"""
import struct
from unittest.mock import MagicMock

from omotion.boot_mode import BootMode
from omotion.MotionConsole import MotionConsole
from omotion.config import OW_CMD, OW_CMD_BOOT_INFO_CONSOLE, OW_RESP, OW_ERROR


def _make_console(payload=b"", packet_type=OW_RESP, *, connected=True, demo=False):
    console = MotionConsole.__new__(MotionConsole)
    console.uart = MagicMock()
    console.uart.demo_mode = demo
    console.is_connected = MagicMock(return_value=connected)
    resp = MagicMock()
    resp.packetType = packet_type
    resp.data = bytes(payload)
    resp.data_len = len(payload)
    console.uart.send_packet.return_value = resp
    console.uart.clear_buffer = MagicMock()
    return console


def _boot_info(vtor):
    return struct.pack("<B3sII", 1, b"\x00\x00\x00", vtor, vtor)


def test_console_boot_mode_bare_metal():
    c = _make_console(_boot_info(0x08000000))
    assert c.get_boot_mode() is BootMode.BARE_METAL
    kwargs = c.uart.send_packet.call_args.kwargs
    assert kwargs["packetType"] == OW_CMD
    assert kwargs["command"] == OW_CMD_BOOT_INFO_CONSOLE  # 0x0B, not the sensor's 0x09


def test_console_boot_mode_bootloader():
    c = _make_console(_boot_info(0x08020400))
    assert c.get_boot_mode() is BootMode.BOOTLOADER


def test_console_boot_mode_old_firmware_error_is_unknown():
    c = _make_console(b"", packet_type=OW_ERROR)
    assert c.get_boot_mode() is BootMode.UNKNOWN


def test_console_boot_mode_not_connected_is_unknown():
    c = _make_console(_boot_info(0x08020400), connected=False)
    assert c.get_boot_mode() is BootMode.UNKNOWN


def test_console_boot_mode_garbled_is_unknown():
    c = _make_console(b"\x01\x02\x03")
    assert c.get_boot_mode() is BootMode.UNKNOWN


def test_console_boot_mode_demo_is_bare_metal():
    c = _make_console(demo=True)
    c.uart.send_packet.side_effect = AssertionError("must not hit the wire in demo mode")
    assert c.get_boot_mode() is BootMode.BARE_METAL
