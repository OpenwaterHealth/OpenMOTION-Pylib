"""Unit tests for CommInterface OUT-endpoint stall recovery (#189).

The EPIPE branch in ``CommInterface.write`` called ``usb.util.clear_halt``,
which does not exist in pyusb — ``clear_halt`` is a method on
``usb.core.Device``. Every stall therefore raised ``AttributeError``, which
the surrounding broad ``except Exception`` caught and relabelled
"clear_halt recovery failed", so the recovery has never run since SDK 1.5.0.

Only ``test_stall_clears_halt_with_endpoint_address`` and
``test_non_usberror_from_clear_halt_is_not_swallowed`` fail against pre-fix
code. ``test_stall_does_not_resend_the_packet`` is a forward regression
fence: it passes today because the AttributeError aborts before the re-send
at the old line 240 could run.

The device mock is ``spec=usb.core.Device`` on purpose. A bare MagicMock
accepts any invented method name, so a test written against one would have
passed just as green against the very defect these tests exist to catch.
"""

from unittest.mock import MagicMock

import pytest
import usb.core

from omotion.CommInterface import CommInterface

pytestmark = pytest.mark.unit

_EP_OUT_ADDR = 0x01


def _make_comm(write_side_effect) -> CommInterface:
    """Build a CommInterface over a fake device, bypassing claim()."""
    dev = MagicMock(spec=usb.core.Device)  # spec: a phantom name cannot pass
    dev.write.side_effect = write_side_effect
    ci = CommInterface(dev, interface_index=0, desc="TEST", async_mode=False)
    ci.ep_out = MagicMock(bEndpointAddress=_EP_OUT_ADDR)
    ci.ep_in = MagicMock(bEndpointAddress=0x81, wMaxPacketSize=64)
    return ci


def _epipe() -> usb.core.USBError:
    """A stalled OUT endpoint as pyusb reports it.

    pyusb's USBError(strerror, backend_error_code, errno) puts the libusb
    code in ``backend_error_code`` and the POSIX errno in ``errno``, and
    ``libusb1._libusb_errno[-9]`` is 32 on every platform — so
    LIBUSB_ERROR_PIPE always arrives as errno 32.
    """
    return usb.core.USBError("pipe error", -9, 32)


def test_stall_clears_halt_with_endpoint_address():
    """The regression: the recovery must actually reach the device."""
    ci = _make_comm(_epipe())

    with pytest.raises(usb.core.USBError):
        ci.write(b"\xaa\x00")

    ci.dev.clear_halt.assert_called_once_with(_EP_OUT_ADDR)


def test_stall_does_not_resend_the_packet():
    """At-most-once delivery: no duplicate command reaches the firmware.

    Forward regression fence — passes pre-fix too (see module docstring).
    """
    ci = _make_comm(_epipe())

    with pytest.raises(usb.core.USBError):
        ci.write(b"\xaa\x00")

    assert ci.dev.write.call_count == 1, (
        "stalled packet was re-sent — the sensor firmware has no id dedup "
        "(uart_comms.c:331), so a duplicate double-executes the command"
    )


def test_clear_halt_failure_does_not_mask_the_original_epipe():
    """A device that refuses CLEAR_HALT must not swallow the original error.

    Common after teardown: dispose_resources() has already released the
    interface, so libusb returns NOT_FOUND.
    """
    ci = _make_comm(_epipe())
    ci.dev.clear_halt.side_effect = usb.core.USBError("entity not found", -5, 2)

    with pytest.raises(usb.core.USBError) as excinfo:
        ci.write(b"\xaa\x00")

    assert excinfo.value.errno == 32, "the original EPIPE must be the one raised"


def test_non_usberror_from_clear_halt_is_not_swallowed():
    """Guards the narrowed except: a coding defect in the recovery path
    (the exact shape of #189) must surface, not read as a device fault."""
    ci = _make_comm(_epipe())
    ci.dev.clear_halt.side_effect = AttributeError("boom")

    with pytest.raises(AttributeError):
        ci.write(b"\xaa\x00")


def test_enodev_is_not_treated_as_a_stall():
    """errno 19 (No such device) must not enter the clear_halt branch."""
    ci = _make_comm(usb.core.USBError("No such device", -4, 19))

    with pytest.raises(usb.core.USBError):
        ci.write(b"\xaa\x00")

    ci.dev.clear_halt.assert_not_called()


def test_timeout_path_still_retries_and_is_unaffected():
    """Regression fence: the EPIPE change must not disturb the pre-existing
    write-timeout back-off loop."""
    ci = _make_comm([usb.core.USBTimeoutError("timeout", -7, 110), 2])

    assert ci.write(b"\xaa\x00", _retries=1) == 2
    assert ci.dev.write.call_count == 2
    ci.dev.clear_halt.assert_not_called()
