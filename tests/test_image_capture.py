"""Software-only unit tests for drip-scan image capture (camera-fpga#8, SDK issue #167).

Covers: pinned config constants, RAW10 pack/unpack bit layout, line parse with
CRC verification, USB envelope parse, FrameAssembler, sweep retiming sequence,
and the sweep retry policy. No hardware required.
"""

import numpy as np
import pytest

pytestmark = pytest.mark.unit


def test_config_constants_pinned_values():
    """The cross-plan pinned wire/protocol constants. If this test fails after
    an edit to config.py, firmware/FPGA interop is broken — these values are
    fixed by the drip-scan design spec and must not drift."""
    from omotion import config

    assert config.TYPE_IMAGE == 0x03
    assert config.OW_IMAGE_PACKET == 0x03          # pre-existing, same value, different namespace
    assert config.OW_CAMERA_IMAGE_MODE == 0x30
    assert config.SWEEP_FSIN_HZ == 0.8
    assert config.PRODUCTION_FSIN_HZ == 40.0
    # Sweep profile: HTS=38400, VTS=1312, tc_r_initial=1308, exposure=1 row.
    assert config.SWEEP_TIMING_PROFILE == (
        (0x380C, 0x96), (0x380D, 0x00),
        (0x380E, 0x05), (0x380F, 0x20),
        (0x3826, 0x05), (0x3827, 0x1C),
        (0x3501, 0x00), (0x3502, 0x01),
    )
    # Restore profile: shipped production values from X02C1B_Sensor_Config.h.
    assert config.PRODUCTION_TIMING_PROFILE == (
        (0x380C, 0x01), (0x380D, 0xB0),
        (0x380E, 0x0A), (0x380F, 0xD0),
        (0x3826, 0x00), (0x3827, 0x00),
        (0x3501, 0x00), (0x3502, 0x48),
    )
