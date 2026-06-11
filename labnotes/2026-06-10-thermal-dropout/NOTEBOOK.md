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
   - `idle_fan_on` — what a real app does between scans: cameras stay powered
     (scan stop does NOT cut the rails), not streaming, fan on;
   - `cams_off_fan_on` — aggressive powered cooldown: camera PCBA rails off
     (`disable_camera_power(0xFF)`), fan ON; IMU cooling curve logged;
   - `cams_off_fan_off` — fan's contribution isolated (low priority).
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
  all 8 cameras both sides, **sensor fans ON** — Ethan (~23:55): assume the fan
  runs all the time in operation, test accordingly. If a fans-on heat phase
  trips nothing within the 25-min cap, that result is recorded (lower bound on
  production time-to-dropout) and the *next* heat phase forces fans OFF once,
  purely to generate a dropout to test recovery on. No abort limits
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

### 2026-06-11 02:31 — campaign launch + first findings

- 02:31:28 campaign start; clean-slate 30 s mains cycle OK (Shelly confirmed
  switching). Console (COM12) and **right** sensor (`357B395A3333`,
  FW 1.6.1-dev.1) reconnect in <2 s after power-on.
- **LEFT SENSOR MISSING**: never enumerated on USB after power-on (checked at
  OS level: only one PID 0x5A5A composite device present; YKUSH ports all ON,
  so not a hub issue). Unknown whether it was present before the experiment —
  I did not verify USB presence at 22:30 (lesson logged). Campaign proceeds
  right-side-only; left/right revision comparison at risk. Will watch
  whether left re-appears on subsequent mains cycles.
- Cycle 0 rc=2 was partly my bug: `_connect`'s rebuild churn could return a
  freshly-reset interface state at the deadline ("console never connected"
  was false). Fixed: rebuild at most once, never trust a negative from an
  interface <15 s old, and proceed with a partial sensor set after 90 s.
  Campaign-side: `bringup_failed` now gets a 60 s mains recovery cycle
  instead of being treated as "no dropouts".
- Side-effect of cycle 0's failure: campaign set force_fans_off, so cycle 1's
  heat phase runs **fans OFF** (off-protocol for a baseline but guarantees an
  early dropout to seed recovery trials; fans-on baselines come later).

### 2026-06-11 02:40–02:52 — two more rig bugs found & fixed live

- Cycle 2 (first run): scan worker crashed at start — **SDK bug**:
  `ScanWorkflow.start_scan` passes both permanent sensor handles to
  `LiveUsbSource`; a never-connected handle has `uart=None` → AttributeError
  in `start_streaming`. Fixed on this branch (mask-0/disconnected sides now
  pass None, which the source supports). Commit 0662236.
- IMU temps read 0.0 — `imu_init()`+`imu_on()` required before
  `imu_get_temperature()`. Fixed (commit 6588c09). Campaign relaunched 02:49.

### 2026-06-11 03:17 — RESULT: fans-on baseline (cycle 0, restarted campaign)

- Right module (FW 1.6.1-dev.1, HWID 24004600035133333639353500000000),
  8-camera scan, laser on, **fan ON**, ambient-night bench: **25 min,
  ZERO dropouts**. 59,868 frames/camera ≈ perfect 40 Hz throughout.
- Die-temp plateau (°C) cam0..7: 55, 70, 90, **102 (cam3)**, 95, 93, 92, 84.
  Hottest is cam3, not 6/7 — position/airflow dependent. Cameras run happily
  at ~100 °C die temp with the fan on.
- Mains draw during scan ≈ 27.5 W (barely above 27 W idle — scan+laser adds
  little; the laser duty cycle is low).
- **Campaign-logic bug found**: the absent left module was counted as
  "dropped", which would have suppressed the fans-off fallback and fabricated
  failed-recovery trials. Fixed (connected-sides-only filter); campaign
  restarted with `control.json {"heat_fans": "off"}` to generate dropouts
  efficiently. Fan-on validation cycles will be re-run near morning if a
  boundary emerges.
- Hypothesis for Ethan: the left module's absence may not be coincidence —
  if left is the dropout-prone revision, it may have cooked itself into
  non-enumeration during earlier bench use. Needs physical inspection.

### 2026-06-11 03:21–03:24 — SDK finding: imu_on wedges sensor comms

- Adding `imu_init()+imu_on()` to bring-up **stalled the right sensor's
  command interface twice** (cycles 0–1 of run 3, identical signature):
  imu_on succeeds → imu_get_temperature times out → every later command
  write times out (`RIGHT-COMM: write timed out after 6 attempts`,
  `usb.core.USBTimeoutError`) → camera power-on fails → whole bring-up dead.
  Teardown shows `RIGHT-IMU: Streaming stopped — 0 USB read chunk(s)`.
  Working hypothesis: imu_on starts IMU streaming on IF2 with no consumer →
  USB backpressure wedges the firmware's command endpoint. Worth a proper
  SDK/firmware investigation (filed as morning-report follow-up).
- Mitigation: imu_init only; accept IMU temp possibly invalid. Die temps
  (per-frame, on-chip) remain the primary thermal observable.
- **DATA HYGIENE**: run-3 trials for cycles 0 and 1 (mains_off 30 s, all-8
  "dropped"/"not recovered") are ARTIFACTS of the comm wedge, NOT thermal
  results. Exclude from recovery analysis; the in-campaign bisection sees
  them, so steer via control.json if it skews the schedule post-ladder.

### 2026-06-11 03:26–03:46 — first genuine dropouts + first recovery trial

**Cycle 2** (fans OFF, all 8 fresh after power cycle): 6/8 cameras dropped
between t=110–224 s of scan. Die temps at drop, °C: cam1 ~115 (early), cam5
115.2, cam6 116.1, cam2 116.0, cam4 114.2, cam7 116.1. **Trip line ≈ 115 °C
die temp, strikingly consistent.** Survivors: cam0, cam3 (stayed cooler).
Firmware's own detection prints `Camera N has stopped posting data` (1-based
numbering!) ~3.5 s before the host watchdog each time, and dumps wedged
peripheral states post-scan (`SPI/USART HAL_*_STATE_BUSY_RX`).

**Cycle 3** (recovery test after **cams_off_fan_on 60 s**): **0/6 recovered**
— all six tripped cameras report `get_camera_status = 0` (not even
peripheral-READY). So the trip persists ≥60 s of rails-off+fan cooling, and
**a tripped camera is detectable by a cheap status poll** — no scan needed.
Meanwhile fresh cams 0 and 3 configured, streamed ~10 min, and dropped at
116.1/115.1 °C (t=615/580 s) → 115 °C line holds for 8/8 dropout events.
IMU temp now valid (init-only fix): 36.7 °C at bring-up (cameras still
tripped!), 51.8 °C after the scan — IMU/board temp runs FAR below die temp;
its value as a recovery gate is questionable, cooling curves will tell.

Next: mains_off 1800 s (until ~04:16) → cycle 4 = long-cool recovery anchor.

### 2026-06-11 04:16–05:05 — THE MODE, NOT THE TIME, CLEARS THE TRIP

Genuine trial matrix so far (right module only; "recovered n/m" = previously
tripped cameras that produce data after bring-up):

| cycle | cooling before | recovered |
|---|---|---|
| 4 | mains_off 1800 s | **8/8** |
| 5 | idle_fan_on 600 s | **0/6** (board cooled 50→31 °C, trip persisted!) |
| 6 | cams_off_fan_on 300 s | **0/8** |
| 7 | mains_off 300 s | **8/8** |

- **mains_off 300 s recovers everything; rails-off 300 s recovers nothing.**
  Same duration, opposite outcome → the recovery variable is FULL module
  power removal, not elapsed time, not module temperature (IMU/board temp
  reached ~31 °C during the failed idle cool — colder than after the
  successful 300 s mains-off).
- Implication: `disable_camera_power` (OW_CAMERA_POWER_OFF) does NOT
  de-energize whatever latches — likely a shared upstream regulator with
  per-camera load switches downstream, or a latched fault state in the
  regulator that only input-power removal resets.
- Every dropout event so far (12/12) occurred at **114–116 °C die temp**;
  cams 0/3 repeatedly survive longest (coolest positions), 1/2/4/5/6/7 trip
  within ~2 min fans-off from warm, ~2–4 min from cold.
- Cumulative-cooling confound noted: failed-recovery cycles chain without
  re-heating. Mitigation in analysis: count only mains-off seconds (idle and
  rails-off demonstrably contribute nothing).
- 05:06 steering: control.json queue = mains 120 → mains 60 →
  cams_off_fan_on 1800 (does rails-off EVER clear it?) → mains 30 →
  mains 300 (repeat). Boundary to bisect: 30 s < T_mains ≤ 300 s.

### 2026-06-11 05:21–06:07 — boundary collapse + the definitive rails-off probe

| cycle | cooling before | recovered |
|---|---|---|
| 8 | idle_fan_on 300 s | 0/6 (cams 0/3 streamed, re-tripped ~115 °C) |
| 9 | mains_off 120 s | **8/8** |
| 10 | mains_off 60 s | **8/8** |
| 11 | **cams_off_fan_on 1800 s** | **0/8** |

- **mains-off works down to 60 s (so far). Rails-off NEVER works — not even
  30 minutes with the fan on, module stone cold.** `disable_camera_power`
  (OW_CAMERA_POWER_OFF) demonstrably does not de-energize the latched
  domain; the trip survives indefinitely while the module stays powered.
- Operational consequence: there is no firmware-only recovery path today.
  Recovery requires cutting module input power (Shelly tonight; whatever
  feeds the sensors in the product). Conversely the required outage is
  short: ≤60 s (floor hunt at 30/15/10 s in progress).
- Module heavily heat-soaked by the trial chain; recovered cameras re-trip
  in ~2–5 min fans-off, still at the same ~115 °C die line.
- 06:08 queue: mains 30 → 15 → 10 → 300 (repeat) → 60 (repeat).

### 2026-06-11 06:08–07:19 — floor hunt + production fans-on soak

| cycle | cooling before | recovered | note |
|---|---|---|---|
| 12 | mains_off 30 s | **8/8** | clean repeat of the artifact-tainted point |
| 13 | mains_off 15 s | **6/6** | |
| 14 | mains_off 10 s | **8/8** | module hot throughout — recovery is NOT thermal |
| 15 | mains_off 300 s | **8/8** | repeat ✓; then fans-ON 45-min soak |

- **Recovery floor < 10 s.** A 10-second mains cut on a hot, heat-soaked
  module recovers all 8 cameras. Combined with rails-off (30 min, cold)
  recovering nothing: the latch is **electrical, not thermal** — clearing it
  requires removing module input power, duration irrelevant in practice.
- **The "how long to wait" question is answered: ~0 — but only via a full
  module power cycle.** No wait duration helps without one.
- **Cycle 15 fans-ON soak**: 45 min at 40 Hz from a heat-soaked start,
  **zero dropouts**; die steady-state 56–105 °C (cam3 hottest), ≈10 °C below
  the 115 °C line. At tonight's lab ambient, fan-on operation does not trip.
  Caveat for report: warmer clinical ambient / blocked airflow shrinks that
  margin; cam-to-cam spread is ~50 °C so margin is position-dependent.
- 07:19: control back to fans-off heating; remaining trials = mains 60
  repeat + leftover plan (cams_off_fan_off 300 fills the fan-off row,
  mains 900, cams_off_fan_on 120, mains 120). Report drafting begins.

### 2026-06-11 07:23–09:40 — validation trials and campaign close

- Repeats all consistent: mains 60 ✓ (2nd), mains 900 ✓, mains 120 ✓ (2nd);
  rails-off fan-on 120 ✗, rails-off fan-off 300/600/1200 ✗✗✗ (adaptive kept
  doubling the only unresolved mode — by design, and every point red).
- Final tallies: **24 cycles, 93 dropout events (115.2 ± 0.93 °C), 11/11
  mains-off recoveries, 0/9 powered-mode recoveries** (artifact trials
  excluded; see analyze.py output).
- Rig bug noted for posterity (not fixed live): the campaign writes its
  stale in-memory control.json back when consuming queue entries —
  lost-update against concurrent hand edits. Worth a re-read-before-write
  if this script gets reused.
- 09:36 campaign self-stopped (24 cycles), restore ran. 09:37: manual 30 s
  mains cycle to clear the tripped cameras, fans set ON, verified all 8
  cameras READY (status 0x1). **Bench left healthy.**
- Left module: final USB probe at 09:38 — still absent. Physical inspection
  needed.
