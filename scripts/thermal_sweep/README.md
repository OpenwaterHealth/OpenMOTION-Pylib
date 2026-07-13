# Thermal sweep analysis pipeline

Analysis tooling for the camera on/off duty-cycle experiments driven by
[`scripts/camera_scan_off_cycle.py`](../camera_scan_off_cycle.py). Turns the
experiment's full raw histogram CSVs into per-frame statistics, thermal model
fits, discontinuity audits, figures, and a self-contained HTML report.

Used for the two 2026-07-11 characterization runs (results and reports in
[`docs/experiments/2026-07-11-camera-thermal-duty-cycle/`](../../docs/experiments/2026-07-11-camera-thermal-duty-cycle/)).

## Workflow

```powershell
# 1. Run the experiment (see the driver's docstring for options)
python scripts/camera_scan_off_cycle.py --mask 0xFF --off-schedule 5,10,15,20,25,30,2,4,8 --data-dir <data_dir>

# 2. Reduce raw CSVs (1033 cols) to per-frame stats (mean/std/temp per frame).
#    Incremental + safe to run while the experiment is live (skips files
#    modified in the last 120 s).
python scripts/thermal_sweep/reduce_raw.py <data_dir>

# 3. Fit + audit + figures  ->  <data_dir>/analysis/
python scripts/thermal_sweep/analyze_sweep.py ff <data_dir>     # 8 cams/side preset
python scripts/thermal_sweep/analyze_sweep.py run1 <data_dir>   # 4 cams/side preset

# 4. Optional: rebuild the HTML report from the figures
python scripts/thermal_sweep/build_report_ff.py <data_dir>/analysis/report_assets
```

The `run1`/`ff` presets encode subject name (`SWEEP8H`/`FFSWEEP`), mask suffix
(`0F`/`FF`) and camera count (4/8 per side) used by the 2026-07-11 runs; edit
the config block at the top of `analyze_sweep.py` for a new experiment naming
scheme. `fitting.py` is a dependency-free exponential fitter (1-D/2-D log-grid
search + linear least squares + golden-section refine) — no scipy required.

## What analyze_sweep.py produces

| Output | Content |
|---|---|
| `fits_warmup.csv` | Per scan/side/cam: T_start/T_end, single- and two-exponential warm-up fits, mean/std drift fits, flicker RMS, stale-frame counts |
| `fits_cooling.csv`, `cooling_points.csv` | Per side/cam cool-down fit T0(W) = floor + (T_end − floor)·e^(−W/τ) and its input points |
| `models_mean_std.csv` | Pooled linear mean(T), std(T) models (light frames, 2-s median smoothed) |
| `discontinuities.csv` | Per scan/side/cam: mid-scan gaps, stale-start window, temp steps, dupes, teardown gap |
| `cycle_timing.csv` | Log-parsed off durations and power-on→scan lead times |
| `report_assets/*.png` | Warm-up families, cooling curve, mean/std vs T, τ comparison, example time series |

## Hard-won data-handling rules (violate these and the analysis lies)

- **Order by `timestamp_s`, never `frame_id`** — the wire frame counter wraps
  at 256 (6.4 s @ 40 fps); frame_id sorting scrambles time and fabricates jumps.
- **Skip each camera's stale-start window**: the first 4–40 frames repeat the
  *previous* power-on session's cached temperature (window scales with camera
  count; refresh ≈ every 4–5 frames, staggered). The code measures the leading
  constant run per camera and skips it.
- **Check for frozen temperature** (range < 0.5 °C over a scan): firmware temp
  polling can stall entirely (TIM5 ~11.9 h wrap, fix pending) and serve stale
  values indefinitely.
- **Check for stream truncation** (`t_max` well short of the duration gate):
  cameras hitting their ~115 °C thermal cutoff stop streaming silently.
- The single 151 ms gap at t ≈ 1200.2 s is the duration-gate/teardown boundary
  and is benign; a 2-s rolling median removes ambient-light flicker before any
  mean/std regression.
