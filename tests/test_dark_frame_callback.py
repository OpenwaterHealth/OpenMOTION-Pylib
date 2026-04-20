"""
Dark-frame callback verification (no hardware).

Drives a real SciencePipeline via create_science_pipeline +
feed_pipeline_from_csv using the single_cam_basic fixture
(DISCARD_COUNT=2, DARK_INTERVAL=5 -> darks at absolute frames {3, 6, 11}).

Run with pytest:
    pytest tests/test_dark_frame_callback.py -v
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omotion.MotionProcessing import (
    CorrectedBatch,
    Sample,
    create_science_pipeline,
    feed_pipeline_from_csv,
)

# Keep these in sync with tests/test_pipeline_csv.py / generate_fixtures.py.
DISCARD_COUNT = 2
DARK_INTERVAL = 5
DARK_FRAMES_5 = {3, 6, 11}

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

_ZERO = np.zeros((2, 8), dtype=np.float64)
_ONE = np.ones((2, 8), dtype=np.float64)
BFI_C_MIN = _ZERO.copy()
BFI_C_MAX = _ONE.copy()
BFI_I_MIN = _ZERO.copy()
BFI_I_MAX = np.full((2, 8), 1000.0)


def _fixture(name: str) -> str:
    return os.path.join(FIXTURES_DIR, name)


class TestSampleIsDarkField:
    """Foundational: Sample has an is_dark field defaulting to False."""

    def test_sample_is_dark_defaults_to_false(self):
        s = Sample(
            side="left", cam_id=0,
            frame_id=10, absolute_frame_id=10, timestamp_s=0.1,
            row_sum=1000, temperature_c=25.0,
            mean=100.0, std_dev=10.0, contrast=0.1,
            bfi=1.0, bvi=1.0,
        )
        assert s.is_dark is False

    def test_sample_is_dark_can_be_set_true(self):
        s = Sample(
            side="left", cam_id=0,
            frame_id=10, absolute_frame_id=10, timestamp_s=0.1,
            row_sum=1000, temperature_c=25.0,
            mean=100.0, std_dev=10.0, contrast=0.1,
            bfi=1.0, bvi=1.0,
            is_dark=True,
        )
        assert s.is_dark is True
