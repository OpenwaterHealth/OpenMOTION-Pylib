"""
Science pipeline performance test — real captured scan data.

Feeds the two real scan CSVs from tests/fixtures/ through the pipeline and
reports timing and throughput statistics.  No assertions are made about
correctness here (the fixture tests cover that); the goal is to confirm the
pipeline can sustain real-time throughput and to surface any regressions.

Run standalone (prints a full stats report):
    python tests/test_pipeline_perf.py

Run with pytest (records stats, asserts real-time threshold):
    pytest tests/test_pipeline_perf.py -v -s
"""

import os
import sys
import time
import threading
import statistics

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omotion.MotionProcessing import (
    CorrectedBatch,
    CorrectedSample,
    ScienceFrame,
    create_science_pipeline,
    feed_pipeline_from_csv,
)

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

LEFT_CSV  = os.path.join(FIXTURES_DIR, "scan_owC18EHALL_20251217_160949_left_maskFF.csv")
RIGHT_CSV = os.path.join(FIXTURES_DIR, "scan_owC18EHALL_20251217_160949_right_maskFF.csv")

# Both sides, all 8 cameras
LEFT_MASK  = 0xFF
RIGHT_MASK = 0xFF

# Calibration: identity-like (does not affect timing).
_ZERO = np.zeros((2, 8), dtype=np.float64)
_ONE  = np.ones((2, 8),  dtype=np.float64)
BFI_C_MIN = _ZERO.copy()
BFI_C_MAX = _ONE.copy()
BFI_I_MIN = _ZERO.copy()
BFI_I_MAX = np.full((2, 8), 1000.0)

# Real-time factor threshold: pipeline must process data at least this many
# times faster than the original capture rate (1.0 = exactly real-time).
MIN_REALTIME_FACTOR = 2.0

# Capture rate of the original data (Hz).
CAPTURE_HZ = 40.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _skip_if_missing():
    for path in (LEFT_CSV, RIGHT_CSV):
        if not os.path.isfile(path):
            pytest.skip(f"Real scan fixture not found: {os.path.basename(path)}")


class _PerfCollector:
    """Thread-safe collector for callback timing and sample counts."""

    def __init__(self):
        self._lock = threading.Lock()
        self.uncorrected_times: list[float] = []   # perf_counter at each callback
        self.batches:           list[CorrectedBatch] = []
        self.batch_times:       list[float] = []   # perf_counter when batch arrived
        self.science_frames:    list[ScienceFrame] = []
        self.science_frame_times: list[float] = []

    def on_uncorrected(self, sample: CorrectedSample) -> None:
        t = time.perf_counter()
        with self._lock:
            self.uncorrected_times.append(t)

    def on_corrected_batch(self, batch: CorrectedBatch) -> None:
        t = time.perf_counter()
        with self._lock:
            self.batches.append(batch)
            self.batch_times.append(t)

    def on_science_frame(self, frame: ScienceFrame) -> None:
        t = time.perf_counter()
        with self._lock:
            self.science_frames.append(frame)
            self.science_frame_times.append(t)


def _fmt(label: str, value: str, width: int = 36) -> str:
    return f"  {label:<{width}} {value}"


def _ms(seconds: float) -> str:
    return f"{seconds * 1000:.2f} ms"


def _interval_stats(times: list[float]) -> dict:
    """Compute gap statistics between consecutive timestamps."""
    if len(times) < 2:
        return {}
    gaps = [times[i] - times[i - 1] for i in range(1, len(times))]
    return {
        "count":  len(gaps),
        "min":    min(gaps),
        "max":    max(gaps),
        "mean":   statistics.mean(gaps),
        "median": statistics.median(gaps),
        "stdev":  statistics.stdev(gaps) if len(gaps) > 1 else 0.0,
        "p95":    sorted(gaps)[int(len(gaps) * 0.95)],
        "p99":    sorted(gaps)[int(len(gaps) * 0.99)],
    }


# ---------------------------------------------------------------------------
# Core measurement function (shared by pytest and standalone runner)
# ---------------------------------------------------------------------------

def run_perf_test() -> dict:
    """
    Run the pipeline against both real scan CSVs and return a stats dict.

    Timing breakdown
    ----------------
    csv_load_s
        Wall time to read both CSV files and enqueue all rows.
        (Includes Python CSV parsing, numpy array construction, queue.put().)
    drain_s
        Time from the last enqueue to pipeline.stop() returning — i.e. the
        pipeline worker thread's queued backlog being processed.
    total_s
        csv_load_s + drain_s (end-to-end latency seen by a caller).
    realtime_factor
        scan_duration_s / total_s.  A value > 1 means faster than real-time.
    """
    collector = _PerfCollector()

    pipeline = create_science_pipeline(
        left_camera_mask=LEFT_MASK,
        right_camera_mask=RIGHT_MASK,
        bfi_c_min=BFI_C_MIN,
        bfi_c_max=BFI_C_MAX,
        bfi_i_min=BFI_I_MIN,
        bfi_i_max=BFI_I_MAX,
        on_uncorrected_fn=collector.on_uncorrected,
        on_corrected_batch_fn=collector.on_corrected_batch,
        on_science_frame_fn=collector.on_science_frame,
        frame_timeout_s=0.5,
    )

    # --- CSV ingestion -------------------------------------------------------
    t_load_start = time.perf_counter()
    left_rows  = feed_pipeline_from_csv(LEFT_CSV,  "left",  pipeline)
    right_rows = feed_pipeline_from_csv(RIGHT_CSV, "right", pipeline)
    t_load_end = time.perf_counter()
    csv_load_s = t_load_end - t_load_start

    total_rows = left_rows + right_rows

    # --- Pipeline drain ------------------------------------------------------
    t_drain_start = time.perf_counter()
    pipeline.stop(timeout=120.0)
    t_drain_end = time.perf_counter()
    drain_s = t_drain_end - t_drain_start

    total_s = csv_load_s + drain_s

    # --- Derived stats -------------------------------------------------------
    # Approximate scan duration from per-camera frame count.
    frames_per_cam = left_rows // 8   # 8 cameras, all in mask
    scan_duration_s = frames_per_cam / CAPTURE_HZ
    realtime_factor = scan_duration_s / total_s if total_s > 0 else float("inf")

    # Uncorrected callback interval stats (tells us how smooth the output is).
    unc_stats = _interval_stats(collector.uncorrected_times)

    # Corrected batch sizes.
    batch_sizes = [len(b.samples) for b in collector.batches]

    # Science frame completeness: fraction of frames where all 16 cameras showed up.
    all_keys = frozenset(
        (side, cam_id)
        for side in ("left", "right")
        for cam_id in range(8)
    )
    complete_frames = sum(
        1 for sf in collector.science_frames
        if all_keys.issubset(sf.samples.keys())
    )

    return {
        # Inputs
        "left_rows":          left_rows,
        "right_rows":         right_rows,
        "total_rows":         total_rows,
        "frames_per_cam":     frames_per_cam,
        "scan_duration_s":    scan_duration_s,
        "num_cameras":        16,

        # Timing
        "csv_load_s":         csv_load_s,
        "drain_s":            drain_s,
        "total_s":            total_s,
        "throughput_rows_s":  total_rows / total_s if total_s > 0 else 0,
        "realtime_factor":    realtime_factor,

        # Outputs
        "uncorrected_count":  len(collector.uncorrected_times),
        "batch_count":        len(collector.batches),
        "science_frame_count": len(collector.science_frames),
        "complete_frame_count": complete_frames,

        # Uncorrected callback intervals
        "unc_interval_stats": unc_stats,

        # Corrected batch sizes
        "batch_sizes":        batch_sizes,
        "batch_size_mean":    statistics.mean(batch_sizes) if batch_sizes else 0,
        "batch_size_min":     min(batch_sizes) if batch_sizes else 0,
        "batch_size_max":     max(batch_sizes) if batch_sizes else 0,
    }


# ---------------------------------------------------------------------------
# Pretty-print report
# ---------------------------------------------------------------------------

def print_report(stats: dict) -> None:
    W = 38
    print()
    print("=" * 60)
    print("  OpenMOTION pipeline performance report")
    print("=" * 60)

    print("\n  -- Input --")
    print(_fmt("Left rows:",                  f"{stats['left_rows']:,}", W))
    print(_fmt("Right rows:",                 f"{stats['right_rows']:,}", W))
    print(_fmt("Total rows:",                 f"{stats['total_rows']:,}", W))
    print(_fmt("Cameras (both sides):",       f"{stats['num_cameras']}", W))
    print(_fmt("Frames per camera:",          f"{stats['frames_per_cam']}", W))
    print(_fmt("Original scan duration:",     f"{stats['scan_duration_s']:.2f} s  "
                                              f"(@ {CAPTURE_HZ:.0f} Hz)", W))

    print("\n  -- Timing --")
    print(_fmt("CSV load + enqueue time:",    f"{stats['csv_load_s']:.3f} s", W))
    print(_fmt("Pipeline drain time:",        f"{stats['drain_s']:.3f} s", W))
    print(_fmt("Total processing time:",      f"{stats['total_s']:.3f} s", W))
    print(_fmt("Throughput:",                 f"{stats['throughput_rows_s']:,.0f} rows/s", W))
    print(_fmt("Real-time factor:",           f"{stats['realtime_factor']:.2f}x  "
                                              f"({'OK' if stats['realtime_factor'] >= MIN_REALTIME_FACTOR else 'SLOW'})", W))

    print("\n  -- Pipeline output --")
    print(_fmt("Uncorrected callbacks:",      f"{stats['uncorrected_count']:,}", W))
    print(_fmt("Corrected batches emitted:",  f"{stats['batch_count']}", W))
    print(_fmt("Science frames emitted:",     f"{stats['science_frame_count']:,}", W))
    print(_fmt("Complete science frames",
               f"(all 16 cams):",             W))
    print(_fmt("",                            f"{stats['complete_frame_count']:,}  "
                                              f"({100*stats['complete_frame_count']/max(1,stats['science_frame_count']):.1f}%)", W))

    s = stats["unc_interval_stats"]
    if s:
        print("\n  -- Uncorrected callback intervals --")
        print(_fmt("Count (gaps):",           f"{s['count']:,}", W))
        print(_fmt("Min:",                    _ms(s["min"]), W))
        print(_fmt("Max:",                    _ms(s["max"]), W))
        print(_fmt("Mean:",                   _ms(s["mean"]), W))
        print(_fmt("Median:",                 _ms(s["median"]), W))
        print(_fmt("Std dev:",                _ms(s["stdev"]), W))
        print(_fmt("p95:",                    _ms(s["p95"]), W))
        print(_fmt("p99:",                    _ms(s["p99"]), W))
        expected_gap_ms = (1.0 / CAPTURE_HZ) * 1000
        print(_fmt("Expected gap at 40 Hz:",  f"{expected_gap_ms:.2f} ms", W))

    if stats["batch_sizes"]:
        print("\n  -- Corrected batch sizes (samples per batch) --")
        print(_fmt("Batches:",                f"{stats['batch_count']}", W))
        print(_fmt("Min samples:",            f"{stats['batch_size_min']}", W))
        print(_fmt("Max samples:",            f"{stats['batch_size_max']}", W))
        print(_fmt("Mean samples:",           f"{stats['batch_size_mean']:.1f}", W))

    print()
    print("=" * 60)


# ---------------------------------------------------------------------------
# pytest tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def perf_stats():
    _skip_if_missing()
    return run_perf_test()


def test_all_rows_ingested(perf_stats):
    """All rows from both CSVs must reach the pipeline."""
    assert perf_stats["left_rows"]  == 5240, f"Left:  expected 5240, got {perf_stats['left_rows']}"
    assert perf_stats["right_rows"] == 5240, f"Right: expected 5240, got {perf_stats['right_rows']}"


def test_uncorrected_callbacks_received(perf_stats):
    """Pipeline must fire uncorrected callbacks for non-warmup, non-dark frames."""
    assert perf_stats["uncorrected_count"] > 0, "No uncorrected callbacks received"


def test_corrected_batches_emitted(perf_stats):
    """At least one corrected batch must be emitted for a 16-second scan."""
    assert perf_stats["batch_count"] >= 1, "No corrected batches emitted"


def test_science_frames_emitted(perf_stats):
    """Science frames (cross-side alignment) must be assembled."""
    assert perf_stats["science_frame_count"] > 0, "No science frames emitted"


def test_realtime_factor(perf_stats):
    """Pipeline must process data at least MIN_REALTIME_FACTOR times faster than real-time."""
    factor = perf_stats["realtime_factor"]
    assert factor >= MIN_REALTIME_FACTOR, (
        f"Pipeline too slow: {factor:.2f}x real-time "
        f"(threshold: {MIN_REALTIME_FACTOR:.1f}x)\n"
        f"  Scan duration:      {perf_stats['scan_duration_s']:.2f}s\n"
        f"  Processing time:    {perf_stats['total_s']:.3f}s\n"
        f"  CSV load:           {perf_stats['csv_load_s']:.3f}s\n"
        f"  Pipeline drain:     {perf_stats['drain_s']:.3f}s"
    )


def test_callback_interval_p99(perf_stats):
    """99th-percentile uncorrected callback gap must be under 500 ms."""
    s = perf_stats["unc_interval_stats"]
    if not s:
        pytest.skip("Not enough callbacks to compute interval stats")
    p99_ms = s["p99"] * 1000
    assert p99_ms < 500, (
        f"p99 callback gap {p99_ms:.1f} ms exceeds 500 ms — "
        f"pipeline may be stalling"
    )


def test_print_report(perf_stats, capsys):
    """Print the full stats report (visible with pytest -s)."""
    print_report(perf_stats)


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    for path in (LEFT_CSV, RIGHT_CSV):
        if not os.path.isfile(path):
            print(f"ERROR: fixture not found: {path}")
            sys.exit(1)

    print("Running pipeline performance test...")
    stats = run_perf_test()
    print_report(stats)

    ok = stats["realtime_factor"] >= MIN_REALTIME_FACTOR
    sys.exit(0 if ok else 1)
