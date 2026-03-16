"""
DFU entry tests — intentionally named test_zz_dfu.py so they sort LAST
in pytest collection order (after test_sequences, test_sensor, etc.).

Once a device enters DFU it disconnects from USB and is unavailable for
further testing, so these tests must run after everything else.
"""

import pytest


pytestmark = [pytest.mark.destructive, pytest.mark.slow]


# ===========================================================================
# Sensor DFU (parametrised: runs for left + right)
# ===========================================================================

@pytest.mark.skip(reason="DFU temporarily disabled")
def test_sensor_enter_dfu(any_sensor):
    """Enter DFU mode on sensor.  Device disconnects after this call."""
    result = any_sensor.enter_dfu()
    assert result is True


# ===========================================================================
# Console DFU (already in test_console.py as test_z_enter_dfu, but kept
# here as a canonical single entry-point for --destructive-only runs)
# ===========================================================================
# Note: test_console.py also has test_z_enter_dfu which runs last within
# that file.  The test below is NOT duplicated here to avoid double-DFU;
# rely on test_console.py's test_z_enter_dfu for the console device.
