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


def get_libusb1_backend():
    import usb.backend.libusb1 as libusb1

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
        return libusb1.get_backend(find_library=lambda _: str(dll_path))

    # Non-Windows: use system libusb via the loader
    backend = libusb1.get_backend()
    if backend is not None:
        return backend

    # ctypes.util.find_library misses Homebrew's /opt/homebrew prefix on
    # Apple Silicon and can resolve a wrong-arch dylib (e.g. a stale x86_64
    # copy in /usr/local/lib). Probe known locations and use the first one
    # that actually loads.
    for cand in _darwin_libusb_candidates():
        if not cand.exists():
            continue
        try:
            ctypes.CDLL(str(cand))
        except OSError:
            continue  # wrong arch / unloadable — keep probing
        return libusb1.get_backend(find_library=lambda _n, _p=cand: str(_p))
    return None


def _darwin_libusb_candidates() -> list[Path]:
    if sys.platform != "darwin":
        return []
    return [
        _base_dir() / "libusb-1.0.0.dylib",  # PyInstaller-bundled
        Path("/opt/homebrew/opt/libusb/lib/libusb-1.0.dylib"),
        Path("/opt/homebrew/lib/libusb-1.0.dylib"),
        Path("/usr/local/opt/libusb/lib/libusb-1.0.dylib"),
        Path("/usr/local/lib/libusb-1.0.dylib"),
    ]
