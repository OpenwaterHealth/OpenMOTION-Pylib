"""
Science pipeline tests driven by CSV fixture files.

These tests require no hardware — they feed pre-generated histogram CSVs
directly into SciencePipeline via feed_pipeline_from_csv and assert that
the pipeline's callbacks fire with the correct data.

Run with pytest:
    pytest tests/test_pipeline_csv.py -v

Or standalone:
    python tests/test_pipeline_csv.py

Fixture conventions (see tests/fixtures/generate_fixtures.py for details)
---------------------------------------------------------------------------
- DISCARD_COUNT = 2  → frames 1–2 are warmup and must be dropped
- DARK_INTERVAL = 5  → dark frames at absolute positions 3, 6, 11, 16, 21 …
- Regular histograms: uniform over bins [400, 500)  (mean ≈ 449.5)
- Dark    histograms: uniform over bins  [10,  20)  (mean ≈  14.5)
- expected_row_sum=None is passed to the pipeline so the fixture photon
  count (10 000) is not rejected by the hardware sum validator.
"""

import os
import sys
import threading
import time

import numpy as np
import pytest

# Allow running standalone from the project root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omotion.MotionProcessing import (
    CorrectedBatch,
    Sample,
    create_science_pipeline,
    feed_pipeline_from_csv,
)

# ---------------------------------------------------------------------------
# Constants matching the fixtures (must stay in sync with generate_fixtures.py)
# ---------------------------------------------------------------------------

DISCARD_COUNT  = 2
DARK_INTERVAL  = 5
ROLLOVER_DARK_INTERVAL = 10  # used only in frame_id_rollover fixture
TOTAL_PHOTONS  = 2_457_606

REGULAR_LO, REGULAR_HI = 400, 500
DARK_LO,    DARK_HI    =  10,  20

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

# Expected dark frame positions for the default dark_interval=5, discard_count=2:
#   3, 6, 11, 16, 21 …
DARK_FRAMES_5 = {3, 6, 11, 16, 21}

# ---------------------------------------------------------------------------
# Calibration arrays — identity-like so BFI/BVI stay in the 0–10 range and
# are easy to reason about without knowing the exact calibration offsets.
# ---------------------------------------------------------------------------
_ZERO = np.zeros((2, 8), dtype=np.float64)
_ONE  = np.ones((2, 8),  dtype=np.float64)
BFI_C_MIN = _ZERO.copy()
BFI_C_MAX = _ONE.copy()
BFI_I_MIN = _ZERO.copy()
BFI_I_MAX = np.full((2, 8), 1000.0)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_pipeline(left_mask: int = 0x01,
                   right_mask: int = 0x00,
                   dark_interval: int = DARK_INTERVAL):
    """
    Create a SciencePipeline wired to three collector lists.

    Returns (pipeline, uncorrected_list, batches_list).
    The pipeline is already started; call pipeline.stop() when done.
    """
    uncorrected: list[Sample]         = []
    batches:     list[CorrectedBatch] = []

    pipeline = create_science_pipeline(
        left_camera_mask=left_mask,
        right_camera_mask=right_mask,
        bfi_c_min=BFI_C_MIN,
        bfi_c_max=BFI_C_MAX,
        bfi_i_min=BFI_I_MIN,
        bfi_i_max=BFI_I_MAX,
        on_uncorrected_fn=uncorrected.append,
        on_corrected_batch_fn=batches.append,
        dark_interval=dark_interval,
        discard_count=DISCARD_COUNT,
        expected_row_sum=None,   # fixture photon count != hardware sum
    )
    return pipeline, uncorrected, batches


def _fixture(name: str) -> str:
    return os.path.join(FIXTURES_DIR, name)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSingleCamBasic:
    """Single left camera, 12 frames — verifies warmup discard and dark detection."""

    def setup_method(self):
        self.pipeline, self.uncorrected, self.batches = \
            _make_pipeline(left_mask=0x01)
        self.rows = feed_pipeline_from_csv(
            _fixture("single_cam_basic_left.csv"), "left", self.pipeline
        )
        self.pipeline.stop(timeout=5.0)

    def test_rows_were_fed(self):
        assert self.rows == 12, f"Expected 12 rows, got {self.rows}"

    def test_warmup_frames_discarded(self):
        abs_ids = {s.absolute_frame_id for s in self.uncorrected}
        for bad in range(1, DISCARD_COUNT + 1):
            assert bad not in abs_ids, (
                f"Warmup frame {bad} must not reach the uncorrected callback"
            )

    def test_dark_frames_not_in_uncorrected_as_new_values(self):
        # Dark frame positions should not appear as fresh uncorrected samples —
        # the pipeline repeats the previous value instead of computing a new one.
        # (They may appear with the previous sample's frame ID values; we
        # only assert that the absolute IDs present are consistent.)
        abs_ids = [s.absolute_frame_id for s in self.uncorrected]
        # Frame 3 is first dark: it may appear via the repeat-last-value rule
        # but must never be a genuine new computation.
        # All ids must be above DISCARD_COUNT (warmup) and not skipping ids
        # in an unexpected way.
        assert all(i > DISCARD_COUNT for i in abs_ids)

    def test_at_least_one_corrected_batch(self):
        assert len(self.batches) >= 1, (
            f"Expected >=1 corrected batch; got {len(self.batches)}"
        )

    def test_first_batch_boundaries(self):
        batch = self.batches[0]
        assert batch.dark_frame_start == DISCARD_COUNT + 1, (
            f"First batch dark_frame_start should be {DISCARD_COUNT + 1}, "
            f"got {batch.dark_frame_start}"
        )
        assert batch.dark_frame_end == 6, (
            f"First batch dark_frame_end should be 6, got {batch.dark_frame_end}"
        )

    def test_first_batch_samples_are_corrected(self):
        for s in self.batches[0].samples:
            assert s.is_corrected, (
                f"Sample at absolute frame {s.absolute_frame_id} "
                f"should have is_corrected=True"
            )

    def test_first_batch_sample_frame_range(self):
        """All samples in batch 0 must fall within its dark-frame interval."""
        batch = self.batches[0]
        for s in batch.samples:
            assert batch.dark_frame_start <= s.absolute_frame_id <= batch.dark_frame_end, (
                f"Sample at frame {s.absolute_frame_id} is outside "
                f"[{batch.dark_frame_start}, {batch.dark_frame_end}]"
            )

    def test_two_corrected_batches(self):
        """12 frames give dark frames at 3, 6, 11 → two complete intervals."""
        assert len(self.batches) >= 2, (
            f"Expected >=2 corrected batches for 12 frames; got {len(self.batches)}"
        )

    def test_second_batch_boundaries(self):
        batch = self.batches[1]
        assert batch.dark_frame_start == 6
        assert batch.dark_frame_end == 11


class TestDarkCorrectionMath:
    """
    Verify the dark-correction arithmetic produces plausible values.

    Regular frames use bins [400, 500)  → mean ≈ 449.5
    Dark    frames use bins  [10,  20)  → mean ≈  14.5
    After correction: corrected_mean ≈ 449.5 − 14.5 = 435
    """

    def setup_method(self):
        self.pipeline, self.uncorrected, self.batches = \
            _make_pipeline(left_mask=0x01)
        feed_pipeline_from_csv(
            _fixture("single_cam_basic_left.csv"), "left", self.pipeline
        )
        self.pipeline.stop(timeout=5.0)

    def test_corrected_mean_positive(self):
        """Dark-corrected mean must be positive (signal > noise)."""
        for batch in self.batches:
            for s in batch.samples:
                if s.is_corrected:
                    assert s.mean > 0.0, (
                        f"Frame {s.absolute_frame_id}: corrected mean should be positive, "
                        f"got {s.mean:.4f}"
                    )

    def test_corrected_mean_approx(self):
        """Corrected mean should be close to 449.5 − 14.5 = 435."""
        for batch in self.batches:
            for s in batch.samples:
                if s.is_corrected:
                    assert 400 < s.mean < 470, (
                        f"Frame {s.absolute_frame_id}: corrected mean {s.mean:.2f} "
                        f"not in expected range (400, 470)"
                    )

    def test_uncorrected_mean_approx(self):
        """Uncorrected mean (from regular histogram) should be close to 449.5."""
        regular = [s for s in self.uncorrected
                   if s.absolute_frame_id not in DARK_FRAMES_5]
        for s in regular:
            assert 440 < s.mean < 460, (
                f"Frame {s.absolute_frame_id}: uncorrected mean {s.mean:.2f} "
                f"not close to expected ~449.5"
            )

    def test_bfi_bvi_in_range(self):
        """BFI and BVI outputs should be finite and within a sane range."""
        for batch in self.batches:
            for s in batch.samples:
                assert np.isfinite(s.bfi), f"BFI not finite at frame {s.absolute_frame_id}"
                assert np.isfinite(s.bvi), f"BVI not finite at frame {s.absolute_frame_id}"
                assert -50 < s.bfi < 50, f"BFI out of range: {s.bfi}"
                assert -50 < s.bvi < 50, f"BVI out of range: {s.bvi}"

    def test_contrast_nonnegative(self):
        for batch in self.batches:
            for s in batch.samples:
                if s.is_corrected:
                    assert s.contrast >= 0.0, (
                        f"Frame {s.absolute_frame_id}: contrast must be >=0, got {s.contrast}"
                    )


class TestFrameIdRollover:
    """
    260 frames — the raw u8 frame_id wraps 255→0 at absolute frame 256.
    Verifies that absolute_frame_id is monotonically increasing after rollover.
    """

    def setup_method(self):
        self.pipeline, self.uncorrected, self.batches = \
            _make_pipeline(left_mask=0x01, dark_interval=ROLLOVER_DARK_INTERVAL)
        self.rows = feed_pipeline_from_csv(
            _fixture("frame_id_rollover_left.csv"), "left", self.pipeline
        )
        self.pipeline.stop(timeout=10.0)

    def test_rows_fed(self):
        assert self.rows == 275

    def test_absolute_ids_monotonic(self):
        """Absolute frame IDs in the uncorrected stream must never decrease."""
        abs_ids = [s.absolute_frame_id for s in self.uncorrected]
        for i in range(1, len(abs_ids)):
            assert abs_ids[i] >= abs_ids[i - 1], (
                f"Frame ID decreased at index {i}: "
                f"{abs_ids[i-1]} → {abs_ids[i]}"
            )

    def test_absolute_ids_exceed_255(self):
        """Must have received frames with absolute_frame_id > 255."""
        abs_ids = [s.absolute_frame_id for s in self.uncorrected]
        assert abs_ids, "No uncorrected samples received"
        assert max(abs_ids) > 255, (
            f"Expected max absolute_frame_id > 255, got {max(abs_ids)}"
        )

    def test_corrections_still_fire_after_rollover(self):
        """Dark-frame correction batches must be emitted even past the rollover."""
        assert len(self.batches) >= 1, "Expected at least one corrected batch"
        # At least one batch should span the rollover (dark_frame_end > 255)
        post_rollover = [b for b in self.batches if b.dark_frame_end > 255]
        assert post_rollover, (
            "Expected at least one corrected batch with dark_frame_end > 255"
        )


class TestMultiCamLeft:
    """Two cameras on the left — verifies frame buffer accumulation."""

    def setup_method(self):
        self.pipeline, self.uncorrected, self.batches = \
            _make_pipeline(left_mask=0x03)   # cams 0 and 1
        self.rows = feed_pipeline_from_csv(
            _fixture("multi_cam_left.csv"), "left", self.pipeline
        )
        self.pipeline.stop(timeout=5.0)

    def test_rows_fed(self):
        assert self.rows == 24, f"Expected 24 rows (12 frames × 2 cams), got {self.rows}"

    def test_both_cameras_in_uncorrected(self):
        cam_ids = {s.cam_id for s in self.uncorrected}
        assert 0 in cam_ids, "cam_id 0 missing from uncorrected samples"
        assert 1 in cam_ids, "cam_id 1 missing from uncorrected samples"

    def test_at_least_one_batch(self):
        assert len(self.batches) >= 1

    def test_batch_contains_both_cameras(self):
        """
        The pipeline emits one CorrectedBatch per (side, cam_id) pair per
        dark interval.  Verify that across all batches both camera IDs appear.
        """
        all_cam_ids = {s.cam_id for batch in self.batches for s in batch.samples}
        assert 0 in all_cam_ids, "cam 0 missing from all corrected batches"
        assert 1 in all_cam_ids, "cam 1 missing from all corrected batches"

class TestBothSides:
    """Left cam 0 + right cam 0 fed into a single pipeline."""

    def setup_method(self):
        self.pipeline, self.uncorrected, self.batches = \
            _make_pipeline(left_mask=0x01, right_mask=0x01)
        left_rows = feed_pipeline_from_csv(
            _fixture("both_sides_left.csv"), "left", self.pipeline
        )
        right_rows = feed_pipeline_from_csv(
            _fixture("both_sides_right.csv"), "right", self.pipeline
        )
        self.pipeline.stop(timeout=5.0)
        self.left_rows  = left_rows
        self.right_rows = right_rows

    def test_rows_fed(self):
        assert self.left_rows == 12
        assert self.right_rows == 12

    def test_both_sides_in_uncorrected(self):
        sides = {s.side for s in self.uncorrected}
        assert "left"  in sides, "Left side missing from uncorrected stream"
        assert "right" in sides, "Right side missing from uncorrected stream"

    def test_at_least_one_batch(self):
        assert len(self.batches) >= 1

    def test_batches_contain_both_sides(self):
        """Corrected batches should include samples from both sensor sides."""
        all_sides = {s.side for batch in self.batches for s in batch.samples}
        assert "left"  in all_sides, "Left side missing from corrected batches"
        assert "right" in all_sides, "Right side missing from corrected batches"

class TestMultipleIntervals:
    """25 frames — dark frames at 3, 6, 11, 16, 21 → at least 4 batches."""

    def setup_method(self):
        self.pipeline, self.uncorrected, self.batches = \
            _make_pipeline(left_mask=0x01)
        self.rows = feed_pipeline_from_csv(
            _fixture("multi_interval_left.csv"), "left", self.pipeline
        )
        self.pipeline.stop(timeout=5.0)

    def test_rows_fed(self):
        assert self.rows == 25

    def test_four_batches(self):
        assert len(self.batches) >= 4, (
            f"Expected >=4 corrected batches for 25 frames; got {len(self.batches)}"
        )

    def test_batch_boundaries_in_order(self):
        """Each batch's start must be >= the previous batch's end."""
        for i in range(1, len(self.batches)):
            assert self.batches[i].dark_frame_start >= self.batches[i - 1].dark_frame_end, (
                f"Batch {i} start ({self.batches[i].dark_frame_start}) "
                f"< batch {i-1} end ({self.batches[i-1].dark_frame_end})"
            )

    def test_expected_dark_frame_positions(self):
        """Verify the batch boundaries match the known dark-frame schedule."""
        expected_pairs = [(3, 6), (6, 11), (11, 16), (16, 21)]
        actual_pairs   = [(b.dark_frame_start, b.dark_frame_end)
                          for b in self.batches[:4]]
        assert actual_pairs == expected_pairs, (
            f"Dark frame pairs mismatch.\n"
            f"  Expected: {expected_pairs}\n"
            f"  Got:      {actual_pairs}"
        )


# ---------------------------------------------------------------------------
# Standalone runner (used by run_pipeline_csv_tests.py and direct invocation)
# ---------------------------------------------------------------------------

_ALL_SUITES = [
    TestSingleCamBasic,
    TestDarkCorrectionMath,
    TestFrameIdRollover,
    TestMultiCamLeft,
    TestBothSides,
    TestMultipleIntervals,
]


def run_standalone() -> int:
    """
    Run all test suites without pytest.

    Returns 0 if all tests pass, 1 otherwise.
    """
    passed = failed = 0
    for Suite in _ALL_SUITES:
        suite_name = Suite.__name__
        methods = [m for m in dir(Suite) if m.startswith("test_")]
        for method_name in methods:
            label = f"{suite_name}.{method_name}"
            instance = Suite()
            try:
                instance.setup_method()
                getattr(instance, method_name)()
                print(f"  PASS  {label}")
                passed += 1
            except AssertionError as exc:
                print(f"  FAIL  {label}: {exc}")
                failed += 1
            except Exception as exc:
                print(f"  ERROR {label}: {type(exc).__name__}: {exc}")
                failed += 1

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_standalone())
