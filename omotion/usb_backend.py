# omotion/usb_backend.py
import os
import sys
import platform
import ctypes
from pathlib import Path


def _is_win():
    return sys.platform == "win32"


def _base_dir() -> Path:
    # In PyInstaller one-file/one-dir builds, sys._MEIPASS points to the temp dir
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).parent


def _dll_dir() -> Path | None:
    if not _is_win():
        return None
    arch = platform.machine().lower()
    sub = "x64" if arch in ("amd64", "x86_64") else "x86"
    # Look inside the package vendor path (works for source & frozen)
    p = _base_dir() / "_vendor" / "libusb" / "windows" / sub
    if p.exists():
        return p
    # Fallback: next to the EXE in a flat COLLECT layout
    return _base_dir()


# Module-cached libusb backend. Reused across enumeration calls so we don't
# pay libusb_init/exit on every 200 ms poll sweep; dropped (not force-disposed)
# on USB hotplug via invalidate_libusb1_backend() — see issue #139.
_cached_backend = None


def get_libusb1_backend():
    """Return a libusb1 backend, building one on first use and after each
    invalidation. Callers must not cache the result across a hotplug — always
    go back through this function (the SDK's enumeration paths already do)."""
    global _cached_backend
    if _cached_backend is not None:
        return _cached_backend

    import usb.backend.libusb1 as libusb1

    # Force PyUSB to build a NEW libusb context rather than handing back its
    # own process-wide cached singleton. That singleton's device enumeration
    # goes stale on Windows after repeated USB hotplug churn (issue #139): a
    # long-lived context stops reflecting sensor plug/unplug, so a sensor
    # moved between ports keeps its old left/right label until the process
    # restarts. We build a fresh context, cache it ourselves, and refresh it
    # on hotplug via invalidate_libusb1_backend().
    libusb1._lib_object = None

    if _is_win():
        dll_dir = _dll_dir()
        dll_path = dll_dir / "libusb-1.0.dll"
        if not dll_path.exists():
            raise FileNotFoundError(f"Vendored libusb not found: {dll_path}")

        try:
            os.add_dll_directory(str(dll_dir))  # Python 3.8+ on Windows
        except Exception:
            pass

        ctypes.CDLL(str(dll_path))  # preload for clearer errors
        _cached_backend = libusb1.get_backend(
            find_library=lambda _: str(dll_path)
        )
    else:
        # Non-Windows: use system libusb via the loader
        _cached_backend = libusb1.get_backend()

    return _cached_backend


def invalidate_libusb1_backend():
    """Drop the cached libusb backend so the next :func:`get_libusb1_backend`
    call builds a fresh context. Called on USB hotplug.

    A long-lived libusb context's device list goes stale on Windows after
    repeated plug/unplug churn (issue #139): enumeration stops reflecting
    reality, so a sensor moved between ports keeps its old left/right label.

    We only drop our reference here — we never call ``libusb_exit``. A sensor
    handle still open on the old context keeps working (its open ``Device``
    holds the backend alive by refcount), and that context is disposed
    naturally once its connection closes. The next enumeration builds a fresh
    context that sees the current topology.
    """
    global _cached_backend
    _cached_backend = None
