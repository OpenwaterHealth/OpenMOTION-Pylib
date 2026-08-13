# WI-00015 Safety Calibration Specification

**Date:** 2026-08-12

**Status:** Implemented and software-verified; live hardware verification pending

**Procedure:** Safety Calibration

## 1. Objective

Derive and persist laser safety current and pulse-width limits from the
console's internal safety ADC measurements, prove those values survive a
power cycle, and run a normal 30-second scan with the persisted values active
and no overrides.

The ADC calculation is independent of sensor modules and does not require an
Ophir meter. The final normal scan uses the unit's declared shipping sensor
topology.

## 2. Authority and related specifications

This specification implements Safety Calibration in:

- `docs/WI-00015-automated-process-addendum.md`;
- `docs/superpowers/specs/2026-08-12-wi15-single-and-dual-runner-design.md`;
  and
- WI-00015 revision 2, as modified by the team-approved automated process.

The process addendum controls if the sources conflict.

## 3. Scope

### Included

- Console connection and identity validation.
- Current tuned configuration capture.
- TA current and pulse-width plus/minus 2 percent readback check.
- At least ten scaled-mA ADC samples per safety controller while firing.
- Mean, multiplier, and nearest-integer current-limit calculations.
- Pulse-width-limit calculation.
- Complete configuration write and immediate readback.
- Minimum 15-second power-off dwell and persistence verification.
- Normal 30-second sensor-data scan with no configuration overrides.
- Laser-safety-warning failure detection and report evidence.

### Excluded

- Ophir connection or energy measurement.
- Laser midpoint tuning.
- Static-phantom calibration calculations.
- A hard stored-state requirement proving Laser Calibration previously ran.
- Final TestApp UI.

## 4. Entry point and reusable API boundary

The operator entry point is a dedicated Safety Calibration script. Existing
low-level runner commands may remain available, but the dedicated procedure
is the supported operator flow.

Shared SDK code receives the motion interface/session, declared shipping
topology for the final scan, ADC sampling policy, configuration store,
power-cycle adapter, normal-scan adapter, warning source, and state/report
sink. It does not call `input()`.

It returns a structured outcome containing input configuration, raw ADC
samples, calculations, requested and readback configurations, restart proof,
scan/warning result, and terminal disposition.

## 5. Preconditions and local validation

Safety Calibration does not require stored evidence from Laser Calibration.
It independently validates the state it consumes:

1. Console must be connected, responsive, and have a non-`None`, non-empty
   serial number.
2. Read and preserve the complete current User Configuration.
3. Require finite, positive `TA_CURRENT_DRV` and `TA_PULSE_WIDTH` values and
   the required rate/limit keys needed to construct the final configuration.
4. Bring up the persisted operating point without temporary laser or safety
   overrides.
5. Compare requested TA current and pulse width with active hardware/UI
   readbacks and require each to be within plus or minus 2 percent.
6. Verify 40 Hz triggering, correcting trigger frequency if necessary.

Failure stops before ADC-derived configuration is written.

No sensor module or containment fixture is required for the ADC acquisition.

## 6. ADC sampling

### 6.1 Firing lifecycle

1. Perform normal console laser preflight.
2. Start the laser once.
3. Collect OPT and EE ADC samples while the laser is actively firing.
4. Stop the laser in a guaranteed cleanup path on success, cancellation, or
   exception.
5. Capture laser safety warnings throughout the firing window.

### 6.2 Sample validity

For each of `SAFETY_OPT ADC_DATA` and `SAFETY_EE ADC_DATA`:

- collect at least ten successful values;
- use scaled engineering-unit mA returned/displayed by the SDK/TestApp
  conversion, never raw ADC counts;
- reject exceptions, missing values, booleans, NaN, and infinity;
- record rejected reads and their reasons; and
- fail rather than calculating from fewer than ten valid readings.

Sampling may be sequential or interleaved, provided every accepted value was
read during the same active firing period and both controllers meet the
minimum count.

## 7. Safety calculations

Use the arithmetic mean of every accepted sample for each controller.

1. `opt_mean_mA = sum(opt_samples) / len(opt_samples)`
2. `ee_mean_mA = sum(ee_samples) / len(ee_samples)`
3. `OPT_DRIVE_CL = nearest_integer(opt_mean_mA * 1.3)`
4. `EE_DRIVE_CL = nearest_integer(ee_mean_mA * 1.1)`

Record the sample list, count, mean, multiplier, unrounded product, rounding
operation, and result for both controllers.

Use one shared explicit nearest-integer helper so Python's implicit/banker's
rounding cannot make report and configuration disagree at an exact half.
The helper and its tie behavior must be unit tested and stated in the report.

## 8. Pulse-width limit calculation

Read the requested integer `TA_PULSE_WIDTH` from User Configuration.

- When below 600 microseconds, calculate both `EE_PULSE_WIDTH_UL` and
  `OPT_PULSE_WIDTH_UL` as `nearest_integer(TA_PULSE_WIDTH * 1.1)`.
- At exactly 600 microseconds, require and retain 660 for both limits.
- A pulse width above 600 or a 600-microsecond operating point without the
  required 660 limits is a configuration failure.

Do not change the rate lower limits, tuned TA values, seed value, `TEC_TRIP`,
or unrelated permitted configuration data.

## 9. Configuration write and immediate verification

1. Construct the complete intended configuration by updating the current
   object with the four derived safety limits.
2. Preserve all other permitted current keys and values.
3. Write the complete object and require successful SDK operation.
4. Read it back immediately.
5. Compare every intended key/value, not only the four updated fields.
6. On write failure or mismatch, fail and do not claim persistence.

Record current, intended, and immediate-readback configurations with a
highlighted comparison.

## 10. Power-cycle persistence verification

1. Stop all laser/scan activity.
2. For a manual cycle, have the operator confirm readiness while the unit is
   still connected, begin connection-state observation, and only then instruct
   the operator to power the unit off. Do not require the operator to complete
   the power transition before observation begins.
3. Keep power off for at least 15 measured seconds.
4. After the dwell, again begin observation before instructing the operator to
   restore power, then wait for the console to reconnect.
5. Use firmware uptime or equivalent evidence to prove a restart occurred.
6. Read the complete User Configuration.
7. Require every intended key/value to be unchanged.

An unproven cycle, insufficient dwell, reconnect failure, read failure, or
mismatch fails the procedure.

## 11. Normal 30-second scan verification

The ADC calculation itself is sensor-independent. For this final end-to-end
check, connect the unit's declared shipping topology:

- one-sensor: exactly the declared left or right module;
- two-sensor: both left and right modules.

Required sensor serial numbers must be non-`None` and non-empty. Run the
ordinary production/SDK sensor-data scan path for 30 seconds using the
persisted configuration. Do not pass temporary trigger, laser, safety, or
calibration overrides.

Pass only if:

- the scan starts;
- it runs for the required duration and completes normally;
- expected topology remains connected;
- no laser safety warning is raised; and
- no safety interlock prevents normal scanning.

Any failure returns nonzero. Always stop scan/trigger activity in cleanup.

## 12. Acceptance and failure behavior

Safety Calibration passes only when local preflight, readback tolerance, both
ADC sample sets, all calculations, immediate configuration verification,
power-cycle persistence, and the normal scan pass.

The procedure records the exact failed gate. It does not require Ophir or
stored proof of a prior procedure, and it does not offer a continue-anyway
path after a failed write, persistence check, or safety scan.

## 13. Report evidence

Record:

- console and final-scan sensor identities/topology;
- complete input configuration;
- TA requested/readback values and 2 percent calculations;
- trigger frequency result;
- every accepted/rejected OPT and EE ADC read;
- mean, multiplier, unrounded result, rounding/tie rule, and final limit;
- pulse-width-limit calculation;
- complete intended and immediate-readback configurations;
- power-off timestamps/dwell and restart proof;
- post-restart complete comparison;
- scan configuration showing no overrides;
- scan start/end/duration/outcome and every safety warning; and
- terminal disposition and reason.

## 14. Automated tests

Unit tests cover:

- no Ophir dependency;
- console connection and serial failures;
- missing/invalid current configuration fields;
- plus/minus 2 percent boundaries;
- trigger correction;
- fewer than ten versus ten ADC samples;
- rejected exceptions, NaN, infinity, and booleans;
- scaled-mA usage and exact means;
- both multipliers and nearest-integer tie behavior;
- pulse widths below 600, exactly 600, and above 600;
- write failure and complete-readback mismatch;
- measured 15-second dwell and restart-proof failures;
- one- and two-sensor normal-scan topology;
- proof that no scan overrides were supplied;
- early scan failure, disconnect, warning, and successful 30-second scan;
- guaranteed laser/scan cleanup; and
- required report fields.

## 15. Future TestApp integration

This procedure maps to one future TestApp button. The TestApp presents
connection, power-cycle, and scan progress while calling the same shared SDK
implementation. It may not recalculate limits or weaken warning/persistence
gates in UI code.

## 16. Implementation mapping and verification status

- Safety rules and immutable evidence records:
  `omotion/WI15SafetyCalibration.py`.
- UI-neutral procedure orchestration:
  `omotion/WI15SafetyCalibrationWorkflow.py`.
- Motion console, FPGA, power-cycle, and normal-scan adapter:
  `omotion/WI15SafetyCalibrationHardware.py`.
- Auditor-readable HTML evidence:
  `omotion/WI15SafetyCalibrationReport.py`.
- Current script-only operator entry point:
  `scripts/wi15_safety_calibration.py`.

The automated domain, workflow, adapter, report, and operator-script tests pass
against simulated hardware. A supervised run on representative single- and
dual-sensor hardware remains required before this procedure is released for
production use. That verification must confirm scaled ADC register semantics,
disconnect/reconnect observation, configuration persistence, ordinary scan
duration, and live laser-safety telemetry.
