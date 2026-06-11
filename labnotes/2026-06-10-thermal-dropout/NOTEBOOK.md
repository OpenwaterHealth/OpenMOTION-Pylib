# Lab notebook — camera thermal-dropout recovery characterization

**Researcher:** Claude (autonomous overnight run), on behalf of Ethan
**Date started:** 2026-06-10, ~22:30. Experiment start scheduled ≈ 02:30 (T+4 h per Ethan).
**Bench:** Open-Motion console + 2 sensor modules (different hardware revisions), all
mains-powered through one Shelly Plug US Gen4 (`192.168.1.81`, `$SHELLY_IP_ADDRESS`).
Host PC is NOT on the switched outlet.

## Research question

In 8-camera mode, cameras (most often 6 and 7) drop out under thermal load — the
camera chip's power regulator gives out. Deliverables (refined by Ethan ~23:40):

1. **How long must one wait between 8-camera scans** before dropped cameras are
   usable again — characterize recovery vs. cool-down duration, per sensor
   module (revisions may differ), under several cooling modes:
   - `mains_off` — whole system off via Shelly (passive cooling, fans dead too);
   - `cams_off_fan_on` — system stays powered, camera PCBA rails off
     (`disable_camera_power(0xFF)`), sensor fan forced ON — the practical
     between-scan candidate, with the IMU-temperature cooling curve logged;
   - `cams_off_fan_off` — same but fan off, isolating the fan's contribution.
2. **A detection/prediction rule** for dropout. Caveat from Ethan: the per-frame
   camera temperature comes from the **camera sensor die**, not a PCBA sensor
   near the regulator — it may or may not correlate with dropout probability.
   Treat it as a hypothesis to test: is temp-at-dropout consistent across
   events/cameras/modules? Does IMU temp (readable even with cameras dead, so
   usable as an app-side gate) predict recovery/dropout?

## Agreed protocol (interview with Ethan, 2026-06-10 evening)

- **Power off** = Shelly outlet off → console + both sensor modules fully
  de-energized (confirmed by Ethan). Driver: `openmotion-bloodflow-app/tests/shelly.py`.
- **Dropout symptom** = histogram frames from that camera simply stop while other
  cameras keep streaming (confirmed by Ethan). Detection: per-camera watchdog on
  the pipeline's `raw` channel (pre-TimestampRepair, so no synthetic rows);
  silence > 3 s during an active scan = dropped.
- **Usable again** = the camera actually outputs data (Ethan's definition).
  Operationally: after power-on bring-up, camera configures AND emits histogram
  frames within 120 s of trigger start. Survival time until re-drop is logged too.
- **Heat phase** = normal scan with laser on (Ethan: easier, safe unattended),
  all 8 cameras both sides, **sensor fans OFF** to accelerate. No abort limits
  (Ethan: nothing can be permanently damaged; regulator trip is recoverable).
- **Firmware debug printf ON** (`DEBUG_FLAG_USB_PRINTF`) on both sensors every
  power-on — firmware has its own dropout-detection logic that prints (Ethan).
  Captured into each cycle's `cycle.log` as `[... PRINTF] ...` lines.
- Each recovery test doubles as the next trial's heat phase. One trial =
  heat-to-dropout (+180 s soak) → cool via (mode, T) → bring up → observe.
- Trials interleave the three cooling modes (default plan in the script's
  `DEFAULTS["trial_plan"]`), then adaptive bisection per mode on the
  recover/not-recover boundary. Steerable overnight via `data/control.json`
  (`next_trials`, `heat_fans`, `max_heat_s`, `stop`). Report due ≈ 10:00.
- If time permits near morning: one heat trial with fans ON
  (`heat_fans: "on"`) for a production-realistic time-to-dropout reference.

## Key implementation facts (for reproducibility)

- Orchestrator: `scripts/thermal_dropout_study.py` (this branch).
  `campaign` mode owns the Shelly + schedule; each power-on session is a
  `cycle` subprocess (fresh USB stack every cycle). Verdicts: `data/cycle_NNN/verdict.json`.
- Cold-start sequence each cycle: connect (retry ≤180 s) → identify (FW/HWID) →
  debug printf on → fans off → `apply_laser_power()` (laser registers clear on
  every power cycle!) → **per-camera** configure with error isolation
  (stock `start_configure_camera_sensors` aborts a side at the first dead
  camera — would corrupt verdicts for later cameras, e.g. 7 behind 6) → scan.
- Observables logged per cycle: per-camera frame counts + on-chip temps
  (`temperature_c` from FrameBatch), IMU temp per module at start/end (module
  temperature proxy — at start it reflects the cooled state after off-time T),
  console telemetry, Shelly mains power draw (W), firmware PRINTF lines.
- Camera not-READY / FPGA-program-fail at configure counts as *not recovered*
  (a dead regulator can fail at any bring-up stage, not just streaming).

## Risks / open issues noted before start

- Bring-up itself re-heats: connect + configure ≈ 2–4 min of power-on time
  before the recovery verdict. Unavoidable (can't probe without power); it is
  part of the "usable again" definition and consistent across trials.
- Hardware revisions differ between left/right modules → analyze per side; same
  T sweep applies to both simultaneously (one outlet).
- Cycle 0 = baseline: time-to-dropout from a clean 30 s power cycle, fans off.
  System idled ON (~27 W) for hours before start, so ambient-warm chassis.
- If a whole sensor module fails to enumerate after power-on, that is itself a
  data point (module-level non-recovery) — logged, campaign continues.

---

## Session log

*(entries appended as the experiment runs; data under `data/`)*

### 2026-06-10 ~22:30 — setup

- Interviewed Ethan; protocol above agreed.
- Confirmed Shelly reachable, Gen4 plug, relay ON, system idle at ~27 W.
- Wrote orchestrator; verified SDK call signatures against source
  (`MotionSensor`, `ScanWorkflow`, pipeline factory/runner/batch).
- Found & designed around: stock configure aborts side on first dead camera;
  pipeline nan-fills missing frames after TimestampRepair (watchdog uses `raw`
  channel); laser power registers clear on every power cycle.

### 2026-06-10 ~23:40 — scope refinement from Ethan

- Goal restated: the wait-between-scans rule (or a detection rule), not just
  mains-off recovery. Added powered cooling modes (`cool` subcommand:
  camera rails off via `disable_camera_power`, fan on/off, IMU cooling curve
  at 0.1 Hz) and the interleaved three-mode trial plan.
- Noted: per-frame camera temp = sensor **die** temp, not regulator/PCBA temp.
  Analysis must test, not assume, its correlation with dropout.
