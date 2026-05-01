"""Integration tests for console calibration read/write — no hardware.

Uses unittest.mock to patch MotionConsole.read_config / write_config so
we don't need a connected console.
"""

from unittest.mock import MagicMock

import numpy as np
import pytest

from omotion.Calibration import (
    CALIBRATION_JSON_KEY,
    Calibration,
)
from omotion.MotionConfig import MotionConfig
from omotion.MotionConsole import MotionConsole


# A demo-mode console gives us a real instance without a serial port.
@pytest.fixture
def console():
    return MotionConsole(vid=0, pid=0, baudrate=921600, timeout=1, demo_mode=True)


def _valid_calibration_dict():
    return {
        CALIBRATION_JSON_KEY: {
            "C_min": [[0.0]*8, [0.0]*8],
            "C_max": [[0.5]*8, [0.5]*8],
            "I_min": [[0.0]*8, [0.0]*8],
            "I_max": [[300.0]*8, [300.0]*8],
        }
    }


def test_read_calibration_returns_defaults_when_read_config_returns_none(console):
    console.read_config = MagicMock(return_value=None)
    cal = console.read_calibration()
    assert cal.source == "default"
    assert cal.c_max.shape == (2, 8)


def test_read_calibration_returns_defaults_when_block_absent(console):
    cfg = MotionConfig(json_data={"EE_THRESH": [1, 2, 3]})  # no calibration key
    console.read_config = MagicMock(return_value=cfg)
    cal = console.read_calibration()
    assert cal.source == "default"


def test_read_calibration_returns_console_when_valid(console):
    cfg = MotionConfig(json_data=_valid_calibration_dict())
    console.read_config = MagicMock(return_value=cfg)
    cal = console.read_calibration()
    assert cal.source == "console"
    np.testing.assert_array_equal(cal.c_max, np.full((2, 8), 0.5))
    np.testing.assert_array_equal(cal.i_max, np.full((2, 8), 300.0))


def test_read_calibration_falls_back_when_block_malformed(console, caplog):
    bad = _valid_calibration_dict()
    bad[CALIBRATION_JSON_KEY]["C_max"] = [[0.0]*8, [0.0]*8]  # not > C_min
    cfg = MotionConfig(json_data=bad)
    console.read_config = MagicMock(return_value=cfg)
    with caplog.at_level("WARNING"):
        cal = console.read_calibration()
    assert cal.source == "default"
    assert any("monotonic" in rec.message.lower() or "greater" in rec.message.lower()
               for rec in caplog.records)


# ----- write_calibration -----

def test_write_calibration_rejects_bad_shape_before_wire(console):
    console.read_config = MagicMock()
    console.write_config = MagicMock()
    with pytest.raises(ValueError, match="shape"):
        console.write_calibration(
            np.zeros((2, 7)), np.full((2, 7), 0.5),
            np.zeros((2, 7)), np.full((2, 7), 250.0),
        )
    console.read_config.assert_not_called()
    console.write_config.assert_not_called()


def test_write_calibration_preserves_other_keys(console):
    existing = MotionConfig(json_data={
        "EE_THRESH": [1, 2, 3],
        "OPT_GAIN": [4, 5, 6],
    })
    console.read_config = MagicMock(return_value=existing)
    captured = {}
    def _capture_write(cfg):
        captured["cfg"] = cfg
        return cfg
    console.write_config = MagicMock(side_effect=_capture_write)

    console.write_calibration(
        np.zeros((2, 8)), np.full((2, 8), 0.5),
        np.zeros((2, 8)), np.full((2, 8), 250.0),
    )

    written = captured["cfg"].json_data
    assert written["EE_THRESH"] == [1, 2, 3]
    assert written["OPT_GAIN"] == [4, 5, 6]
    assert CALIBRATION_JSON_KEY in written
    assert written[CALIBRATION_JSON_KEY]["C_max"][0][0] == 0.5


def test_write_calibration_returns_console_source(console):
    console.read_config = MagicMock(return_value=MotionConfig(json_data={}))
    console.write_config = MagicMock(side_effect=lambda cfg: cfg)
    cal = console.write_calibration(
        np.zeros((2, 8)), np.full((2, 8), 0.5),
        np.zeros((2, 8)), np.full((2, 8), 250.0),
    )
    assert cal.source == "console"
    np.testing.assert_array_equal(cal.c_max, np.full((2, 8), 0.5))


def test_write_calibration_raises_when_read_config_returns_none(console):
    console.read_config = MagicMock(return_value=None)
    console.write_config = MagicMock()
    with pytest.raises(RuntimeError, match="read existing config"):
        console.write_calibration(
            np.zeros((2, 8)), np.full((2, 8), 0.5),
            np.zeros((2, 8)), np.full((2, 8), 250.0),
        )
    console.write_config.assert_not_called()
