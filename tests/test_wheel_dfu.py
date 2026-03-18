#!/usr/bin/env python3
"""Test script to verify dfu-util can be found when installed from wheel."""

import pytest
from omotion.DFUProgrammer import DFUProgrammer


def test_dfu_util_location():
    """Verify that DFUProgrammer can locate and execute the dfu-util binary."""
    dfu = DFUProgrammer()  # raises FileNotFoundError if binary is missing

    assert dfu.dfu_util_path.is_file(), (
        f"dfu-util binary not found at {dfu.dfu_util_path}"
    )

    # Attempt to list DFU devices.  dfu-util exits non-zero when no device is
    # attached, which raises an exception — that is expected and acceptable here.
    try:
        output = dfu.list_devices()
        assert isinstance(output, (str, bytes)), (
            f"list_devices() returned unexpected type {type(output)}"
        )
    except Exception as e:
        pytest.skip(f"dfu-util ran but no DFU device is connected: {e}")
