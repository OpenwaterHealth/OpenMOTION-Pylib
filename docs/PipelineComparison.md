# Pipeline Comparison: Legacy vs SDK Science Pipeline

## Overview

This document summarises findings from comparing the legacy `VisualizeBloodflow` pipeline
(in `openmotion-bloodflow-app/processing/visualize_bloodflow.py`) against the current
`SciencePipeline` (in `omotion/MotionProcessing.py`), and catalogues the scripts and
tests written during this work.

---

## Pipelines Compared

| | Legacy (`VisualizeBloodflow`) | SDK (`SciencePipeline`) |
|---|---|---|
| **Location** | `openmotion-bloodflow-app/processing/visualize_bloodflow.py` | `omotion/MotionProcessing.py` |
| **Inputs** | Left/right raw histogram CSVs | Raw histogram CSV, fed row-by-row via `feed_pipeline_from_csv` |
| **Processing** | Batch (entire scan at once) | Streaming (per-frame, emits corrected batches per dark interval) |
| **Dark interval** | 600 frames (default) | 600 frames (default) |
| **Discard count** | Frames at index 0–8 discarded; frame 9 used as first dark anchor | Frames 1–9 discarded; frame 10 is first dark anchor |
| **Noise floor** | Bins < 10 zeroed; bin 0 additionally has 6 subtracted | Bins < 10 zeroed; no bin-0 subtraction |
| **Dark interpolation** | Linear interpolation of dark u₁ and var across all frames in interval | Linear interpolation of dark u₁ and var per frame within interval |
| **Dark frame infill** | Quadratic interpolation at dark frame positions (not in output) | Dark frame value interpolated between adjacent non-dark corrected samples |
| **Calibration constants** | `C_min=0`, `C_max` per-camera (0.40–0.55), `I_min=0`, `I_max` per-camera (150–300) | Same constants, sourced directly from `VisualizeBloodflow` defaults |
| **BFI formula** | `(1 − (K − C_min) / C_den) × 10` | Same |
| **BVI formula** | `(1 − (μ − I_min) / I_den) × 10` | Same |
| **Clamping** | None | None |

---

## Key Findings

### Mean (μ₁) — essentially identical
Both pipelines agree on dark-corrected mean intensity within ~1–5 histogram counts across
all cameras. The difference standard deviation is ~0.5–1.2 over the full scan. The small
offset is attributable to the bin-0 subtraction in the legacy pipeline (subtracts 6 from
bin 0 before moment computation) and the slightly different dark frame anchor (frame 9 vs
frame 10).

### BVI — essentially identical
BVI differences are negligible (~0.02–0.04 std on a 0–10 scale) because BVI depends only
on mean, which is closely matched.

### Contrast (K = σ/μ) — mostly agree, SDK has occasional extreme spikes
Median contrast values are similar between pipelines, but the SDK emits occasional frames
with very large contrast (max observed: 281–1759 vs. legacy max of 4–12). This happens
when `corrected_mean` is very small but positive (near a dark transition), making K = σ/μ
blow up. The legacy pipeline avoids this because vectorised numpy operations set contrast
to 0 wherever mean ≤ 0, and the dark-transition frames happen to fall where the quadratic
infill smooths them out.

### BFI — same trend, SDK has larger negative excursions
Both pipelines produce mostly negative BFI for the cameras tested (corrected contrast
exceeds `C_max`, so BFI < 0). The trend and shape match well. However, the SDK's extreme
contrast spikes translate directly into extreme negative BFI values (observed minimum:
−39,084 for Left Cam 3 vs. legacy minimum of −194). These are the same outlier frames
responsible for the contrast spikes.

### Root cause of divergence
The SDK pipeline has no upper bound on contrast when `corrected_mean` is very small but
positive. In these frames the dark subtraction drives the mean close to zero while the
standard deviation remains non-trivial, producing K >> C_max and BFI << 0. The legacy
pipeline avoids this because it works in large vectorised batches and the problematic
frames are smoothed by quadratic infill at dark boundaries.

### Why corrected BFI is sometimes negative during normal operation
Corrected contrast K̃ = σ̃/μ̃ can exceed `C_max` because dark subtraction reduces μ̃
proportionally more than σ̃ (the dark baseline has low variance but non-trivial mean).
This is expected behaviour with the current uncalibrated constants — it does not indicate
a bug. The real-time display shows positive BFI because it uses uncorrected contrast K
(which is lower than K̃ for the inner cameras). No clamping should be added until
`C_min`/`C_max` are properly calibrated.

---

## Scripts Written

### `data-processing/plot_corrected_scan.py`
Plots data from a `_corrected.csv` file produced by the SDK pipeline.

- **Inputs:** `--csv path/to/_corrected.csv`
- **Options:** `--save` (save PNGs next to CSV), `--show-signal` (add second figure with
  mean / std / contrast in addition to BFI/BVI)
- **Layout:** Uses the physical camera grid from `docs/CameraArrangement.md`. Inactive
  cameras are hidden (not shown as "No data"). Both left and right sensors appear in one
  figure. BFI/BVI y-axis auto-scales to actual data range. Temperature plotted on a
  secondary y-axis.

```bash
python data-processing/plot_corrected_scan.py --csv path/to/scan_corrected.csv --save
```

### `data-processing/compare_pipelines.py`
Runs both the legacy `VisualizeBloodflow` and the SDK `SciencePipeline` on the same raw
histogram CSVs and compares their outputs per camera.

- **Inputs:** `--left`, `--right` (raw histogram CSVs; defaults to the perf-test fixtures)
- **Options:** `--save` (save comparison PNGs to disk)
- **Outputs:** Console summary of mean / std / min / max for BFI, BVI, contrast, and mean
  per camera; six PNG plots (BFI, BVI, contrast, mean, ΔBFI, ΔContrast)

```bash
python data-processing/compare_pipelines.py --save
# or with custom files:
python data-processing/compare_pipelines.py --left left.csv --right right.csv --save
```

---

## Tests Written

### `tests/test_corrected_csv_output.py`
Verifies the content and structure of the `_corrected.csv` file produced by the SDK
pipeline, using the real perf-test fixture CSVs as input.

**Key checks (20 tests):**
- Header contains all 98 expected columns (`frame_id`, `timestamp_s`, and 96 metric
  columns: `bfi_l1..8`, `bfi_r1..8`, `bvi_l1..8`, `bvi_r1..8`, `mean_l1..8`,
  `mean_r1..8`, `std_l1..8`, `std_r1..8`, `contrast_l1..8`, `contrast_r1..8`,
  `temp_l1..8`, `temp_r1..8`)
- All 16 temperature columns present and plausible (0–80 °C, not all identical)
- `frame_id` is strictly increasing
- `timestamp_s` starts near zero
- No blank cells in the output
- Corrected means are ≥ 95 % positive and none below −1.0

```bash
pytest tests/test_corrected_csv_output.py -v
```

All 20 tests pass on the perf-test fixtures.

---

## Reference Data

| File | Description |
|---|---|
| `tests/fixtures/scan_owC18EHALL_20251217_160949_left_maskFF.csv` | Real scan, left sensor, all 8 cameras, used for performance and pipeline tests |
| `tests/fixtures/scan_owC18EHALL_20251217_160949_right_maskFF.csv` | Real scan, right sensor, all 8 cameras |
| `tests/fixtures/corrected_output_check.csv` | Corrected CSV output generated by the pipeline test for inspection |
| `tests/fixtures/scan_owC18EHALL_20251217_160949_compare_*.png` | Comparison plots from `compare_pipelines.py` |
