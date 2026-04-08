"""
Corrected CSV output verification — real scan data.

Feeds the two real scan CSVs through the pipeline using the same accumulation
logic as ScanWorkflow, writes a corrected CSV to tests/fixtures/, then runs a
battery of sanity checks on the result.

Run standalone (prints a full stats report):
    python tests/test_corrected_csv_output.py

Run with pytest:
    pytest tests/test_corrected_csv_output.py -v -s
"""

import csv
import math
import os
import sys
import threading

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omotion.MotionProcessing import (
    CorrectedBatch,
    Sample,
    create_science_pipeline,
    feed_pipeline_from_csv,
)

# ---------------------------------------------------------------------------
# Paths and calibration
# ---------------------------------------------------------------------------

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

LEFT_CSV  = os.path.join(FIXTURES_DIR, "scan_owC18EHALL_20251217_160949_left_maskFF.csv")
RIGHT_CSV = os.path.join(FIXTURES_DIR, "scan_owC18EHALL_20251217_160949_right_maskFF.csv")

LEFT_MASK  = 0xFF
RIGHT_MASK = 0xFF

# Identity-like calibration so BFI/BVI reflect raw contrast/mean directly.
_ZERO = np.zeros((2, 8), dtype=np.float64)
_ONE  = np.ones((2, 8),  dtype=np.float64)
BFI_C_MIN = _ZERO.copy()
BFI_C_MAX = _ONE.copy()
BFI_I_MIN = _ZERO.copy()
BFI_I_MAX = np.full((2, 8), 1000.0)

# Expected column suffixes given both masks = 0xFF
EXPECTED_SUFFIXES = [f"l{i}" for i in range(1, 9)] + [f"r{i}" for i in range(1, 9)]

CORRECTED_COLUMNS = (
    [f"bfi_l{i}" for i in range(1, 9)]
    + [f"bfi_r{i}" for i in range(1, 9)]
    + [f"bvi_l{i}" for i in range(1, 9)]
    + [f"bvi_r{i}" for i in range(1, 9)]
    + [f"mean_l{i}" for i in range(1, 9)]
    + [f"mean_r{i}" for i in range(1, 9)]
    + [f"std_l{i}" for i in range(1, 9)]
    + [f"std_r{i}" for i in range(1, 9)]
    + [f"contrast_l{i}" for i in range(1, 9)]
    + [f"contrast_r{i}" for i in range(1, 9)]
    + [f"temp_l{i}" for i in range(1, 9)]
    + [f"temp_r{i}" for i in range(1, 9)]
)

OUTPUT_CSV = os.path.join(FIXTURES_DIR, "corrected_output_check.csv")


# ---------------------------------------------------------------------------
# CSV accumulator — mirrors ScanWorkflow._on_corrected_batch logic exactly
# ---------------------------------------------------------------------------

class _CorrectedCsvBuilder:
    def __init__(self):
        self._lock = threading.Lock()
        self._by_frame: dict[int, dict] = {}
        self._base_ts: float | None = None
        self._complete_rows: list[list] = []
        self._partial_rows:  list[list] = []
        self.batch_count  = 0
        self.sample_count = 0

    def on_corrected_batch(self, batch: CorrectedBatch) -> None:
        with self._lock:
            self.batch_count += 1
            for sample in batch.samples:
                self.sample_count += 1
                frame_key  = int(sample.absolute_frame_id)
                col_suffix = f"{sample.side[0]}{int(sample.cam_id) + 1}"
                entry = self._by_frame.get(frame_key)
                if entry is None:
                    entry = {"timestamp_s": float(sample.timestamp_s), "values": {}}
                    self._by_frame[frame_key] = entry
                else:
                    entry["timestamp_s"] = min(
                        float(entry["timestamp_s"]), float(sample.timestamp_s)
                    )
                entry["values"][f"bfi_{col_suffix}"]      = float(sample.bfi)
                entry["values"][f"bvi_{col_suffix}"]      = float(sample.bvi)
                entry["values"][f"mean_{col_suffix}"]     = float(sample.mean)
                entry["values"][f"std_{col_suffix}"]      = float(sample.std_dev)
                entry["values"][f"contrast_{col_suffix}"] = float(sample.contrast)
                entry["values"][f"temp_{col_suffix}"]     = float(sample.temperature_c)

            # Flush rows where all expected cameras have contributed.
            complete = [
                fid for fid, e in self._by_frame.items()
                if all(f"bfi_{s}" in e["values"] for s in EXPECTED_SUFFIXES)
            ]
            if complete:
                if self._base_ts is None:
                    self._base_ts = min(
                        float(self._by_frame[fid]["timestamp_s"]) for fid in complete
                    )
                for fid in sorted(complete):
                    entry = self._by_frame.pop(fid)
                    rel_ts = float(entry["timestamp_s"]) - self._base_ts
                    row = [fid, rel_ts]
                    row.extend(entry["values"].get(col, "") for col in CORRECTED_COLUMNS)
                    self._complete_rows.append(row)

    def flush_remaining(self) -> None:
        with self._lock:
            if not self._by_frame:
                return
            if self._base_ts is None:
                self._base_ts = min(
                    float(e["timestamp_s"]) for e in self._by_frame.values()
                )
            for fid in sorted(self._by_frame.keys()):
                entry = self._by_frame[fid]
                rel_ts = float(entry["timestamp_s"]) - self._base_ts
                row = [fid, rel_ts]
                row.extend(entry["values"].get(col, "") for col in CORRECTED_COLUMNS)
                self._partial_rows.append(row)
            self._by_frame.clear()

    def write_csv(self, path: str) -> None:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["frame_id", "timestamp_s", *CORRECTED_COLUMNS])
            for row in self._complete_rows:
                w.writerow(row)
            for row in self._partial_rows:
                w.writerow(row)

    @property
    def all_rows(self) -> list[list]:
        return self._complete_rows + self._partial_rows


# ---------------------------------------------------------------------------
# Core build function
# ---------------------------------------------------------------------------

def build_corrected_csv() -> tuple["_CorrectedCsvBuilder", list[dict], list[str]]:
    """
    Run the pipeline on both real scan CSVs, write corrected CSV to
    OUTPUT_CSV, and return (builder, rows, header).
    """
    builder = _CorrectedCsvBuilder()

    pipeline = create_science_pipeline(
        left_camera_mask=LEFT_MASK,
        right_camera_mask=RIGHT_MASK,
        bfi_c_min=BFI_C_MIN,
        bfi_c_max=BFI_C_MAX,
        bfi_i_min=BFI_I_MIN,
        bfi_i_max=BFI_I_MAX,
        on_corrected_batch_fn=builder.on_corrected_batch,
    )

    builder.left_rows  = feed_pipeline_from_csv(LEFT_CSV,  "left",  pipeline)
    builder.right_rows = feed_pipeline_from_csv(RIGHT_CSV, "right", pipeline)
    pipeline.stop(timeout=120.0)
    builder.flush_remaining()
    builder.write_csv(OUTPUT_CSV)

    with open(OUTPUT_CSV, "r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        header = list(reader.fieldnames or [])
        rows   = list(reader)

    return builder, rows, header


def _col_values(rows: list[dict], col: str) -> list[float]:
    return [float(r[col]) for r in rows if r.get(col, "") != ""]


# ---------------------------------------------------------------------------
# pytest fixture
# ---------------------------------------------------------------------------

def _skip_if_missing():
    for path in (LEFT_CSV, RIGHT_CSV):
        if not os.path.isfile(path):
            pytest.skip(f"Real scan fixture not found: {os.path.basename(path)}")


@pytest.fixture(scope="module")
def corrected(request):
    _skip_if_missing()
    builder, rows, header = build_corrected_csv()
    return {"builder": builder, "rows": rows, "header": header}


# ---------------------------------------------------------------------------
# Tests — structure
# ---------------------------------------------------------------------------

def test_header_has_expected_columns(corrected):
    expected = {"frame_id", "timestamp_s", *CORRECTED_COLUMNS}
    actual   = set(corrected["header"])
    missing  = expected - actual
    extra    = actual - expected
    assert not missing and not extra, (
        f"Header mismatch — missing: {missing}, extra: {extra}"
    )


def test_temperature_columns_present(corrected):
    """All 16 per-camera temperature columns must be in the header."""
    header = corrected["header"]
    missing = [f"temp_{s}" for s in EXPECTED_SUFFIXES if f"temp_{s}" not in header]
    assert not missing, f"Temperature columns missing from header: {missing}"


def test_row_count(corrected):
    """At least 100 rows — enough to cover one complete dark interval."""
    n = len(corrected["rows"])
    assert n >= 100, f"Only {n} rows produced (expected >= 100)"


def test_complete_rows_dominate(corrected):
    """
    Most rows should be complete (all cameras present); partial rows only
    appear at scan end when the final dark interval was not closed.
    """
    b = corrected["builder"]
    total = b.batch_count  # proxy — actual row split is in builder
    complete = len(b._complete_rows)
    partial  = len(b._partial_rows)
    assert complete + partial > 0
    frac = complete / (complete + partial)
    assert frac >= 0.5, (
        f"Only {frac:.1%} of rows are complete ({complete} complete, {partial} partial)"
    )


# ---------------------------------------------------------------------------
# Tests — frame ordering and timestamps
# ---------------------------------------------------------------------------

def test_frame_id_strictly_increasing(corrected):
    rows = corrected["rows"]
    ids = [int(r["frame_id"]) for r in rows]
    violations = [
        (i, ids[i], ids[i + 1])
        for i in range(len(ids) - 1)
        if ids[i] >= ids[i + 1]
    ]
    assert not violations, f"frame_id not strictly increasing at: {violations[:3]}"


def test_timestamp_non_decreasing(corrected):
    rows = corrected["rows"]
    ts = [float(r["timestamp_s"]) for r in rows]
    violations = [i for i in range(len(ts) - 1) if ts[i] > ts[i + 1]]
    assert not violations, f"timestamp_s decreases at rows: {violations[:3]}"


def test_timestamp_starts_near_zero(corrected):
    first = float(corrected["rows"][0]["timestamp_s"])
    assert abs(first) < 1.0, f"First timestamp {first:.4f}s is not near zero"


def test_timestamp_end_is_plausible(corrected):
    last = float(corrected["rows"][-1]["timestamp_s"])
    assert 1.0 < last < 30.0, f"Last timestamp {last:.3f}s is outside range (1, 30) s"


# ---------------------------------------------------------------------------
# Tests — data quality
# ---------------------------------------------------------------------------

def test_no_blank_cells(corrected):
    """Every camera-metric cell must be populated for a complete row."""
    rows = corrected["rows"]
    blank = {
        col: sum(1 for r in rows if r.get(col, "") == "")
        for col in CORRECTED_COLUMNS
    }
    bad = {col: n for col, n in blank.items() if n > 0}
    assert not bad, f"{len(bad)} columns have blank cells: {list(bad.items())[:5]}"


def test_no_nan_or_inf(corrected):
    rows = corrected["rows"]
    nan_cols = [c for c in CORRECTED_COLUMNS if any(math.isnan(v) for v in _col_values(rows, c))]
    inf_cols = [c for c in CORRECTED_COLUMNS if any(math.isinf(v) for v in _col_values(rows, c))]
    assert not nan_cols, f"NaN found in columns: {nan_cols[:5]}"
    assert not inf_cols, f"Inf found in columns: {inf_cols[:5]}"


def test_corrected_means_mostly_positive(corrected):
    """
    Corrected mean must be > 0 for >= 95% of rows per camera.  A tiny
    fraction (< 5%) may go slightly negative at dark-frame interpolation
    boundaries where signal ≈ dark baseline — this is expected.
    """
    rows = corrected["rows"]
    bad = []
    for s in EXPECTED_SUFFIXES:
        vals = _col_values(rows, f"mean_{s}")
        frac_pos = sum(1 for v in vals if v > 0.0) / len(vals) if vals else 0
        if frac_pos < 0.95:
            bad.append((f"mean_{s}", f"{frac_pos:.1%}"))
    assert not bad, f"<95% positive means in columns: {bad}"


def test_corrected_means_not_deeply_negative(corrected):
    """
    Any negative corrected mean must be very small in magnitude (< 1.0).
    Values more negative than -1.0 indicate a correction bug.
    """
    rows = corrected["rows"]
    bad = []
    for s in EXPECTED_SUFFIXES:
        vals = _col_values(rows, f"mean_{s}")
        deep = [v for v in vals if v < -1.0]
        if deep:
            bad.append((f"mean_{s}", min(deep)))
    assert not bad, f"Corrected mean < -1.0 found: {bad[:5]}"


def test_std_nonnegative(corrected):
    rows = corrected["rows"]
    bad = [s for s in EXPECTED_SUFFIXES
           if any(v < 0.0 for v in _col_values(rows, f"std_{s}"))]
    assert not bad, f"Negative std_dev in columns: {[f'std_{s}' for s in bad]}"


def test_contrast_nonnegative(corrected):
    rows = corrected["rows"]
    bad = [s for s in EXPECTED_SUFFIXES
           if any(v < 0.0 for v in _col_values(rows, f"contrast_{s}"))]
    assert not bad, f"Negative contrast in columns: {[f'contrast_{s}' for s in bad]}"


def test_bfi_mostly_in_range(corrected):
    """
    >= 95% of BFI values per camera must be in (-50, 50).  The small
    remainder are dark-frame boundary rows where contrast diverges under
    identity calibration with near-zero corrected mean.
    """
    rows = corrected["rows"]
    bad = []
    for s in EXPECTED_SUFFIXES:
        vals = _col_values(rows, f"bfi_{s}")
        frac_ok = sum(1 for v in vals if -50.0 < v < 50.0) / len(vals) if vals else 0
        if frac_ok < 0.95:
            bad.append((f"bfi_{s}", f"{frac_ok:.1%} in range"))
    assert not bad, f"<95% of BFI in (-50,50): {bad}"


def test_pipeline_output_varies(corrected):
    """bfi_l1 and bvi_l1 must not be constant — pipeline is doing real work."""
    rows = corrected["rows"]
    for col in ("bfi_l1", "bvi_l1"):
        vals = _col_values(rows, col)
        n_unique = len(set(round(v, 6) for v in vals))
        assert n_unique > 1, f"{col} is constant across all rows — pipeline output is degenerate"


# ---------------------------------------------------------------------------
# Tests — temperature specifically
# ---------------------------------------------------------------------------

def test_temperatures_populated(corrected):
    """All 16 temperature columns must have values in every row."""
    rows = corrected["rows"]
    blank = {
        f"temp_{s}": sum(1 for r in rows if r.get(f"temp_{s}", "") == "")
        for s in EXPECTED_SUFFIXES
    }
    bad = {col: n for col, n in blank.items() if n > 0}
    assert not bad, f"Temperature columns have blank cells: {bad}"


def test_temperatures_plausible(corrected):
    """
    Camera temperatures should be in the range (0, 80) °C.
    The OV2312 operating range is 0-70°C; allow a little margin.
    """
    rows = corrected["rows"]
    bad = []
    for s in EXPECTED_SUFFIXES:
        vals = _col_values(rows, f"temp_{s}")
        oor = [v for v in vals if not (0.0 < v < 80.0)]
        if oor:
            bad.append((f"temp_{s}", len(oor), min(oor), max(oor)))
    assert not bad, f"Temperature values outside (0, 80) °C: {bad[:5]}"


def test_temperatures_not_all_identical(corrected):
    """
    Temperatures should vary over the scan (sensor warms up slightly).
    A perfectly constant temperature column would indicate the field is
    not being populated correctly.
    """
    rows = corrected["rows"]
    flat = []
    for s in EXPECTED_SUFFIXES:
        vals = _col_values(rows, f"temp_{s}")
        if len(set(round(v, 3) for v in vals)) == 1:
            flat.append(f"temp_{s}")
    assert not flat, (
        f"Temperature is identical across all rows for: {flat} "
        f"— temperature_c may not be stored correctly"
    )


# ---------------------------------------------------------------------------
# Report + standalone runner
# ---------------------------------------------------------------------------

def test_print_report(corrected, capsys):
    """Print summary stats (visible with pytest -s)."""
    builder = corrected["builder"]
    rows    = corrected["rows"]

    print()
    print("=" * 62)
    print("  Corrected CSV output verification")
    print("=" * 62)
    print(f"  Output file:            {OUTPUT_CSV}")
    print(f"  Left rows fed:          {builder.left_rows:,}")
    print(f"  Right rows fed:         {builder.right_rows:,}")
    print(f"  Corrected batches:      {builder.batch_count}")
    print(f"  Total samples:          {builder.sample_count:,}")
    print(f"  Complete rows:          {len(builder._complete_rows)}")
    print(f"  Partial rows:           {len(builder._partial_rows)}")
    print(f"  Total rows:             {len(rows)}")
    if rows:
        ids = [int(r["frame_id"]) for r in rows]
        tss = [float(r["timestamp_s"]) for r in rows]
        print(f"  frame_id range:         {ids[0]} .. {ids[-1]}")
        print(f"  timestamp_s range:      {tss[0]:.4f} s .. {tss[-1]:.3f} s")

    print()
    print(f"  {'Column':<20} {'N':>6}  {'Min':>9}  {'Mean':>9}  {'Max':>9}")
    print("  " + "-" * 58)
    sample_cols = [
        "mean_l1",     "mean_r1",
        "contrast_l1", "contrast_r1",
        "bfi_l1",      "bfi_r1",
        "bvi_l1",      "bvi_r1",
        "temp_l1",     "temp_r1",
    ]
    for col in sample_cols:
        vals = _col_values(rows, col)
        if vals:
            print(
                f"  {col:<20} {len(vals):>6}  "
                f"{min(vals):>9.4f}  "
                f"{sum(vals)/len(vals):>9.4f}  "
                f"{max(vals):>9.4f}"
            )
    print("=" * 62)


if __name__ == "__main__":
    for path in (LEFT_CSV, RIGHT_CSV):
        if not os.path.isfile(path):
            print(f"ERROR: fixture not found: {path}")
            sys.exit(1)

    print("Building corrected CSV from real scan data...")
    builder, rows, header = build_corrected_csv()

    print(f"Output:          {OUTPUT_CSV}")
    print(f"Rows written:    {len(rows)}")
    print(f"Batches:         {builder.batch_count}")
    print(f"Samples:         {builder.sample_count:,}")
    if rows:
        ids = [int(r["frame_id"]) for r in rows]
        tss = [float(r["timestamp_s"]) for r in rows]
        print(f"frame_id range:  {ids[0]} .. {ids[-1]}")
        print(f"timestamp range: {tss[0]:.4f}s .. {tss[-1]:.3f}s")
    print()

    sample_cols = [
        "mean_l1", "mean_r1", "contrast_l1", "contrast_r1",
        "bfi_l1",  "bfi_r1",  "bvi_l1",      "bvi_r1",
        "temp_l1", "temp_r1",
    ]
    print(f"  {'Column':<20} {'N':>6}  {'Min':>9}  {'Mean':>9}  {'Max':>9}")
    print("  " + "-" * 58)
    for col in sample_cols:
        vals = _col_values(rows, col)
        if vals:
            print(
                f"  {col:<20} {len(vals):>6}  "
                f"{min(vals):>9.4f}  {sum(vals)/len(vals):>9.4f}  {max(vals):>9.4f}"
            )
