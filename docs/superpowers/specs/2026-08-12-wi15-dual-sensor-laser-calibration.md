# WI-00015 Dual-Sensor Laser Calibration Specification

**Date:** 2026-08-12

**Status:** Implemented and verified in software and on dual-sensor hardware

**Procedure:** Dual-Sensor Laser Calibration

## 1. Objective

Tune the shared console laser for a unit that will ship with both left and
right sensor modules. The procedure rejects an initial differential greater
than 100 microjoules, adjusts the shared laser so the two-sensor midpoint is
as close as practical to 350 microjoules, and passes only when both valid
final readings are within 300-400 microjoules inclusive.

It permits at most three complete post-adjustment cross-checks.

## 2. Authority and related specifications

This specification implements the Dual-Sensor Laser Calibration requirements
in:

- `docs/WI-00015-automated-process-addendum.md`;
- `docs/superpowers/specs/2026-08-12-wi15-single-and-dual-runner-design.md`;
  and
- WI-00015 revision 2, as modified by the team-approved automated process.

The process addendum controls if the sources conflict.

## 3. Scope

### Included

- Exact two-sensor topology validation.
- Console/sensor identity and serial validation.
- Ophir connection, identity, configuration, and readback.
- Pre-existing configuration capture and exact default configuration write.
- Valid left and right 0 cm measurements.
- Initial 100-microjoule differential gate.
- Direction-specific tuning toward a 350-microjoule midpoint.
- Up to three complete left/right cross-checks.
- Final dual-energy and register-readback acceptance.
- Structured state and report evidence.

### Excluded

- Processing either side as a standalone one-sensor unit.
- Safety ADC calibration.
- Static-phantom Measurement Calibration.
- Final TestApp UI.

## 4. Entry point and reusable API boundary

The operator entry point is
`scripts/wi15_dual_sensor_laser_calibration.py`. It guides each required
change from the left module to the right module or from the right module to
the left module, but does not own the tuning calculations. Consecutive
measurements of the same seated module do not repeat the placement prompt.

Shared SDK code receives explicit declared sides, motion interface/session,
Ophir adapter, configuration store, measurement settings, state/report sink,
progress/cancellation callbacks, and an injected placement-change
acknowledgement callback. It must not call `input()`.

It returns a structured outcome containing the initial pair, differential,
tuning choices, cross-checks, final settings, terminal disposition, and
artifact references. Failure maps to a nonzero script exit code.

Every operator-facing step and report event uses descriptive audit language
that states the phase, sensor side, reason, requested action, and result when
applicable. Internal method names and terse event codes may appear in JSON
field names, but they are not sufficient operator or report labels.

## 5. Preconditions and fail-closed preflight

Before configuration mutation or laser action:

1. the console, left sensor, and right sensor must be connected and
   responsive;
2. console, left, and right serial numbers must be non-`None` and non-empty;
3. available firmware, FPGA, hardware ID, and identity data must be recorded;
4. Ophir COM instantiation, scan, open, energy-sensor presence, identity, and
   calibration-due reads must pass; and
5. all Ophir settings and readbacks in the process addendum must pass.

Any failure stops before default configuration or firing. A missing side is
not reinterpreted as a one-sensor unit.

## 6. Default configuration setup

1. Read and preserve the complete existing User Configuration.
2. Write exactly the ten-key default object in the process addendum.
3. Require successful SDK write and exact complete readback.
4. Bring up and verify active TA pulse width, TA current, seed value, and 40
   Hz trigger frequency.
5. Correct only trigger frequency if needed; fail on another required
   operating mismatch.

Retain pre-existing, requested-default, and actual-default objects.

## 7. Valid measurement definition

Every left/right observation used for a calculation or decision must have:

- more than 25 valid samples after Ophir sentinel filtering;
- standard deviation below 40 microjoules;
- repetition rate from 39 through 41 Hz inclusive; and
- finite mean, standard deviation, rate, minimum, and maximum.

Record invalid observations but stop rather than using their means.

## 8. Initial pair and differential gate

1. Prompt the operator to seat the left module and acquire a valid
   measurement.
2. Prompt the operator to seat the right module and acquire a valid
   measurement.
3. Calculate `difference = abs(left - right)` and
   `midpoint = (left + right) / 2`.
4. If `difference > 100`, record the pair and fail/NCR immediately.
5. At exactly 100, continue; final acceptance still requires both readings
   within 300-400.

The initial pair is a baseline, not one of the three post-adjustment
cross-checks.

The workflow tracks the side most recently acknowledged as seated. The first
measurement requires acknowledgement of the left module. The second requires
acknowledgement of the switch to the right module. The acknowledgement must
identify both the side and serial number. If the operator declines or cancels,
the run fails before the associated firing or measurement.

## 9. Approved midpoint-tuning algorithm

For every tuning round, use the latest valid left/right pair.

### 9.1 Midpoint above 350

1. Select the higher-reading module.
2. Calculate its expected target as `350 + difference / 2`.
3. Keep TA pulse width fixed.
4. Reduce `TA_CURRENT_DRV` by 50 mA per step.
5. After each checked write/readback, acquire a valid measurement of the
   selected module.
6. Continue until the selected measurement reaches/crosses its expected
   target or the conservative current floor is reached.
7. If adjacent permitted settings straddle the target, select the one whose
   measurement is closest, then reapply/read back it if necessary.
8. Reaching the current floor without an acceptable reachable setting is a
   terminal NCR.

### 9.2 Midpoint below 350

1. Select the lower-reading module.
2. Calculate its expected target as `350 - difference / 2`.
3. Temporarily set both pulse-width upper limits to 660 microseconds and
   verify readback.
4. Keep TA current fixed.
5. Increase `TA_PULSE_WIDTH` by 10 microseconds per step.
6. After each checked write/readback, acquire a valid measurement of the
   selected module.
7. Continue until the selected measurement reaches/crosses its expected
   target or TA pulse width reaches 600 microseconds.
8. If adjacent permitted settings straddle the target, select the closer one
   and reapply/read back it if necessary.
9. At 600 microseconds, a selected measurement below 300 is an immediate
   terminal NCR.

### 9.3 Midpoint at 350 or no improving discrete step

Do not alter the setting when the midpoint is exactly 350 or when the current
discrete setting is already the closest permitted setting. Continue to a
complete cross-check.

Before the first tuning measurement of the selected module, request a
placement change only when that selected side differs from the side currently
seated. Leave the selected module seated across consecutive steps of the same
tuning sweep. Record the sensor side on every measurement even when no new
placement acknowledgement is required.

## 10. Complete cross-check loop

After every adjustment decision, including no adjustment:

1. apply and verify the selected final setting for that round;
2. prompt for and measure left;
3. prompt for and measure right;
4. increment `crosscheck_count` only after both valid measurements exist;
5. calculate difference, midpoint, midpoint distance from 350, and individual
   signed offsets from 350; and
6. pass immediately if both readings are within 300-400 inclusive.

If either side is outside range and fewer than three complete cross-checks
have run, recompute the next adjustment from the latest pair. If either side
is outside range after cross-check three, fail/NCR immediately.

An invalid measurement does not consume a complete cross-check, but it fails
the current execution rather than silently retrying or tuning from partial
data.

The placement-change rule applies to every cross-check: prompt only when the
next required side differs from the currently seated side. A complete
cross-check still always measures left first and right second.

## 11. Final readback and acceptance

After a passing cross-check:

1. require requested-versus-active `TA_CURRENT_DRV` within plus or minus 2
   percent;
2. require requested-versus-active `TA_PULSE_WIDTH` within plus or minus 2
   percent;
3. retain the passing left/right pair and all midpoint metrics; and
4. construct the complete passing User Configuration with the final TA
   current/pulse values, the other approved defaults, and provisional
   pulse-width limits of 660 when upward pulse tuning was used (otherwise
   550);
5. require successful write and exact immediate complete readback; and
6. mark that readback as the authoritative input to Safety Calibration.

Both energy bounds and the readback-tolerance bounds are inclusive.
Dual-Sensor Laser Calibration does not power-cycle; Safety Calibration owns
final safety-limit calculation and persistence verification.

## 12. Failure behavior

Topology, identity, Ophir, default configuration, measurement-quality,
initial differential, adjustment bound, third cross-check, or readback
failure returns nonzero with an exact reason. Energy, differential,
adjustment-bound, and third-cross-check failures are NCR dispositions.

After terminal NCR, no final tuned/safety configuration write or later guided
phase may execute. The required earlier default write remains recorded. The
guided runner has no continue-anyway path.

## 13. Report evidence

In addition to common report requirements, record:

- exact declared and actual dual topology;
- initial left/right observations, differential, and midpoint;
- selected tuning side and why;
- every acknowledged physical placement change, including side, serial
  number, procedure phase, and operator response;
- target calculation for every round;
- every current/pulse step and quantized readback;
- each complete cross-check number and pair;
- each pair's differential, midpoint, distance from 350, and asymmetry;
- final 300/400 and 2 percent results;
- passing tuned configuration and exact immediate readback;
- highlighted default-versus-final changes; and
- terminal outcome and NCR reason.

## 14. Automated tests

Unit tests cover:

- valid dual topology and either-side-missing rejection;
- all three required serial checks;
- Ophir preflight failures and proof of no mutation/firing;
- default configuration write/readback failures;
- measurement-quality boundaries;
- differential 100 accepted and greater than 100 NCR;
- above-midpoint higher-side selection and current stepping;
- below-midpoint lower-side selection and pulse stepping;
- target-straddling closest-setting selection;
- no-improvement/no-adjustment behavior;
- first-, second-, and third-cross-check pass;
- failure immediately after cross-check three;
- invalid partial cross-check behavior;
- 300/400 inclusive energy bounds and plus/minus 2 percent readback bounds;
- tuned-configuration write failure and complete-readback mismatch;
- proof NCR prevents final writes;
- required report contents;
- side-change-only prompt behavior, including no duplicate prompt during a
  same-side tuning sweep;
- declined/canceled placement acknowledgement preventing the associated
  measurement; and
- descriptive operator/report labels for initial measurements, tuning
  rationale, adjustment steps, cross-check results, and final verification.

## 15. Future TestApp integration

This procedure maps to one future TestApp button. The TestApp provides swap
prompts and progress presentation but calls the same shared implementation.
It may not duplicate or relax topology, differential, tuning, cross-check, or
failure logic.

## 16. Implementation mapping and verification

The software implementation is divided at the intended reusable boundaries:

- shared constants, validation, topology, paired metrics, and selection rules:
  `omotion/calibration/laser.py`;
- UI-neutral dual procedure and immutable evidence model:
  `omotion/calibration/dual_sensor_laser.py`;
- exact-dual Motion preflight, pre-fire topology guards, and shared Ophir
  acquisition: `omotion/calibration/laser_hardware.py`;
- auditor-readable HTML evidence:
  `omotion/calibration/dual_sensor_laser_report.py`;
- operator CLI and artifact/resource finalization:
  `scripts/wi15_dual_sensor_laser_calibration.py`; and
- invocation guidance: `scripts/WI15_PROCEDURES.md`.

Focused automated coverage is provided by:

- `tests/test_wi15_laser_calibration.py`;
- `tests/test_wi15_dual_sensor_laser_calibration.py`;
- `tests/test_wi15_laser_calibration_hardware.py`;
- `tests/test_wi15_dual_sensor_laser_calibration_report.py`; and
- `tests/test_wi15_dual_sensor_laser_script.py`.

Software verification on 2026-08-13 completed with 322 passing tests in the
exact single/dual WI-00015 matrix and 1,125 passing tests with 207
hardware-marked tests deselected in the repository hardware-independent
suite. Static compilation, Ruff, forbidden-dependency, and diff checks also
passed.

A live dual-sensor execution passed on 2026-08-13 using run
`WI-00015-20260813T190000Z` and commit `f2af676`. The exact topology contained
console and left serial `ZZZ99Z99999` plus right serial `WWWA4Q40005`. The
initial valid means were 316.107 uJ left and 299.867 uJ right, giving a
16.240 uJ differential and 307.987 uJ midpoint. Approved upward tuning of the
right sensor selected a requested 570 us pulse width, with 569.92 us active
readback. Cross-check 1 passed at 363.962 uJ left and 345.429 uJ right, with a
354.695 uJ midpoint and 18.533 uJ differential. The final complete User
Configuration read back exactly, both final active-setting checks passed,
JSON and HTML artifacts finalized, and no trigger, active-restoration, or
resource-cleanup failure was recorded.

Post-refactor live regression testing passed on 2026-08-13 using commit
`bf55d72` and run `WI-00015-20260813T221750Z`. The exact dual topology and all
three required non-null identities remained stable. The initial accepted
means were 321.333 microjoules left and 306.333 microjoules right, giving a
15.000-microjoule differential. The approved right-side upward sweep selected
a requested 560-microsecond pulse width. Cross-check 1 passed at 362.321
microjoules left and 338.519 microjoules right. All ten observations met the
acquisition-quality criteria, both final active-setting checks passed, the
complete final configuration read back, and finalized JSON and HTML artifacts
recorded no trigger, restoration, or resource-cleanup failure.

The same build exercised two fail-closed paths before that pass. Run
`WI-00015-20260813T221643Z` rejected a missing right sensor before measurement.
Run `WI-00015-20260813T221251Z` used an exact dual topology but returned
`failed_ncr` when the selected sensor remained below 300 microjoules at the
600-microsecond ceiling. Both runs finalized their evidence; the bound NCR
restored active defaults and did not write the passing tuned configuration.
