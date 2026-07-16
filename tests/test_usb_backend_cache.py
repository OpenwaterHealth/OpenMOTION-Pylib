"""Issue #139: the libusb backend must be cached across enumeration calls
(so we don't churn libusb_init/exit on every 200 ms poll) but rebuilt from a
fresh context after invalidate_libusb1_backend() (called on USB hotplug),
because a long-lived libusb context's device list goes stale on Windows after
repeated plug/unplug churn.

Pure-logic tests: usb.backend.libusb1.get_backend is mocked, so no real
libusb context is created and no hardware or DLL is touched.
"""

import pytest

import usb.backend.libusb1 as libusb1

import omotion.usb_backend as usb_backend

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_cache_and_backend(monkeypatch):
    # Force the OS-independent (non-Windows) branch so the test never loads
    # the vendored DLL; the caching logic under test is identical on both.
    monkeypatch.setattr(usb_backend, "_is_win", lambda: False)
    # Each get_backend() call returns a distinct sentinel so "same object"
    # vs "new object" is observable.
    monkeypatch.setattr(
        libusb1, "get_backend",
        lambda *a, **k: object(),
    )
    # Don't leak our None-poking into other tests' view of PyUSB.
    monkeypatch.setattr(libusb1, "_lib_object", None, raising=False)
    # Start every test with an empty module cache.
    usb_backend._cached_backend = None
    yield
    usb_backend._cached_backend = None


def test_backend_is_cached_across_calls():
    first = usb_backend.get_libusb1_backend()
    second = usb_backend.get_libusb1_backend()
    assert first is second, "repeated calls must reuse the cached context"


def test_invalidate_forces_a_fresh_backend():
    first = usb_backend.get_libusb1_backend()
    usb_backend.invalidate_libusb1_backend()
    second = usb_backend.get_libusb1_backend()
    assert first is not second, "invalidate must rebuild a fresh context"


def test_calls_after_invalidate_recache():
    usb_backend.get_libusb1_backend()
    usb_backend.invalidate_libusb1_backend()
    a = usb_backend.get_libusb1_backend()
    b = usb_backend.get_libusb1_backend()
    assert a is b, "a rebuilt context must itself be cached until next invalidate"
