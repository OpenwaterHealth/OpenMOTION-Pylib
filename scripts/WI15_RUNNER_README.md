# WI-00015 Automated Tuning Runner — deviation log and traceability

Accompanies `wi15_runner.py`. Automates **WI-00015 Open-Motion Device Specific
Parameter Tuning rev 2 (ECO-000270)** via the `omotion` SDK and the Ophir
Centauri COM interface. This document records every place the implementation
deviates from the WI as written, and why, so a future reader can reconstruct
the reasoning without the original conversation.

Maintainer contact for all rulings below: Ethan (ethan@openwater.cc).
Rulings were given 2026-08-05 in review of the first automated runs.

---

## 1. Temporary acceptance window — NOT production values

`SPEC31_MIN_UJ = 75.0` / `SPEC31_MAX_UJ = 125.0` at the top of the script.

The WI/SPEC-31 production window is **300–400 µJ**. The dev-rig unit (console
QWW04Q10003) tops out at ~163 µJ at maximum drive (5000 mA / 500 µs; measured
slope 0.289 µJ/µs of pulse width, i.e. ~975 µs would be needed to reach
300 µJ — far beyond the 600 µs ceiling). Per Ethan, the window is scaled to
75–125 µJ *for the time being* so the full decision tree can be exercised on
this unit, with **literal semantics: >125 µJ is "too hot" and triggers the
WI step-19 current-reduction loop**. The tuned (reduced) current is what lands
in EPROM on this rig.

**Restore 300/400 before factory use.** The constants are the first thing in
the configuration block.

## 2. Authoritative WI interpretation rulings (Ethan, 2026-08-05)

These resolve ambiguities in the WI text itself. Numbering matches the
questions as asked.

| # | Ambiguity in WI | Ruling | Where implemented |
|---|---|---|---|
| 1 | Step 18 routes an under-min module to "skip ahead to section 4.4", but steps 23–26 describe a pulse-width escalation for the same condition | **Skip ahead is correct.** Under-min units proceed straight to §4.4 with no adjustment; the SPEC-31 line records the failure. The step-23 sweep exists in the runner only as `diagnose-low`, a non-persisting diagnostic | `phase_tune` under-min branch; `phase_diagnose_low` |
| 2 | §4.4 never says which module is seated during the safety-ADC reads, yet OPT ADC is module-dependent (measured: ~2247 mA with Left seated vs ~2358 mA with Right) | **Use the module with the higher measured power.** | `phase_tune` refuses to run unless `--seated` matches the higher-baseline side |
| 3 | Step 31 says round the 1.1× EE value "to the nearest integer" in the step text but "round down" in its results column | **Round down** for EE (1.1×). OPT (1.3×, step 30) stays round-to-nearest as the WI consistently states | `EE_DRIVE_CL = int(...)`, `OPT_DRIVE_CL = round(...)` |
| 4 | One module above max while the other is below min (single shared TA drive) is not covered by the decision tree | **NCR.** Unresolvable by tuning | Falls out of the branch logic; cross-check failure marks NCR |
| 5 | Step 18 "The Test is complete" cannot literally end the test (§4.4–4.6 follow) | Reading confirmed: it means §4.2 tuning is complete; the procedure continues | flow always proceeds to §4.4 |
| 6 | Steps 24/26 say to set TA_PULSE_WIDTH "to the new drive current found" | Copy-paste error in the WI; the Figure-Y line references show pulse width is meant | n/a (affects only the diagnostic sweep) |
| 7 | What does a factory-new console EPROM contain, and are keys beyond the WI's nine permitted in the final config? | **Factory-new is empty. Extra keys are permitted.** | `phase_finalize` preserves pre-existing `calibration`/`TEC_TRIP`; on an empty unit this is a no-op |
| 8 | §4.6 step text says `calibration-YYYYMMDD_HHMMSS.csv`, its results table says `test-YYYYMMDD_HHMMSS.csv` | Resolved from code: `CalibrationWorkflow.py` writes `calibration-{ts}.csv` from the calibration flow and `test-{ts}.csv` from the separate verification-test flow. **The step text is right; the results-table header is wrong** | (§4.6 not yet automated) |
| 9 | Power-cycle dwell, trigger frequency, single-vs-averaged ADC reads unspecified | **15 s dwell; assume/enforce 40 Hz; average a few ADC reads** | `POWER_OFF_DWELL_S = 15`, `assert_trigger_40hz()` (sets it if wrong), `ADC_SAMPLES = 5` averaged |
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
  parameters** (5 reads averaged, higher-power module seated). The registers
  latch their last value after the trigger stops — a post-stop read looks
  plausible and is wrong. Reads with the laser off return 0.
- **The EPROM is written once, at the end** (`finalize`), rather than the WI's
  incremental save-as-you-go through steps 9→33. The end state is identical;
  the write is verified key-by-key after a power cycle.
- **WI step 9 ("delete any data … paste the default configuration") is not
  performed.** On the dev unit it would have destroyed a real calibration
  block and the legacy `EE/OPT_THRESH`/`_GAIN` overrides. Instead the runner
  reads the existing config, preserves `calibration`/`TEC_TRIP`
  (permitted per ruling 7), and **drops** legacy `EE_THRESH`, `EE_GAIN`,
  `OPT_THRESH`, `OPT_GAIN` at write time — `omotion/laser.py` prefers those
  over `EE/OPT_DRIVE_CL` when both are present, so carrying them forward
  would silently defeat the freshly tuned limits.
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

## 4. Known gaps / not yet implemented

- §4.6 BFI/BVI calibration (bloodflow-app or `MotionInterface.start_calibration()`).
- Sensor-module serials/firmware and FPGA revisions in the report (WI step 8's
  inventory) — the DUT side is currently operator-asserted via `--seated`.
- Physical-setup attestation prompts (strap covers, cable bend, orientation)
  for an operator-guided single-command flow.
- The over-max branch was validated only against the temporary 125 µJ ceiling;
  the under-min skip-ahead branch is unreachable on this unit with the 75 µJ
  floor and remains logic-tested only.
- The WI names the TestApp/bloodflow-app as the instruments; whether this
  SDK-based runner can be the *official* method needs a WI revision or
  TP-00018 blessing.

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
