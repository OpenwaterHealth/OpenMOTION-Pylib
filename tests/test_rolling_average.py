"""
Rolling-average verification (no hardware).

Drives a real SciencePipeline via create_science_pipeline +
feed_pipeline_from_csv using the single_cam_basic fixture
(DISCARD_COUNT=2, DARK_INTERVAL=5 -> light frames at
{4, 5, 7, 8, 9, 10, 12}, darks at {3, 6, 11}).

Run with pytest:
    pytest tests/test_rolling_average.py -v
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

DISCARD_COUNT = 2
DARK_INTERVAL = 5
LIGHT_FRAMES = {4, 5, 7, 8, 9, 10, 12}
DARK_FRAMES = {3, 6, 11}

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

_ZERO = np.zeros((2, 8), dtype=np.float64)
_ONE = np.ones((2, 8), dtype=np.float64)
BFI_C_MIN = _ZERO.copy()
BFI_C_MAX = _ONE.copy()
BFI_I_MIN = _ZERO.copy()
BFI_I_MAX = np.full((2, 8), 1000.0)


def _fixture(name: str) -> str:
    return os.path.join(FIXTURES_DIR, name)


def _make_pipeline(
    *,
    rolling_avg_enabled: bool,
    rolling_avg_window: int = 3,
    left_mask: int = 0x01,
    right_mask: int = 0x00,
):
    uncorrected: list[Sample] = []
    batches: list[CorrectedBatch] = []
    rolling: list[Sample] = []

    pipeline = create_science_pipeline(
        left_camera_mask=left_mask,
        right_camera_mask=right_mask,
        bfi_c_min=BFI_C_MIN,
        bfi_c_max=BFI_C_MAX,
        bfi_i_min=BFI_I_MIN,
        bfi_i_max=BFI_I_MAX,
        on_uncorrected_fn=uncorrected.append,
        on_corrected_batch_fn=batches.append,
        on_rolling_avg_fn=rolling.append,
        rolling_avg_enabled=rolling_avg_enabled,
        rolling_avg_window=rolling_avg_window,
        dark_interval=DARK_INTERVAL,
        discard_count=DISCARD_COUNT,
        expected_row_sum=None,
    )
    return pipeline, uncorrected, batches, rolling


class TestRollingAverageDisabled:
    """rolling_avg_enabled=False means the callback never fires."""

    def test_disabled_never_fires_callback(self):
        pipeline, _, _, rolling = _make_pipeline(rolling_avg_enabled=False)
        feed_pipeline_from_csv(
            _fixture("single_cam_basic_left.csv"), "left", pipeline
        )
        pipeline.stop(timeout=5.0)
        assert rolling == [], (
            f"Expected no rolling-avg emissions when disabled, got {len(rolling)}"
        )


class TestRollingAverageEnabled:
    """rolling_avg_enabled=True with window=3 emits once per light frame,
    averaging only mean and contrast, zeroing the rest."""

    def setup_method(self):
        self.pipeline, self.uncorrected, self.batches, self.rolling = \
            _make_pipeline(rolling_avg_enabled=True, rolling_avg_window=3)
        feed_pipeline_from_csv(
            _fixture("single_cam_basic_left.csv"), "left", self.pipeline
        )
        self.pipeline.stop(timeout=5.0)

    def test_one_emission_per_light_frame(self):
        # Light frames: {4, 5, 7, 8, 9, 10, 12} -> 7 emissions.
        assert len(self.rolling) == 7, (
            f"Expected 7 rolling-avg emissions, got {len(self.rolling)}"
        )

    def test_emissions_only_at_light_frame_positions(self):
        abs_ids = sorted(s.absolute_frame_id for s in self.rolling)
        assert abs_ids == sorted(LIGHT_FRAMES), (
            f"Rolling-avg emissions should occur at light frames "
            f"{sorted(LIGHT_FRAMES)}, got {abs_ids}"
        )

    def test_no_dark_frame_ids_in_emissions(self):
        for s in self.rolling:
            assert s.absolute_frame_id not in DARK_FRAMES, (
                f"Rolling-avg emission at dark frame {s.absolute_frame_id} "
                f"- dark frames must not enter the window"
            )

    def test_emitted_sample_not_corrected_not_dark(self):
        for s in self.rolling:
            assert s.is_corrected is False
            assert s.is_dark is False

    def test_zeroed_fields(self):
        for s in self.rolling:
            assert s.std_dev == 0.0
            assert s.bfi == 0.0
            assert s.bvi == 0.0
            assert s.row_sum == 0
            assert s.temperature_c == 0.0

    def test_rolling_mean_matches_reference(self):
        # Build reference: group uncorrected samples by absolute_frame_id,
        # filter out dark repeats, then apply a size-3 rolling mean manually.
        light_uncorrected = [
            s for s in self.uncorrected if s.is_dark is False
        ]
        light_uncorrected.sort(key=lambda s: s.absolute_frame_id)

        reference_means: dict[int, float] = {}
        window: list[Sample] = []
        for s in light_uncorrected:
            window.append(s)
            if len(window) > 3:
                window.pop(0)
            reference_means[s.absolute_frame_id] = sum(
                w.mean for w in window
            ) / len(window)

        for s in self.rolling:
            expected = reference_means[s.absolute_frame_id]
            assert abs(s.mean - expected) < 1e-6, (
                f"Frame {s.absolute_frame_id}: rolling mean {s.mean:.6f} "
                f"does not match reference {expected:.6f}"
            )

    def test_rolling_contrast_matches_reference(self):
        light_uncorrected = [
            s for s in self.uncorrected if s.is_dark is False
        ]
        light_uncorrected.sort(key=lambda s: s.absolute_frame_id)

        reference_contrasts: dict[int, float] = {}
        window: list[Sample] = []
        for s in light_uncorrected:
            window.append(s)
            if len(window) > 3:
                window.pop(0)
            reference_contrasts[s.absolute_frame_id] = sum(
                w.contrast for w in window
            ) / len(window)

        for s in self.rolling:
            expected = reference_contrasts[s.absolute_frame_id]
            assert abs(s.contrast - expected) < 1e-6, (
                f"Frame {s.absolute_frame_id}: rolling contrast {s.contrast:.6f} "
                f"does not match reference {expected:.6f}"
            )

    def test_partial_window_before_n_samples(self):
        # First light frame (4) has only 1 sample in the window; the emitted
        # mean must equal that sample's own uncorrected mean, not wait for
        # the window to fill.
        first = next(s for s in self.rolling if s.absolute_frame_id == 4)
        first_uncorr = next(
            s for s in self.uncorrected
            if s.absolute_frame_id == 4 and s.is_dark is False
        )
        assert abs(first.mean - first_uncorr.mean) < 1e-6, (
            f"Partial-window emission at frame 4 should equal uncorrected "
            f"mean {first_uncorr.mean:.6f}, got {first.mean:.6f}"
        )


class TestRollingAveragePerCameraIsolation:
    """Interleaved frames across cameras must not cross-contaminate windows."""

    def test_left_and_right_cameras_do_not_mix(self):
        # Enable one camera on each side; feed both fixtures.  The
        # single_cam_basic fixtures for left and right use the same bin
        # distributions, so a cross-contamination bug would be hard to
        # detect numerically - instead assert per-(side, cam_id) emission
        # counts.
        pipeline, _, _, rolling = _make_pipeline(
            rolling_avg_enabled=True,
            rolling_avg_window=3,
            left_mask=0x01,
            right_mask=0x01,
        )
        # Only run this if the right-side fixture exists
        right_fix = _fixture("single_cam_basic_right.csv")
        if not os.path.exists(right_fix):
            pytest.skip("right-side single-cam fixture not present")
        feed_pipeline_from_csv(
            _fixture("single_cam_basic_left.csv"), "left", pipeline
        )
        feed_pipeline_from_csv(right_fix, "right", pipeline)
        pipeline.stop(timeout=5.0)

        left_count = sum(1 for s in rolling if s.side == "left")
        right_count = sum(1 for s in rolling if s.side == "right")
        # Each side emits once per light frame -> 7 each.
        assert left_count == 7
        assert right_count == 7
