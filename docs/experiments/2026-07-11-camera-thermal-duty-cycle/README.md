# Camera thermal duty-cycle characterization — 2026-07-11

Two scan/off duty-cycle sweeps on the bench rig (console + both sensor modules,
ambient-lit, laser optically decoupled from the cameras), driven by
[`scripts/camera_scan_off_cycle.py`](../../../scripts/camera_scan_off_cycle.py)
and analyzed with [`scripts/thermal_sweep/`](../../../scripts/thermal_sweep/).

| | Run 1 (overnight) | Run 2 (daytime) |
|---|---|---|
| Time | 00:02–07:44 | 11:29–16:51 |
| Mask | 0x0F (4 cams/side) | 0xFF (8 cams/side) |
| Scans | 10 × 20 min, all succeeded | 10 × 20 min, all succeeded |
| Cameras-off waits (min) | 5,15,25,35,45,55,10,30,40 | 5,10,15,20,25,30,2,4,8 |
| Data | 3.84 M frames, 10.7 GB raw | 7.14 M frames, 20.6 GB raw |
| Report | `thermal_report.html` | `thermal_report_ff.html` |

The HTML reports are self-contained (figures embedded); hosted copies:
[run 1](https://claude.ai/code/artifact/98bd6913-1ae6-4592-a79a-27de8a188dce) ·
[run 2](https://claude.ai/code/artifact/963378a8-06a7-4bb8-9caf-c43b30594305).
Fit tables are under `run1_fits/` and `run2_fits/`. Raw + per-frame data live on
the bench PC at `scan_off_cycle_data/` and `scan_off_cycle_data_ff/` (repo root,
untracked).

## Headline results

- **Warm-up is two-pole**: fast die pole 11–31 s + slow module pole 3–8 min;
  two-exponential fits reach 0.2–0.4 °C RMSE. 20-min plateaus: 66–98 °C at
  4-cam load, **69–116 °C at 8-cam load** (+15–20 °C); hot spot mid-array on
  both modules.
- **Hard thermal cutoff at ~115 °C (run 2)**: left cams 3/5 stopped streaming
  in *every* full-load scan at 116.3 / 115.1 °C (±0.1), cam 4 at 114.4 °C on
  hot starts; clean silent truncation, fully revived by the next power cycle
  (23/23). Right module peaked at 113 °C — <3 °C of margin. Per-event data:
  `run2_fits/thermal_dropouts.csv`.
- **Cool-down**: T0(W) = floor + (T_end − floor)·e^(−W/τ), τ ≈ 1.3–3.5 min
  depending on module/load; **15 min off = full thermal reset** (even from
  116 °C); off-state floors are a module property (all cameras converge to the
  same floor; the 30 °C running spread vanishes).
- **Image statistics vs temperature** (run 1 = radiometric reference; run 2 is
  scene-confounded by daytime ambient): mean drifts linearly, gain-like,
  0.004–0.07 %/°C; std rises with T and dominates dark channels (~60 % over a
  scan on the dimmest camera) — contrast-derived quantities inherit the thermal
  time constants. Near-saturated channels (mean ≳ 950/1023) show clipping
  artifacts including negative apparent std slope.
- **Telemetry defects found**: firmware caches camera temperatures across power
  cycles (stale first 4–40 frames every scan; one module-wide freeze lasting
  2.7 h — TIM5 ~11.9 h wrap, fix on `fix/temp-telemetry-staleness`); per-camera
  stream death is invisible to SDK diagnostics (watchdog task filed).
- **Full-load-only histogram stalls**: module-wide 1–14-frame drops in the
  ~0.4 s after dark frames, ~25 % of dark boundaries in run 2's first five
  scans (≤0.24 %), zero at 4-cam load — reproduction recipe for the
  histo-stall debugging (PR #117 flag).
