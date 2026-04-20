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


def _make_pipeline_with_dark(
    left_mask: int = 0x01,
    right_mask: int = 0x00,
    dark_interval: int = DARK_INTERVAL,
):
    """Build a pipeline that collects uncorrected, corrected batches, and dark samples."""
    uncorrected: list[Sample] = []
    batches: list[CorrectedBatch] = []
    dark_samples: list[Sample] = []

    pipeline = create_science_pipeline(
        left_camera_mask=left_mask,
        right_camera_mask=right_mask,
        bfi_c_min=BFI_C_MIN,
        bfi_c_max=BFI_C_MAX,
        bfi_i_min=BFI_I_MIN,
        bfi_i_max=BFI_I_MAX,
        on_uncorrected_fn=uncorrected.append,
        on_corrected_batch_fn=batches.append,
        on_dark_frame_fn=dark_samples.append,
        dark_interval=dark_interval,
        discard_count=DISCARD_COUNT,
        expected_row_sum=None,
    )
    return pipeline, uncorrected, batches, dark_samples


class TestOnDarkFrameCallback:
    """on_dark_frame_fn fires once per scheduled dark frame with a Sample
    whose is_dark=True and whose mean/std_dev come from the raw dark
    histogram statistics."""

    def setup_method(self):
        self.pipeline, self.uncorrected, self.batches, self.darks = \
            _make_pipeline_with_dark(left_mask=0x01)
        feed_pipeline_from_csv(
            _fixture("single_cam_basic_left.csv"), "left", self.pipeline
        )
        self.pipeline.stop(timeout=5.0)

    def test_dark_callback_fires_at_every_dark_position(self):
        abs_ids = sorted(s.absolute_frame_id for s in self.darks)
        # DISCARD_COUNT=2, DARK_INTERVAL=5 -> darks at 3, 6, 11
        assert abs_ids == [3, 6, 11], (
            f"Expected dark callback at frames [3, 6, 11], got {abs_ids}"
        )

    def test_dark_callback_does_not_fire_on_light_frames(self):
        for s in self.darks:
            assert s.absolute_frame_id in DARK_FRAMES_5, (
                f"Frame {s.absolute_frame_id} is not a scheduled dark position"
            )

    def test_dark_samples_have_is_dark_true(self):
        for s in self.darks:
            assert s.is_dark is True, (
                f"Dark sample at frame {s.absolute_frame_id} must have is_dark=True"
            )

    def test_dark_samples_have_is_corrected_false(self):
        for s in self.darks:
            assert s.is_corrected is False

    def test_dark_sample_mean_is_pedestal_subtracted(self):
        # Fixture dark bins are [10, 20) -> raw u1 ~= 14.5.
        # on_dark_frame_fn emits pedestal-subtracted means, so expected
        # value is roughly 14.5 - 64.0 = -49.5.
        for s in self.darks:
            assert -60.0 < s.mean < -40.0, (
                f"Dark frame {s.absolute_frame_id} mean {s.mean:.2f} "
                f"not in expected pedestal-subtracted range (-60, -40)"
            )

    def test_dark_sample_std_dev_nonnegative_and_finite(self):
        for s in self.darks:
            assert s.std_dev >= 0.0 and np.isfinite(s.std_dev)

    def test_dark_sample_bfi_bvi_are_zero(self):
        # BFI/BVI are not meaningful on dark frames - the callback leaves them 0.
        for s in self.darks:
            assert s.bfi == 0.0
            assert s.bvi == 0.0

    def test_dark_sample_contrast_is_std_over_mean(self):
        for s in self.darks:
            if s.mean > 0:
                expected_contrast = s.std_dev / s.mean
                assert abs(s.contrast - expected_contrast) < 1e-9, (
                    f"Dark frame {s.absolute_frame_id} contrast {s.contrast:.6f} "
                    f"does not match std/mean = {expected_contrast:.6f}"
                )
            else:
                assert s.contrast == 0.0, (
                    f"Dark frame {s.absolute_frame_id} contrast should be 0 "
                    f"when mean <= 0, got {s.contrast:.6f}"
                )

    def test_missing_dark_callback_is_a_noop(self):
        """Pipeline must still run correctly when on_dark_frame_fn is None."""
        uncorrected: list[Sample] = []
        batches: list[CorrectedBatch] = []
        pipeline = create_science_pipeline(
            left_camera_mask=0x01,
            right_camera_mask=0x00,
            bfi_c_min=BFI_C_MIN, bfi_c_max=BFI_C_MAX,
            bfi_i_min=BFI_I_MIN, bfi_i_max=BFI_I_MAX,
            on_uncorrected_fn=uncorrected.append,
            on_corrected_batch_fn=batches.append,
            on_dark_frame_fn=None,          # explicit None
            dark_interval=DARK_INTERVAL,
            discard_count=DISCARD_COUNT,
            expected_row_sum=None,
        )
        feed_pipeline_from_csv(
            _fixture("single_cam_basic_left.csv"), "left", pipeline
        )
        pipeline.stop(timeout=5.0)
        # At least one corrected batch proves the pipeline processed frames.
        assert len(batches) >= 1


class TestDarkRepeatUncorrectedIsMarked:
    """The on_uncorrected_fn also fires at dark-frame slots with a
    repeat-previous-value Sample so live plots see no gap.  That sample
    should now be tagged is_dark=True."""

    def setup_method(self):
        self.pipeline, self.uncorrected, self.batches, self.darks = \
            _make_pipeline_with_dark(left_mask=0x01)
        feed_pipeline_from_csv(
            _fixture("single_cam_basic_left.csv"), "left", self.pipeline
        )
        self.pipeline.stop(timeout=5.0)

    def test_light_uncorrected_samples_are_not_marked_dark(self):
        # Frame 3 is the first dark — no prev exists yet, so no repeat sample.
        # Frames 6 and 11 ARE dark-repeat slots.  All other emitted
        # uncorrected samples (4, 5, 7, 8, 9, 10, 12) are genuine light.
        dark_repeat_slots = {6, 11}
        for s in self.uncorrected:
            if s.absolute_frame_id in dark_repeat_slots:
                assert s.is_dark is True, (
                    f"Dark-repeat uncorrected sample at frame "
                    f"{s.absolute_frame_id} must have is_dark=True"
                )
            else:
                assert s.is_dark is False, (
                    f"Light uncorrected sample at frame "
                    f"{s.absolute_frame_id} must have is_dark=False"
                )

    def test_dark_repeat_sample_actually_emitted(self):
        # Confirm the fixture produces the expected dark-repeat emissions.
        abs_ids = {s.absolute_frame_id for s in self.uncorrected
                   if s.is_dark}
        assert abs_ids == {6, 11}, (
            f"Expected dark-repeat uncorrected samples at {{6, 11}}, got {abs_ids}"
        )
