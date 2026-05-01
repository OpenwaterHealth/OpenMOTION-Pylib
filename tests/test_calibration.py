"""Unit tests for omotion.Calibration (no hardware required)."""

import numpy as np
import pytest

from omotion.Calibration import (
    Calibration,
    CALIBRATION_JSON_KEY,
    parse_calibration,
    serialize_calibration,
)


# Reference defaults — copied verbatim from
# openmotion-bloodflow-app/processing/visualize_bloodflow.py as of 2026-05-01.
# This is the golden test that pins the SDK defaults to the values the
# bloodflow app has been using.
_REF_C_MIN = np.zeros((2, 8), dtype=float)
_REF_C_MAX = np.array(
    [[0.4, 0.4, 0.45, 0.55, 0.55, 0.45, 0.4, 0.4],
     [0.4, 0.4, 0.45, 0.55, 0.55, 0.45, 0.4, 0.4]],
    dtype=float,
)
_REF_I_MIN = np.zeros((2, 8), dtype=float)
_REF_I_MAX = np.array(
    [[150, 300, 300, 300, 300, 300, 300, 150],
     [150, 300, 300, 300, 300, 300, 300, 150]],
    dtype=float,
)


def test_default_values_match_visualize_bloodflow_defaults():
    cal = Calibration.default()
    np.testing.assert_array_equal(cal.c_min, _REF_C_MIN)
    np.testing.assert_array_equal(cal.c_max, _REF_C_MAX)
    np.testing.assert_array_equal(cal.i_min, _REF_I_MIN)
    np.testing.assert_array_equal(cal.i_max, _REF_I_MAX)


def test_default_source_label():
    cal = Calibration.default()
    assert cal.source == "default"


def test_default_returns_independent_copies():
    a = Calibration.default()
    b = Calibration.default()
    a.c_max[0, 0] = 999.0
    # Mutating one default must not bleed into the next call.
    assert b.c_max[0, 0] == _REF_C_MAX[0, 0]


def test_default_arrays_have_correct_shape_and_dtype():
    cal = Calibration.default()
    for arr in (cal.c_min, cal.c_max, cal.i_min, cal.i_max):
        assert arr.shape == (2, 8)
        assert arr.dtype == np.float64
