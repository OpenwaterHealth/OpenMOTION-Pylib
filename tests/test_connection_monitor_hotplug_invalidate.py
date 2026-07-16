"""Issue #139: a USB hotplug event must invalidate the cached libusb backend
before the monitor re-enumerates, so the sweep runs on a fresh context that
reflects the new topology (a long-lived context goes stale on Windows after
plug/unplug churn).

No hardware: the sweep itself is stubbed and invalidate is spied on.
"""

from unittest.mock import MagicMock

import pytest

import omotion.usb_backend as usb_backend
from omotion.connection_monitor import ConnectionMonitor, HotplugWake, PollArrived

pytestmark = pytest.mark.unit


def _make_monitor():
    return ConnectionMonitor(
        console=MagicMock(name="console"),
        left=MagicMock(name="left"),
        right=MagicMock(name="right"),
        console_vid=0x0483,
        console_pid=0xA53E,
        sensor_vid=0x0483,
        sensor_pid=0x5A5A,
        hotplug=None,
    )


def test_hotplug_invalidates_backend_then_sweeps(monkeypatch):
    monitor = _make_monitor()

    calls = []
    monkeypatch.setattr(
        usb_backend, "invalidate_libusb1_backend",
        lambda: calls.append("invalidate"),
    )
    monkeypatch.setattr(monitor, "_poll_sweep", lambda: calls.append("sweep"))

    monitor._dispatch(HotplugWake())

    # Invalidate must happen, and it must happen BEFORE the sweep — otherwise
    # the sweep re-enumerates on the stale context we were trying to refresh.
    assert calls == ["invalidate", "sweep"]


def test_non_hotplug_events_do_not_invalidate(monkeypatch):
    monitor = _make_monitor()

    calls = []
    monkeypatch.setattr(
        usb_backend, "invalidate_libusb1_backend",
        lambda: calls.append("invalidate"),
    )
    # A routed handle event must not touch the backend cache.
    monitor._dispatch(PollArrived(handle_name="left"))

    assert calls == []
