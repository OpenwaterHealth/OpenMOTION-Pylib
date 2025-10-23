# omotion/usb_backend.py
import os
import sys
import platform
from pathlib import Path
import ctypes

def _dll_dir() -> Path | None:
    if sys.platform != "win32":
        return None
    arch = platform.machine().lower()
    # Map common arch strings to our folders
    sub = "x64" if arch in ("amd64", "x86_64") else "x86"
    return Path(__file__).parent / "_vendor" / "libusb" / "windows" / sub

def get_libusb1_backend():
    import usb.backend.libusb1 as libusb1

    if sys.platform == "win32":
        dll_dir = _dll_dir()
        if not dll_dir:
            raise RuntimeError("Unsupported Windows arch for libusb")
        dll_path = dll_dir / "libusb-1.0.dll"
        if not dll_path.exists():
            raise FileNotFoundError(f"Missing vendored libusb DLL at {dll_path}")

        # Ensure Windows can load this DLL (Python 3.8+)
        os.add_dll_directory(str(dll_dir))
        # Preload explicitly (optional but helpful for clear errors)
        ctypes.CDLL(str(dll_path))

        # Tell PyUSB to use this exact DLL
        return libusb1.get_backend(find_library=lambda name: str(dll_path))

    # Non-Windows: let libusb be found by system loader
    return libusb1.get_backend()
