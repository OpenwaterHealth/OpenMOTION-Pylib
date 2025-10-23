from __future__ import annotations
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from .config import *
from .MotionUart import MOTIONUart
from .MotionSignal import MOTIONSignal
from .MotionComposite import MotionComposite
from .USBInterfaceBase import USBInterfaceBase

__all__ = ["__version__"]

try:
    # works when installed (wheel/sdist) — uses dist-info METADATA
    __version__ = _pkg_version("openmotion-pylib")
except PackageNotFoundError:
    # running from source (no dist-info)? fall back to pyproject.toml
    try:
        import tomllib  # Python 3.11+
        from pathlib import Path
        pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_bytes()
        __version__ = tomllib.loads(pyproject)["project"]["version"]
    except Exception:
        __version__ = "0+unknown"
