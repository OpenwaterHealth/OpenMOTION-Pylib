# omotion/usb_backend.py
import os, sys, platform, ctypes
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
    return libusb1.get_backend()
