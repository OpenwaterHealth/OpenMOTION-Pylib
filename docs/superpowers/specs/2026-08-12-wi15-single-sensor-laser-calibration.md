# WI-00015 Single-Sensor Laser Calibration Specification

**Date:** 2026-08-12

**Status:** Draft for written review

**Procedure:** Single-Sensor Laser Calibration

## 1. Objective

Tune the shared console laser for a unit that will ship with exactly one
sensor module. The operator declares the installed side, and the procedure
tunes that module's 0 cm per-pulse energy as close as practical to 350
microjoules. It passes only with a valid final energy from 300 through 400
microjoules, inclusive.

This procedure is distinct from Dual-Sensor Laser Calibration. A two-sensor
unit may not be processed by running this procedure twice.

## 2. Authority and related specifications

This specification implements the Laser Calibration requirements in:

- `docs/WI-00015-automated-process-addendum.md`;
- `docs/superpowers/specs/2026-08-12-wi15-single-and-dual-runner-design.md`;
  and
- WI-00015 revision 2, as modified by the team-approved automated process.

The process addendum controls if this file is inadvertently interpreted in a
way that conflicts with the approved process.

## 3. Scope

### Included

- Operator declaration and confirmation of `left` or `right`.
- Exact one-sensor topology validation.
- Console/sensor identity and serial validation.
- Ophir connection, identity, configuration, and readback.
- Pre-existing configuration capture and exact default configuration write.
- Valid 0 cm energy measurement.
- Direction-specific tuning toward 350 microjoules.
- Final energy and register-readback acceptance.
- Structured state and report evidence.

### Excluded

- A second sensor or inter-sensor comparison.
- Safety ADC calibration.
- Static-phantom Measurement Calibration.
- Final TestApp UI.

## 4. Entry points and reusable API boundary

The operator entry point is
`scripts/wi15_single_sensor_laser_calibration.py`. Before any mutation it asks
the operator to select `left` or `right`, displays the selection, and requires
confirmation.

The shared implementation resides outside the script and receives explicit
inputs. It must not call `input()` or format terminal prompts. Conceptually it
accepts:

- declared side;
- motion interface/session;
- Ophir meter adapter;
- energy-measurement duration/configuration;
- configuration store;
- run-state/report sink; and
- cancellation/progress callbacks suitable for a future TestApp caller.

It returns a structured outcome containing `passed`, terminal disposition,
reason, selected side, measurements, settings, and artifact references. A
failed outcome maps to a nonzero script exit code.

## 5. Preconditions and fail-closed preflight

Preflight runs before step-9 configuration or laser action.

1. The console must be connected and responsive.
2. Exactly the declared sensor side must be connected.
3. The opposite side must not be connected.
4. Console and selected-sensor serial numbers must be non-`None` and
   non-empty after trimming.
5. Available firmware, FPGA, hardware ID, and related identity fields are
   read and recorded.
6. The Ophir COM object must instantiate.
7. USB scan must find a meter, the meter must open, and the configured channel
   must report an energy sensor.
8. Meter/sensor identity and calibration-due fields must be readable.
9. Every Ophir setting and readback in the process addendum must pass.

Any failure stops the procedure before configuration mutation or firing.

## 6. Default configuration setup

1. Read and preserve the complete existing User Configuration.
2. Write exactly the ten-key default object in the process addendum.
3. Require a successful SDK write result.
4. Read back the complete object and require exact keys and values.
5. Bring up the laser configuration and verify active TA pulse width, TA
   current, seed value, and 40 Hz trigger frequency.
6. Correct only trigger frequency when necessary. A mismatch in another
   required operating value fails the procedure.

The pre-existing, requested-default, and actual-default objects are retained
for the report.

## 7. Valid measurement definition

Every energy observation used for adjustment or acceptance must contain:

- more than 25 valid Ophir samples;
- standard deviation below 40 microjoules;
- repetition rate from 39 through 41 Hz inclusive; and
- finite mean, standard deviation, rate, minimum, and maximum.

Samples with nonzero Ophir status are discarded and counted. If an
observation fails, record it and stop the procedure; do not tune from its
mean.

## 8. Tuning algorithm

### 8.1 Initial measurement

Prompt the operator to place the declared module in the 0 cm fixture, record
the physical confirmation, and acquire a valid measurement at the default
operating point.

### 8.2 Downward adjustment

If the mean is above 350 microjoules:

1. leave TA pulse width unchanged;
2. reduce `TA_CURRENT_DRV` by 50 mA;
3. check requested-versus-active readback;
4. acquire a valid measurement; and
5. continue until the mean reaches or crosses 350, or the conservative
   current floor is reached.

When two adjacent permitted settings straddle 350, select the setting whose
valid measured mean is closest to 350. Reapply and verify that setting if it
is not the last one tested. Reaching the current floor without an acceptable
result is a terminal NCR.

### 8.3 Upward adjustment

If the mean is below 350 microjoules:

1. set `EE_PULSE_WIDTH_UL` and `OPT_PULSE_WIDTH_UL` to 660 microseconds and
   check readback;
2. leave TA current unchanged;
3. increase `TA_PULSE_WIDTH` by 10 microseconds;
4. check requested-versus-active readback;
5. acquire a valid measurement; and
6. continue until the mean reaches or crosses 350, or TA pulse width reaches
   600 microseconds.

When two adjacent permitted settings straddle 350, select the setting whose
valid measured mean is closest to 350 and reapply/verify it if necessary. If
600 microseconds is reached while energy remains below 300 microjoules, fail
NCR immediately. If the closest reachable setting is outside 300-400, fail
NCR.

### 8.4 No adjustment

If the initial mean is exactly 350, no setting adjustment is required. The
procedure still performs the final verification measurement and readback.

## 9. Final verification and acceptance

With the selected final settings active:

1. acquire a new valid measurement of the declared module;
2. require energy from 300 through 400 microjoules inclusive;
3. compare requested `TA_CURRENT_DRV` with active readback and require plus or
   minus 2 percent;
4. compare requested `TA_PULSE_WIDTH` with active readback and require plus or
   minus 2 percent; and
5. record requested values, readbacks, differences, and percent differences.

Only after all five checks pass, construct and write the complete passing
User Configuration:

- set `TA_CURRENT_DRV` and `TA_PULSE_WIDTH` to the final requested values;
- preserve the other approved default values;
- retain 660 for both pulse-width limits when upward pulse-width tuning was
  used, otherwise retain the default 550 limits; and
- require SDK write success and an exact immediate complete readback.

Do not power-cycle in this procedure. The passing readback becomes the
authoritative input to the separate Safety Calibration procedure, which owns
final safety-limit calculation and persistence verification.

## 10. Failure behavior

Topology, identity, Ophir, configuration, measurement-quality, adjustment
bound, final-energy, or readback failure returns nonzero and records a
terminal reason. Energy/bound failure is identified as NCR.

After terminal NCR, the procedure must not invoke Safety Calibration or write
the passing tuned-configuration handoff or final safety configuration. The
earlier required default
configuration write remains part of the record. There is no continue-anyway
prompt.

## 11. Report evidence

The report includes all common evidence in section 6 of the automated process
addendum and, specifically:

- declared and actual topology;
- selected side and operator confirmation;
- identities and serial-validation results;
- Ophir setup and readbacks;
- pre-existing, default, and final configuration;
- every energy observation and validity criterion;
- every requested setting, quantized readback, and adjustment;
- closest-to-350 selection rationale;
- final energy and 2 percent checks;
- passing tuned configuration and exact immediate readback;
- highlighted default-versus-final changes; and
- terminal outcome/NCR reason.

## 12. Automated tests

Unit tests cover:

- left and right success;
- neither, both, and wrong-side topology rejection;
- `None`, empty, and valid serials;
- every Ophir preflight failure and proof of no mutation/firing;
- default write failure, extra/missing key, and value mismatch;
- exactly 25 versus 26 valid pulses;
- standard-deviation, rate, and non-finite boundaries;
- initial 350 no-adjustment;
- downward steps, crossing selection, and current-floor NCR;
- upward steps, crossing selection, 600-microsecond success, and ceiling NCR;
- final 300/400 inclusive acceptance and outside failure;
- plus/minus 2 percent readback boundaries;
- tuned-configuration write failure and complete-readback mismatch;
- proof terminal NCR cannot reach final persistence; and
- required report fields and change highlighting.

## 13. Future TestApp integration

This procedure maps to one future TestApp button. The UI supplies the side
selection/confirmation and renders structured progress. It must call the same
shared implementation and may not reimplement topology, tuning, or acceptance
logic.
