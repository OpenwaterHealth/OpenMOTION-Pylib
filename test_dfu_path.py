#!/usr/bin/env python3
"""Minimal test to verify dfu-util path resolution."""

import sys
from pathlib import Path

# Add current directory to path for testing
sys.path.insert(0, str(Path(__file__).parent))

# Only import what we need
import platform
from pathlib import Path

# Simulate the DFUProgrammer path resolution logic
def _platform_subdir() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    
    if system.startswith("darwin"):
        return "darwin-x86_64"
    if system.startswith("linux"):
        return "linux-amd64"
    if system.startswith("windows"):
        return "win64" if "64" in machine else "win32"
    
    raise RuntimeError(f"Unsupported OS: {platform.system()}")

def locate_dfu_util() -> Path:
    subdir = _platform_subdir()
    exe = "dfu-util.exe" if platform.system().lower().startswith("windows") else "dfu-util"
    
    # Try package-installed location first
    package_dir = Path(__file__).parent / "omotion"
    package_dfu_dir = package_dir / "dfu-util" / subdir
    package_dfu_path = package_dfu_dir / exe
    if package_dfu_path.is_file():
        return package_dfu_path
    
    # Fall back to repo root location
    repo_root = Path(__file__).parent
    repo_dfu_dir = repo_root / "dfu-util" / subdir
    repo_dfu_path = repo_dfu_dir / exe
    if repo_dfu_path.is_file():
        return repo_dfu_path
    
    raise FileNotFoundError(
        f"dfu-util binary not found in package ({package_dfu_path}) "
        f"or repo ({repo_dfu_path})"
    )

if __name__ == "__main__":
    try:
        dfu_path = locate_dfu_util()
        print(f"✅ SUCCESS: dfu-util found at: {dfu_path}")
        print(f"   Binary exists: {dfu_path.is_file()}")
        print(f"   Parent directory: {dfu_path.parent}")
        print(f"   Platform detected: {_platform_subdir()}")
    except FileNotFoundError as e:
        print(f"❌ FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
