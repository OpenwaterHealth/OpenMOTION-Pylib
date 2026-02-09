#!/usr/bin/env python3
"""Test script to verify dfu-util can be found when installed from wheel."""

from pathlib import Path
from omotion.DFUProgrammer import DFUProgrammer

def test_dfu_util_location():
    """Verify that DFUProgrammer can locate dfu-util binary."""
    try:
        dfu = DFUProgrammer()
        print(f"✅ SUCCESS: dfu-util found at: {dfu.dfu_util_path}")
        print(f"   Binary exists: {dfu.dfu_util_path.is_file()}")
        print(f"   Parent directory: {dfu.dfu_util_path.parent}")
        
        # Try to list devices (this will fail if dfu-util doesn't work)
        try:
            output = dfu.list_devices()
            print(f"✅ dfu-util executed successfully")
            print(f"   Output length: {len(output)} bytes")
        except Exception as e:
            print(f"⚠️  dfu-util execution failed (might be OK if no device connected): {e}")
        
        return True
    except FileNotFoundError as e:
        print(f"❌ FAILED: {e}")
        return False
    except Exception as e:
        print(f"❌ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_dfu_util_location()
    exit(0 if success else 1)
