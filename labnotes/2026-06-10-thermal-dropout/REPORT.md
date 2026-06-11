# Camera thermal-dropout characterization — overnight report

**Run:** 2026-06-11, 02:30–09:00, autonomous (Claude). Bench: console +
right sensor module (FW 1.6.1-dev.1, HWID `2400460003513333…`), Shelly
mains switching, 8-camera scans with laser. **Left module was absent from
USB all night** — see Anomalies. All quantitative results below are from
the right module; the left/right revision comparison could not be done.

## TL;DR

1. **Dropout cause/threshold:** every dropout (40+ events) occurred when that
   camera's **die temperature hit 115.2 ± 0.95 °C**. The on-chip temp in the
   frame stream is an excellent *dropout* predictor (contrary to our prior
   skepticism) — per-camera trip points are stable (113.2–116.1 °C).
2. **With the fan ON the module never tripped** — 25 min from cold and 45 min
   from a worst-case heat-soaked start, die temps plateau at 56–105 °C
   (hottest camera ~10 °C below the trip line) at overnight lab ambient.
   All dropouts tonight required running with the fan off.
3. **Recovery is electrical, not thermal.** "How long must we wait?" has a
   sharp answer: **waiting does nothing — a full module power cycle fixes
   everything, and even 10 s off is enough on a hot module.**
   - mains off 10 / 15 / 30 / 60 / 120 / 300 / 300 / 1800 s → **8/8 recover, every time**
   - camera rails off via `disable_camera_power` + fan, 60 s–**30 min** → **0 recover, every time** (even with the board cooled to ~28 °C)
   - idle (cameras powered, fan on) 5–10 min → **0 recover**
   The latch only clears when module *input power* is removed —
   `OW_CAMERA_POWER_OFF` does not de-energize the failing regulator domain.
4. **A tripped camera is detectable in <1 s without scanning:**
   `get_camera_status()` returns status `0` (not even peripheral-READY).
   Firmware also already detects dropouts itself and prints
   `Camera N has stopped posting data` (~3.5 s before host-side detection).

## Operational recommendations

- **Between-scans rule:** none needed for waiting — there is no wait
  duration that recovers a tripped camera while powered. Instead:
  - **Detect:** after every scan (or before starting one), poll
    `get_camera_status(0xFF)`; any camera with status 0 is tripped.
  - **Recover:** cycle the **sensor module's input power** (≥10 s off to be
    safe; we never found a too-short duration). Today that means the
    system's power path, not any firmware command.
- **Prevent:** keep the fan on during 8-camera scans (margin ≈10 °C at
  ~22 °C ambient; re-validate at clinical ambient). A conservative firmware/
  app guard: warn or derate when any die temp exceeds ~110 °C — dropout
  follows within seconds-to-minutes of crossing 115 °C.
- **Hardware follow-up:** identify the regulator/power domain that latches.
  Per-camera load switches (`OW_CAMERA_POWER_OFF`) are downstream of it;
  whatever feeds the camera complex upstream retains the fault while the
  module is powered. A firmware-controllable reset of that domain would
  turn the recovery story from "power-cycle the system" into one command.
- **Firmware opportunity:** the firmware already prints per-camera dropout
  detection; promote it from debug printf to a status flag/event the SDK can
  read, and consider auto-recovery once the upstream domain is switchable.

## Evidence

### Trip line (die temperature at dropout)

40 dropout events across 9 heat phases, fans off:
mean **115.2 °C**, σ **0.95**, range 113.2–116.1. Per camera (n=5 each):
cam0 115.9, cam1 113.2, cam2 115.9, cam3 115.0, cam4 114.5, cam5 115.1,
cam6 116.1, cam7 116.1. Cameras 0 and 3 run coolest at this airflow/position
and survive longest; with the fan off, 6/8 cameras trip within 2–4 min from
warm. See `figs/fig_die_temps.png`.

### Recovery matrix

See `figs/fig_recovery_matrix.png`. Mains-off recovers 8/8 at every duration
tried (10 s–30 min, including on a module so hot it re-trips within ~2 min of
scanning). Rails-off (`disable_camera_power`) and idle recover 0 at every
duration tried (up to 30 min / 10 min), including with the board cooled to
~28–31 °C (IMU temp; `figs/fig_cooling_curves.png`) — bulk temperature is
irrelevant to recovery, which is why "wait between scans" never works
without a power cycle.

### Fan-on production baseline

- Cold start, fan on: 25 min, 0 dropouts, die plateau 55–102 °C.
- Heat-soaked start, fan on: **45 min, 0 dropouts**, die plateau 56–105 °C,
  107,958 frames/camera ≈ lossless 40 Hz.
- Same module, fan off: trips begin t≈110 s (warm start ≈2 min; cold ≈4 min).

## Anomalies & bugs found along the way

1. **Left sensor module absent from USB the entire night** (only PID 0x5A5A
   device is the right module; YKUSH ports all on; survived many power
   cycles). Possibly unrelated bench issue — but if the left unit is the
   dropout-prone revision, it may have failed permanently. **Needs physical
   inspection before any cross-revision conclusion.**
2. **SDK bug (fixed on this branch):** `ScanWorkflow.start_scan` crashed the
   scan worker when a sensor is disconnected (`LiveUsbSource` got a handle
   with `uart=None`). Now passes None for mask-0/disconnected sides.
   Commit `0662236`.
3. **SDK/firmware bug (open):** `imu_init()+imu_on()` then reading
   temperature reliably **wedges the sensor's command interface** until
   power cycle (reproduced 2×; suspected unconsumed IMU stream on IF2
   back-pressuring the USB stack). Spawned follow-up task. Mitigation in
   rig: init-only. Also: IMU temp reads 0.0 after a cold mains boot even
   with init (works when the module was already up) — worth a look.
4. The stock configure workflow aborts a whole side at the first failed
   camera — for diagnostics, per-camera isolation (as the rig does) gives a
   per-camera verdict instead. Maybe worth an SDK option.

## Method (short)

Automated campaign (`scripts/thermal_dropout_study.py`): each cycle =
power-on → identify → debug printf on → `apply_laser_power` → per-camera
FPGA program/configure (error-isolated) → 8-camera scan with per-camera
frame watchdog ("raw" pipeline channel) → heat until dropout+180 s (or cap)
→ cooling phase (mode × duration from an adaptive schedule) → next cycle
judges recovery = camera configures AND emits frames. Cooling modes:
Shelly mains-off; rails-off via `disable_camera_power(0xFF)` fan on/off;
idle fan on. Trial decisions logged in `data/events.jsonl`; per-cycle logs,
1 Hz per-camera samples, verdicts under `data/cycle_*`; full narrative in
`NOTEBOOK.md`. Artifact trials (comm-wedge cycles 0–1) excluded from stats.

## Data index

- `NOTEBOOK.md` — chronological lab notebook (decisions, bugs, findings)
- `data/events.jsonl` — every trial/power/cool event
- `data/cycle_NNN/{cycle.log,samples.csv,verdict.json,status.json}`
- `data/cool_NNN/cooling_curve.csv` — IMU temp + mains W during powered cools
- `figs/` — recovery matrix, die-temp traces, cooling curves
- `analyze.py` — regenerates stats + figures from the data
