# Science Pipeline CSV Tests

Hardware-free tests for the science data pipeline (`SciencePipeline` in `omotion/MotionProcessing.py`). All tests are driven by pre-generated histogram CSV fixtures — no physical device is required.

## Running the tests

```bash
# Correctness tests (synthetic fixtures, no hardware required)
pytest tests/test_pipeline_csv.py -v
python tests/test_pipeline_csv.py          # standalone, no pytest needed
python scripts/run_pipeline_csv_tests.py   # auto-generates fixtures if missing

# Performance test (real captured scan data)
pytest tests/test_pipeline_perf.py -v -s  # -s shows the stats report
python tests/test_pipeline_perf.py         # standalone with full report
```

## How it works

`feed_pipeline_from_csv(csv_path, side, pipeline)` (in `omotion/MotionProcessing.py`) reads a raw histogram CSV and enqueues every row into a running `SciencePipeline`. This lets any captured or synthetic histogram data be replayed through the pipeline without hardware.

Each test creates a `SciencePipeline` wired to two collector lists (uncorrected samples, corrected batches), feeds one or more fixture CSVs into it, calls `pipeline.stop()` to drain the queue, then asserts against what was collected.

## Fixture files

All fixtures live in `tests/fixtures/`. They can be regenerated at any time:

```bash
python tests/fixtures/generate_fixtures.py
```

Fixtures use the same column format as live scans (`cam_id`, `frame_id`, `timestamp_s`, bins `0`–`1023`, `temperature`, `sum`) and sum to `EXPECTED_HISTOGRAM_SUM` (2,457,606) so the pipeline's photon-count validator passes without being disabled.

**Histogram shapes used:**
- **Regular frames** — uniform distribution over bins 400–499 (mean ≈ 449.5)
- **Dark frames** — uniform distribution over bins 10–19 (mean ≈ 14.5)

**Pipeline parameters used in all tests (unless noted):**
- `discard_count = 2` — frames 1–2 are warmup and must be discarded
- `dark_interval = 5` — dark frames at absolute positions 3, 6, 11, 16, 21, …

| File | Cameras | Frames | Dark interval | Purpose |
|---|---|---|---|---|
| `single_cam_basic_left.csv` | left cam 0 | 12 | 5 | Basic dark detection and correction |
| `multi_cam_left.csv` | left cams 0, 1 | 12 | 5 | Multi-camera frame assembly |
| `both_sides_left.csv` | left cam 0 | 12 | 5 | Cross-side alignment (paired with right) |
| `both_sides_right.csv` | right cam 0 | 12 | 5 | Cross-side alignment (paired with left) |
| `frame_id_rollover_left.csv` | left cam 0 | 275 | 10 | Raw u8 frame ID wraps 255→0 at frame 256 |
| `multi_interval_left.csv` | left cam 0 | 25 | 5 | Four complete dark intervals |

---

## Performance test — `test_pipeline_perf.py`

Feeds two real captured scan CSVs (left and right, all 8 cameras each) through the pipeline and measures end-to-end throughput, latency, and output quality.  The CSVs are not committed to the repository — they must be present in `tests/fixtures/` for this test to run; it is skipped automatically if the files are missing.

**Input files:**
| File | Rows | Cameras | Duration |
|---|---|---|---|
| `scan_owC18EHALL_20251217_160949_left_maskFF.csv` | 5,240 | 8 (mask 0xFF) | ~16.4 s |
| `scan_owC18EHALL_20251217_160949_right_maskFF.csv` | 5,240 | 8 (mask 0xFF) | ~16.4 s |

**Metrics collected:**

| Metric | Description |
|---|---|
| CSV load + enqueue time | Wall time to read both files and put all rows on the pipeline queue |
| Pipeline drain time | Time from last enqueue to `pipeline.stop()` returning |
| Total processing time | Sum of the above |
| Throughput | Total rows processed per second |
| Real-time factor | Original scan duration / total processing time (>1 = faster than real-time) |
| Uncorrected callback count | Total `on_uncorrected_fn` calls received |
| Corrected batches emitted | Total `on_corrected_batch_fn` calls received |
| Callback interval stats | Min / max / mean / median / std / p95 / p99 of gaps between uncorrected callbacks |
| Batch size stats | Min / mean / max samples per corrected batch |

**Pytest assertions:**

| Test | What it checks |
|---|---|
| `test_all_rows_ingested` | Exactly 5,240 rows ingested from each side |
| `test_uncorrected_callbacks_received` | At least one uncorrected callback fired |
| `test_corrected_batches_emitted` | At least one corrected batch emitted for a 16s scan |
| `test_realtime_factor` | Processing completes at least 2× faster than real-time |
| `test_callback_interval_p99` | p99 gap between uncorrected callbacks is under 500 ms |
| `test_print_report` | Prints the full stats report (visible with `pytest -s`) |

**Typical results on a development machine:**

```
CSV load + enqueue time:    ~4.2 s
Pipeline drain time:        ~0.3 s
Total processing time:      ~4.6 s
Throughput:                 ~2,300 rows/s
Real-time factor:           ~3.5x
Uncorrected callbacks:      10,320  (655 frames × 8 cams × 2 sides, minus warmup/dark)
Corrected batches:          16      (1 batch per camera after the one dark interval)
Batch size:                 591 samples each
```

---

## Correctness test suites — `test_pipeline_csv.py`

### TestSingleCamBasic
Single left camera, 12 frames. Dark frames land at absolute positions 3, 6, and 11, producing two complete correction intervals (3→6, 6→11).

| Test | What it checks |
|---|---|
| `test_rows_were_fed` | All 12 CSV rows reach the pipeline |
| `test_warmup_frames_discarded` | Frames 1 and 2 never appear in the uncorrected callback |
| `test_dark_frames_not_in_uncorrected_as_new_values` | All uncorrected absolute IDs are above the discard threshold |
| `test_at_least_one_corrected_batch` | At least one `CorrectedBatch` is emitted |
| `test_first_batch_boundaries` | First batch spans dark frames 3→6 |
| `test_first_batch_samples_are_corrected` | All samples in batch 0 have `is_corrected=True` |
| `test_first_batch_sample_frame_range` | All batch 0 sample IDs fall within [3, 6] |
| `test_two_corrected_batches` | Two complete intervals → two batches |
| `test_second_batch_boundaries` | Second batch spans dark frames 6→11 |

### TestDarkCorrectionMath
Same fixture as above, focused on numeric correctness of the dark-frame correction arithmetic.

After subtracting the dark baseline (mean ≈ 14.5) from the regular signal (mean ≈ 449.5), the corrected mean should be close to 435.

| Test | What it checks |
|---|---|
| `test_corrected_mean_positive` | Corrected mean > 0 (signal exceeds dark noise) |
| `test_corrected_mean_approx` | Corrected mean is in the range (400, 470) |
| `test_uncorrected_mean_approx` | Uncorrected mean of regular frames is near 449.5, range (440, 460) |
| `test_bfi_bvi_in_range` | BFI and BVI are finite and within (−50, 50) |
| `test_contrast_nonnegative` | Corrected contrast is >= 0 for all corrected samples |

### TestFrameIdRollover
275 frames with `dark_interval=10`. The raw u8 frame ID counter wraps from 255 back to 0 at absolute frame 256. Dark frames at 261 and 271 both fall after the rollover, so a correction batch must be emitted with `dark_frame_end > 255`.

| Test | What it checks |
|---|---|
| `test_rows_fed` | All 275 rows are ingested |
| `test_absolute_ids_monotonic` | Absolute frame IDs in the uncorrected stream never decrease |
| `test_absolute_ids_exceed_255` | At least one uncorrected sample has `absolute_frame_id > 255` |
| `test_corrections_still_fire_after_rollover` | At least one batch has `dark_frame_end > 255` |

### TestMultiCamLeft
Two cameras (cam 0 and cam 1) on the left side, 12 frames. The pipeline must emit corrected batches for both cameras.

Note: the pipeline emits one `CorrectedBatch` per `(side, cam_id)` per dark interval, so a single batch object covers only one camera. The multi-camera check is done across all batches.

| Test | What it checks |
|---|---|
| `test_rows_fed` | 24 rows ingested (12 frames × 2 cameras) |
| `test_both_cameras_in_uncorrected` | Both cam 0 and cam 1 appear in the uncorrected stream |
| `test_at_least_one_batch` | At least one corrected batch is emitted |
| `test_batch_contains_both_cameras` | Across all batches, both camera IDs are represented |

### TestBothSides
Left cam 0 and right cam 0 fed into a single pipeline instance. Both sides should appear in all output streams.

| Test | What it checks |
|---|---|
| `test_rows_fed` | 12 rows each from left and right |
| `test_both_sides_in_uncorrected` | Both `"left"` and `"right"` appear in uncorrected samples |
| `test_at_least_one_batch` | At least one corrected batch is emitted |
| `test_batches_contain_both_sides` | Across all batches, both sides are represented |

### TestMultipleIntervals
25 frames, producing dark frames at 3, 6, 11, 16, and 21. This gives four complete correction intervals and verifies that batches are emitted in the correct order with the correct boundaries.

| Test | What it checks |
|---|---|
| `test_rows_fed` | All 25 rows ingested |
| `test_four_batches` | At least 4 corrected batches emitted |
| `test_batch_boundaries_in_order` | Each batch's `dark_frame_start` >= the previous batch's `dark_frame_end` |
| `test_expected_dark_frame_positions` | First four batch pairs are exactly (3,6), (6,11), (11,16), (16,21) |
