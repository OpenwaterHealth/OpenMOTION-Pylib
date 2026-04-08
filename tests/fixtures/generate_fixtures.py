"""
Generate test fixture CSV files for the science pipeline tests.

Run this script to (re)create all fixture CSVs in the fixtures/ directory:

    python tests/fixtures/generate_fixtures.py

Each fixture is a raw histogram CSV matching the format written by ScanWorkflow:

    cam_id, frame_id, timestamp_s, 0, 1, ..., 1023, temperature, sum

Fixture conventions
-------------------
- DISCARD_COUNT = 2  → frames 1–2 are warmup and must be discarded by the pipeline
- DARK_INTERVAL  = 5 → dark frames are at absolute positions 3, 6, 11, 16, 21 ...
  (position 3 = discard_count+1; subsequent: (abs-1) % 5 == 0)
- Regular frames use a uniform histogram over bins [400, 500)  (mean ≈ 449.5)
- Dark    frames use a uniform histogram over bins  [10,  20)  (mean ≈  14.5)
- Total photons per frame = TOTAL_PHOTONS (not the hardware sum 2,457,606 —
  tests must pass expected_row_sum=None to the pipeline)

Fixture files
-------------
single_cam_basic_left.csv
    Left cam 0, 12 frames. Covers one full correction cycle (3→6) and one
    additional dark interval (6→11) so two CorrectedBatches are expected.

multi_cam_left.csv
    Left cams 0 and 1, 12 frames. Same frame schedule as basic; tests that
    both cameras are handled and science frames are assembled correctly.

both_sides_left.csv / both_sides_right.csv
    Left cam 0 and right cam 0, 12 frames each, identical timestamps.
    Feed both into the same pipeline to test cross-side alignment.

frame_id_rollover_left.csv
    Left cam 0, 275 frames, dark_interval=10. Absolute frame IDs 1–275 wrap
    the raw u8 counter (255->0) at absolute frame 256.  Dark frames at 3, 11,
    21, ..., 261, 271 — the interval 261->271 has both bounding dark frames at
    absolute_frame_id > 255, verifying FrameIdUnwrapper and that corrections
    fire correctly after the rollover.

multi_interval_left.csv
    Left cam 0, 25 frames. Provides four complete dark intervals (3→6,
    6→11, 11→16, 16→21) so at least 4 CorrectedBatches are expected.
"""

import csv
import os

HISTO_SIZE_WORDS = 1024

DISCARD_COUNT  = 2
DARK_INTERVAL  = 5
TOTAL_PHOTONS  = 2_457_606   # must match EXPECTED_HISTOGRAM_SUM in MotionProcessing.py

REGULAR_LO, REGULAR_HI = 400, 500   # 100-bin uniform  (mean ≈ 449.5)
DARK_LO,    DARK_HI    =  10,  20   #  10-bin uniform  (mean ≈  14.5)

FRAME_INTERVAL_S = 0.025  # 40 Hz

FIXTURES_DIR = os.path.dirname(os.path.abspath(__file__))

_HEADERS = ["cam_id", "frame_id", "timestamp_s",
            *range(HISTO_SIZE_WORDS), "temperature", "sum"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_dark_frame(absolute_frame: int,
                   discard_count: int = DISCARD_COUNT,
                   dark_interval: int = DARK_INTERVAL) -> bool:
    """Mirror of SciencePipeline._is_dark_frame."""
    if absolute_frame == discard_count + 1:
        return True
    return (
        absolute_frame > discard_count + 1
        and (absolute_frame - 1) % dark_interval == 0
    )


def _make_histogram(lo: int, hi: int, total: int) -> list[int]:
    """Uniform distribution over bins [lo, hi) summing to *total*."""
    bins = [0] * HISTO_SIZE_WORDS
    n = hi - lo
    per_bin, extra = divmod(total, n)
    for i in range(lo, hi):
        bins[i] = per_bin
    bins[lo] += extra
    assert sum(bins) == total
    return bins


def _write_csv(
    filename: str,
    camera_ids: list[int],
    num_frames: int,
    start_absolute: int = 1,
    discard_count: int = DISCARD_COUNT,
    dark_interval: int = DARK_INTERVAL,
) -> None:
    path = os.path.join(FIXTURES_DIR, filename)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(_HEADERS)
        for frame_idx in range(num_frames):
            abs_frame  = start_absolute + frame_idx
            raw_fid    = abs_frame % 256          # u8 wrap — matches firmware
            ts         = frame_idx * FRAME_INTERVAL_S
            dark       = _is_dark_frame(abs_frame, discard_count, dark_interval)
            bins       = (_make_histogram(DARK_LO, DARK_HI, TOTAL_PHOTONS)
                          if dark else
                          _make_histogram(REGULAR_LO, REGULAR_HI, TOTAL_PHOTONS))
            for cam_id in camera_ids:
                w.writerow([cam_id, raw_fid, ts, *bins, 25.0, TOTAL_PHOTONS])
    print(f"  wrote {filename}  ({num_frames} frames, cams {camera_ids})")


# ---------------------------------------------------------------------------
# Fixture definitions
# ---------------------------------------------------------------------------

def generate_all() -> None:
    print(f"Generating fixtures in: {FIXTURES_DIR}\n")

    # 1. single_cam_basic_left.csv — 12 frames, cam 0
    #    Dark frames at 3, 6, 11  → batches 3→6 and 6→11
    _write_csv("single_cam_basic_left.csv",
               camera_ids=[0], num_frames=12)

    # 2. multi_cam_left.csv — 12 frames, cams 0 and 1
    _write_csv("multi_cam_left.csv",
               camera_ids=[0, 1], num_frames=12)

    # 3. both_sides_left.csv / both_sides_right.csv — 12 frames, cam 0 each
    _write_csv("both_sides_left.csv",
               camera_ids=[0], num_frames=12)
    _write_csv("both_sides_right.csv",
               camera_ids=[0], num_frames=12)

    # 4. frame_id_rollover_left.csv — 275 frames, cam 0
    #    dark_interval=10: darks at 3, 11, 21, ..., 261, 271
    #    raw frame_id rolls over 255→0 at absolute frame 256.
    #    The interval 261→271 has both dark frames at absolute_frame_id > 255,
    #    so corrections must fire correctly after the rollover.
    _write_csv("frame_id_rollover_left.csv",
               camera_ids=[0], num_frames=275,
               dark_interval=10)

    # 5. multi_interval_left.csv — 25 frames, cam 0
    #    Dark frames at 3, 6, 11, 16, 21  → 4 complete batches
    _write_csv("multi_interval_left.csv",
               camera_ids=[0], num_frames=25)

    print("\nDone.")


if __name__ == "__main__":
    generate_all()
