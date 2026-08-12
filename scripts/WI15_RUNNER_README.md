# WI-00015 Automated Tuning Runner — deviation log and traceability

Accompanies `scripts/wi15_runner.py` (CLI) and `omotion/tuning.py` (the phase
engine — importable so the test-app's future tune button, tracked as
openmotion-test-app#70, can drive the same phases directly). Automates **WI-00015 Open-Motion Device Specific
Parameter Tuning rev 2 (ECO-000270)** via the `omotion` SDK and the Ophir
Centauri COM interface. This document records every place the implementation
deviates from the WI as written, and why, so a future reader can reconstruct
the reasoning without the original conversation.

Maintainer contact for all rulings below: Ethan (ethan@openwater.cc).
Rulings were given 2026-08-05 in review of the first automated runs.

---

## 1. Acceptance window — production by default, per-run override

The defaults in `omotion/tuning.py` are the **production SPEC-31 values,
300–400 µJ**. A dev rig overrides them per run at the baseline phase:

    python scripts/wi15_runner.py baseline --side right --window 75 125

(or `WI15_WINDOW=75,125` in the environment). The chosen window and its
provenance are pinned into the run state at baseline time and reloaded by
every later phase — a single run can never mix windows — and both appear in
the PDF header.

Background for the 75–125 dev window: the bench unit (console QWW04Q10003)
tops out at ~163 µJ at maximum drive (5000 mA / 500 µs; measured slope
0.289 µJ/µs of pulse width ⇒ ~975 µs to reach 300 µJ, far beyond the 600 µs
ceiling), so per Ethan the window is scaled to 75–125 µJ with **literal
semantics** (>125 µJ triggers the step-19 current-reduction loop) to
exercise the full decision tree. By the production window this unit is an
NCR.

## 2. Authoritative WI interpretation rulings (Ethan, 2026-08-05)

These resolve ambiguities in the WI text itself. Numbering matches the
questions as asked.

| # | Ambiguity in WI | Ruling | Where implemented |
|---|---|---|---|
| 1 | Step 18 routes an under-min module to "skip ahead to section 4.4", but steps 23–26 describe a pulse-width escalation for the same condition | **SUPERSEDED 2026-08-06:** under-min now routes through the **step-23 escalation** (lowest module seated, +10 µs steps to the 600 µs ceiling; ceiling-failure ends the procedure per step 23’s text). Ethan’s formal recommendation, replacing the WI author’s informal skip-ahead answer; step 18’s "skip ahead to 4.4" text is the error (its "place it back" already matches step 23’s setup). Redline item 3 proposes the same | `phase_tune` under-min branch (persisting); `diagnose-low` remains the non-persisting sweep |
| 2 | §4.4 never says which module is seated during the safety-ADC reads | **CORRECTED 2026-08-06:** seating is irrelevant to the ADC — one laser, one safety ADC, both module outputs always fed (the ~5% Left/Right difference measured earlier was drift, not module dependence). Seating for the tune phase is governed by the adjustment branch instead: highest module for step 19, **lowest for step 23**, higher for the in-window case (continuity only) | `tuning_seat_for()`; `phase_tune` seat guard |
| 3 | Step 31 says round the 1.1× EE value "to the nearest integer" in the step text but "round down" in its results column | **SUPERSEDED 2026-08-12 (BH response): round to NEAREST for everything.** Christopher's round-down was deliberate conservatism, but one LSB is noise relative to the system's own variability, so plain numeric rounding for both multipliers. (Original 2026-08-05 ruling: round down for EE.) | `EE_DRIVE_CL = round(...)`, `OPT_DRIVE_CL = round(...)` |
| 4 | One module above max while the other is below min (single shared TA drive) is not covered by the decision tree | **NCR.** Unresolvable by tuning. (BH 2026-08-12 concurs: already covered by steps 22/23/26 — the unit fails WI-00015 if both modules aren't in range by end of testing) | Falls out of the branch logic; cross-check failure marks NCR |
| 5 | Step 18 "The Test is complete" cannot literally end the test (§4.4–4.6 follow) | Reading confirmed: it means §4.2 tuning is complete; the procedure continues | flow always proceeds to §4.4 |
| 6 | Steps 24/26 say to set TA_PULSE_WIDTH "to the new drive current found" | Copy-paste error in the WI; the Figure-Y line references show pulse width is meant | n/a (affects only the diagnostic sweep) |
| 7 | What does a factory-new console EPROM contain, and are keys beyond the WI's nine permitted in the final config? | **Factory-new is empty. Extra keys are permitted.** | `phase_finalize` preserves pre-existing `calibration`/`TEC_TRIP`; on an empty unit this is a no-op |
| 8 | §4.6 step text says `calibration-YYYYMMDD_HHMMSS.csv`, its results table says `test-YYYYMMDD_HHMMSS.csv` | Resolved from code: `CalibrationWorkflow.py` writes `calibration-{ts}.csv` from the calibration flow and `test-{ts}.csv` from the separate verification-test flow. **The step text is right; the results-table header is wrong.** (BH 2026-08-12: "update to what's on newest app version" — re-verified against the extracted 1.4.0-rc.0 app bundle: identical filenames, so the finding stands) | (§4.6 not yet automated) |
| 9 | Power-cycle dwell, trigger frequency, single-vs-averaged ADC reads unspecified | **15 s dwell; assume/enforce 40 Hz; average a few ADC reads** | `POWER_OFF_DWELL_S = 15`, `assert_trigger_40hz()` (sets it if wrong), `ADC_SAMPLES = 10` averaged (raised from 5 per BH 2026-08-12 — "at least 10 or more"; ~400 suggested if register polling ever moves to 40 Hz). Power cycling is automated via a Shelly smart switch when the `SHELLY_IP_ADDRESS` env var is set - the same variable the bloodflow-app HIL test rigs already export (Ethan's bench: `192.168.1.81`); **without it — e.g. at the factory — the flow prompts the operator to flip the power switch**, and in both modes the console's firmware uptime is checked after reconnect (< 3 min) to prove the cycle actually happened — an unproven cycle fails the persistence verification |
| 10 | Steps 30/31 "note the returned value": raw ADC counts or scaled mA? | **Scaled mA** (confirmed). Proven empirically: writing `EE_DRIVE_CL: 4604` yields register raw 2475 = 4603.50 mA — a clean round trip through the 1.86 mA/LSB scale. The TestApp displays `rawValue * scale` for any field carrying unit+scale (`Console.qml`), which ADC DATA does. A literal raw-counts reading would program EE_DRIVE_CL ≈ 2476 mA against a ~4185 mA operating current and trip the interlock on the first pulse. **The WI wording should be redlined to say "the value in mA as displayed."** | multipliers applied to scaled mA |

## 3. Deviations from the WI as written

### Measurement method
- **Statistics are computed from the meter's per-pulse stream**, not read off
  the StarLab display. The WI's "Average / Std. Dev displayed on the meter"
  (3-sec display window) becomes computed mean/σ/rate over the capture. The
  WI's display-only settings ("Average 3 sec", "Graph type Statistics") have
  no COM equivalent and none is needed.
- **Non-zero-status samples are discarded before any statistic.** ~2% of the
  Centauri stream carries status 327680 with sentinel values (~10¹³ µJ),
  occasionally sharing a timestamp with a real pulse. Unfiltered, one run
  produced a "mean" of 975,775 µJ. The WI has no notion of this; the report
  records how many samples were discarded.
- **Longer capture**: ~320 pulses (8 s) per measurement instead of the WI's
  reset-wait-a-second-stop (~40 pulses). Loop steps use 4 s. A 1 s settling
  window after laser-on is discarded, mirroring the WI's "press Reset, then a
  second or so later".
- **Repetition rate** comes from pulse timestamps instead of the meter's
  display readout.

### Sequencing
- **Safety ADC values are sampled while the laser fires at the final tuned
  parameters** (10 reads averaged, higher-power module seated). The registers
  latch their last value after the trigger stops — a post-stop read looks
  plausible and is wrong. Reads with the laser off return 0.
- **Section 4.4 runs before the cross-check (WI order: steps 21–22 then
  §4.4).** The ADC values depend only on the final tuned drive settings,
  which are frozen when the step-19 loop exits, and the cross-check changes
  no settings — so sampling §4.4 while the higher module is still seated is
  measurement-equivalent and saves a fixture insertion (the WI's literal
  order needs the higher module seated a second time: 5 insertions vs 3–4).
- **Cross-check only runs when tuning adjusted something.** WI steps 21–22
  (over-max) and 25–26 (under-min) exist inside the adjustment branches;
  when both baselines land in the window the other module was already
  measured at the final settings and the re-measure buys nothing. The skip
  is recorded in the run notes/PDF. The cross-checked module is always the
  one NOT seated for the adjustment loop.
- **Baseline order is operator's choice**, not the WI's hardcoded
  Left-then-Right (steps 12/15). Symmetric measurements; the guided flow
  tips the operator to seat the expected lower-power module first so the
  higher one is already seated for §4.4.
- **The EPROM is written once, at the end** (`finalize`), rather than the WI's
  incremental save-as-you-go through steps 9→33. The end state is identical;
  the write is verified key-by-key after a power cycle.
- **WI step 9 IS performed, literally (ruling update, Ethan 2026-08-06):**
  at the start of each fresh run the **entire User Configuration is replaced
  with the default starting values — including any calibration block**,
  before the first bring-up. Proven necessary on hardware: a "factory-new"
  unit arrived carrying a stale foreign config (seq 66,
  `TA_PULSE_WIDTH: 600` — the WI ceiling), and without the wipe every
  baseline would have measured at the wrong operating point
  (`apply_laser_power()` honors EPROM overrides by design). The pre-wipe
  config is recorded verbatim in the PDF's Appendix A along with a note
  listing every non-default value found. The later EPROM write (`finalize`)
  preserves extras only from the **current** post-wipe contents (ruling 7),
  never from the pre-run snapshot, and always drops legacy `EE_THRESH`,
  `EE_GAIN`, `OPT_THRESH`, `OPT_GAIN` (`omotion/laser.py` prefers them over
  `EE/OPT_DRIVE_CL`, silently defeating tuned limits).
- **Laser bring-up** uses the SDK's `apply_laser_power()`, which programs
  1000 µs pulse-width upper limits from its bundled `laser_params.json`. The
  WI's standing default is 550 µs, so the runner tightens both limits to
  550 µs immediately after bring-up, before any firing.

### Records
- **No screenshots** (WI steps 8/11 capture TestApp screens). Equivalent data
  is recorded as text in the PDF: console serial/firmware, meter and sensor
  identities and calibration due dates, applied meter settings, and every
  register value used. (Sensor-module serial/firmware capture is still TODO.)
- Each run emits a **PDF record** with the pre-run EPROM contents reproduced
  verbatim in Appendix A (so nothing is lost even though step 9's wipe is
  skipped).

### Safety engineering not present in the WI
- Trigger stop in `finally` on every exit path — no path leaves the laser on.
- Pre-flight check before every firing: TA pulse width must be below both
  safety pulse-width limits (else abort rather than trip the interlock).
- TA current only ever steps **down**, with a hard floor
  (`TA_CURRENT_FLOOR_MA = 2000`) and an iteration cap.
- The `diagnose-low` sweep restores the safety limits **and the TA pulse
  width itself** on every exit path, and persists nothing.

## 3b. Section 4.6 calibration — `scripts/wi15_calibration.py`

Deliberately a **separate flow** from the tuning runner (per Ethan: keep laser
tuning and calibration segmented in software even though they will be tied
together later). Shared bench plumbing (console session, Shelly control,
output dirs) is imported from `omotion.tuning`; state and PDF are its own
(`wi15_cal_state.json`, `WI-00015-46_*_calibration.pdf`).

Runs the same SDK engine as the bloodflow-app's Calibrate button
(`MotionInterface.start_calibration` → `CalibrationWorkflow`), with the app's
parameters: camera mask 0xFF per selected side, 5 s scan / 1 s delay / 600 s
watchdog, canonical trigger config. Deviations / decisions:

- **No bloodflow-app GUI** (WI step 34 opens it in developer mode with a
  password). The engine and the EEPROM write are identical; the app's
  passwords and green indicator are UI dressing over this same workflow.
- **Physical attestations become mandatory flags**: `--phantom-confirmed`
  every run; `--side both` additionally needs `--two-phantoms` (WI step 35's
  warning about improperly recalibrating the other module, enforced).
- **Thresholds default to zeros** on the bench (dev unit is dim), so "passed"
  means "ran end-to-end and wrote EEPROM". For factory use, pass
  `--thresholds-json` with the bloodflow-app's factory-test thresholds so
  PASSED means what the app means. `--allow-dim` wires the SDK's #199
  below-threshold consent gate.
- **Step 36's record** (side/cam/mean/avg_contrast per camera) is captured in
  the console output, the state file, and the PDF, along with the engine's
  own `calibration-{ts}.csv`.
- **Step 37** is the `verify` subcommand: real mains power cycle, then a
  key-by-key comparison of the FULL config (tuning keys + calibration block +
  extras) before/after.
- `apply_laser_power()` runs first, which also applies the tuned EPROM
  overrides — calibration therefore happens at the tuned laser point.

## 3c. Operator-guided single-command flow — `scripts/wi15_guided.py`

Runs the entire WI (tuning 4.1–4.5 then calibration 4.6) with `y`-gated
prompts at every physical step: fixture swaps, phantom placement +
attestation, and mains-power-cycle warnings. Wraps the same phase functions
as the two per-flow CLIs — no separate logic. `--window MIN MAX` passes
through to the baseline phase; `--skip-calibration` runs tuning only;
`--fresh` discards previous run state. A failed cross-check (NCR per WI step
22) stops the run unless the operator explicitly continues (dev bench only).

## 3d. Device inventory (WI step 8)

Captured automatically once per run and rendered in both PDFs: SDK version,
console serial/firmware, per-sensor-module serial/firmware/hardware-id, and
the console laser/safety FPGA versions (TA / Seed / Safety EE / Safety OPT,
read from their I2C revision registers). The tuning flow collects sensor
identities via a short-lived `MotionInterface` session before opening its
console-only session; the calibration flow reuses its own interface.

## 4. Known gaps / not yet implemented

- The over-max branch was validated only against the 125 µJ dev ceiling; the
  under-min skip-ahead branch is unreachable on this unit with the 75 µJ
  floor and remains logic-tested only. Both need a pass on a bright unit
  with the production window.
- The WI names the TestApp/bloodflow-app as the instruments; whether this
  SDK-based runner can be the *official* method needs a WI revision or
  TP-00018 blessing. Proposed WI edits are collected in
  `docs/WI-00015-redline-suggestions.md`.

## 5. Hardware quirks discovered (apply to any future implementation)

- **Ophir status≠0 sentinels** — see above; filter before statistics.
- **EE/OPT ADC latch** — 0 before first fire, last value after stop; only
  read while firing.
- **OPT ADC is module-dependent** (~5% Left vs Right on the dev unit);
  EE ADC is not (electrical, shared TA path). Hence ruling 2.
- **Register quantization**: programmed 140 reads back 141.87 (SEED_CW_GAIN),
  5000 → 5000.80 mA, 500 → 500.16 µs. WI step 28 acknowledges this; no
  tolerance is specified anywhere.
- **StarLab 4.00 COM object ships broken** (typelib version mismatch,
  `TYPE_E_LIBNOTREGISTERED` on every by-name call). Machine-wide fix
  installed on this PC: patched typelib registered as 10.11 from
  `C:\ProgramData\OphirComFix\`. See `OPHIR_SETUP_NOTES.md`.

## 6. Run sequence

```
python wi15_runner.py baseline   --side right     # whichever is seated first
python wi15_runner.py baseline   --side left      # after swapping
python wi15_runner.py tune       --seated <higher-power side>
python wi15_runner.py crosscheck --seated <other side>
python wi15_runner.py finalize                    # EPROM + Shelly power cycle + PDF
python wi15_runner.py reset-state                 # start a fresh run
```

State accumulates in `wi15_run_state.json`; the PDF lands in the same
directory. Both default to `./wi15_out` — set the `WI15_OUT_DIR` environment
variable to change it.

Python dependencies beyond the SDK itself: `pywin32` (Ophir COM) and
`reportlab` (PDF). The Ophir StarLab COM object must be installed and its
typelib fixed per `docs/OphirCentauriSetup.md`.
