# WI-00015 Automated Process Addendum

**Date:** 2026-08-12

**Status:** Team-approved automation requirements; implementation pending

**Applies to:** WI-00015 scripted execution on one- and two-sensor
Open-Motion units

## 1. Purpose and authority

This addendum makes the team-approved automated process deterministic. It
defines the setup, calculation, acceptance, persistence, and reporting rules
that were implicit or ambiguous in the process description.

The process has three calibration categories and four operator-facing
procedures:

1. Single-Sensor Laser Calibration
2. Dual-Sensor Laser Calibration
3. Safety Calibration
4. Measurement Calibration

Application screenshots are not required from the automated flow. The
generated report provides the equivalent objective evidence described in
section 6.

The following team-approved changes from the literal WI-00015 revision 2
sequence are intentional:

- an initial left/right energy differential greater than 100 microjoules is
  an immediate NCR;
- tuning targets a two-sensor midpoint as close as practical to 350
  microjoules, with the two readings preferably equidistant around it;
- up to three complete post-adjustment cross-checks are permitted;
- a 30-second normal scan is added after Safety Calibration;
- Measurement Calibration uses a 15-second calibration scan and a 2-second
  validation scan; and
- report evidence replaces TestApp screenshots.

## 2. Unit topology and common identity checks

The workflow must run against the topology in which the unit will ship.

- A two-sensor unit is executed as a two-sensor unit. It may not be processed
  by running the one-sensor procedure twice.
- A one-sensor unit is executed by the separate one-sensor runner. The
  operator selects `left` or `right` at startup and confirms the choice.
- The connected topology must match the declared topology before a mutating
  operation or laser action.
- The console and every sensor required by the declared topology must be
  connected and must report a serial number that is neither `None` nor empty.
- A missing module on a declared two-sensor unit is a topology failure, not a
  one-sensor unit.

The console and required sensors' serial numbers, firmware, hardware IDs, and
other available identity data are recorded in the report.

## 3. Laser Calibration addenda

Laser Calibration is the only stage that requires an Ophir energy meter.
Safety Calibration and Measurement Calibration do not require an Ophir
connection.

### 3.1 Ophir connection and configuration

Before the default console configuration is written or the laser is fired,
the Laser Calibration runner must:

1. instantiate the Ophir COM interface;
2. scan for an attached meter;
3. open the meter;
4. confirm that an energy sensor is present on the selected channel;
5. read and record meter and sensor identity, serial numbers, and calibration
   due information; and
6. configure and read back the following WI settings:

| Setting | Required value |
|---|---:|
| Measurement mode | Energy |
| Range | 2.0 mJ |
| Wavelength | 795 nm |
| Pulse length | 1.0 ms |
| Threshold | Minimum available |
| Display/statistics averaging | 3 seconds, when applicable |
| Graph/display mode | Statistics, when applicable |

COM failure, no meter, open failure, a missing energy sensor, unreadable
identity, or a configuration/readback mismatch prevents Laser Calibration
from starting. No step-9 write or laser action is permitted after that
failure.

### 3.2 Default console configuration

Before overwriting the User Configuration, preserve its complete contents in
the run record. Replace the User Configuration with exactly the following
starting values:

```json
{
  "TA_PULSE_WIDTH": 500,
  "TA_CURRENT_DRV": 5000,
  "SEED_CW_GAIN": 140,
  "EE_PULSE_WIDTH_UL": 550,
  "EE_RATE_LL": 23125,
  "EE_DRIVE_CL": 9999,
  "OPT_PULSE_WIDTH_UL": 550,
  "OPT_RATE_LL": 23125,
  "OPT_DRIVE_CL": 9999,
  "TEC_TRIP": 40
}
```

Check the write result and read the configuration back immediately. Any
failed operation, missing key, extra key, or mismatched value fails the
stage. Before energy measurement, also verify the active TA pulse width, TA
drive current, seed value, and 40 Hz trigger frequency. Correct the trigger
frequency to 40 Hz if necessary; other readback failures stop the stage.

### 3.3 Valid energy measurement

Every Ophir measurement used for tuning or acceptance must satisfy all of the
following:

- more than 25 valid per-pulse energy samples;
- standard deviation less than 40 microjoules;
- repetition rate from 39 through 41 Hz, inclusive; and
- no non-finite calculated statistic.

Ophir samples carrying a nonzero status word are invalid sentinels and are
excluded. An observation that fails any measurement-quality criterion is
recorded but may not be used to tune or pass the unit. The stage fails and
requires a new valid execution.

For each observation, direct streaming continues until at least 26 valid
status-zero samples have been collected or the bounded 2.0-second acquisition
maximum expires. The stream stops as soon as the returned batch reaches the
target; all valid samples in that batch are included in the statistics and
rate calculation. Nonzero-status samples remain discarded and counted. The
stream is always stopped. If the timeout expires below 26 valid samples, the
partial observation is recorded and fails the existing criteria.

Because the Ophir API may return buffered samples from an earlier stream, the
first nonempty, complete `GetData` batch after each `StartStream` is drained as
session-priming data. The complete priming batch is excluded: its values,
timestamps, and statuses do not contribute to statistics, repetition rate,
valid count, or discarded count. Fresh-sample collection starts after the
drain and remains subject to the same 2.0-second overall maximum. If no fresh
observation reaches 26 valid samples, the result fails the unchanged criteria.
Misaligned arrays still fail immediately, and every successfully started
stream is stopped or its stop failure is propagated for cleanup retry.

The energy acceptance interval is 300 through 400 microjoules, inclusive.

### 3.4 Approved laser adjustment algorithm

The allowed controls are direction-specific:

- To reduce energy, adjust `TA_CURRENT_DRV` downward in 50 mA increments.
- To increase energy, first set `EE_PULSE_WIDTH_UL` and
  `OPT_PULSE_WIDTH_UL` temporarily to 660 microseconds, then adjust
  `TA_PULSE_WIDTH` upward in 10 microsecond increments.
- `TA_PULSE_WIDTH` may not exceed 600 microseconds. Reaching 600
  microseconds while the applicable module remains below 300 microjoules is
  an immediate failure/NCR.
- The implementation retains its existing conservative TA-current floor. If
  that floor is reached before an acceptable result, the unit fails/NCR.
- Every adjustment measurement must pass section 3.3 before it is used.

For a two-sensor unit:

1. Measure the left module and then the right module at the default operating
   point.
2. If the absolute difference is greater than 100 microjoules, fail/NCR
   immediately.
3. Compute the observed midpoint `(left + right) / 2` and the inter-module
   difference.
4. If energy must be reduced, tune using the higher-reading module. Its
   expected target is `350 + difference / 2` microjoules.
5. If energy must be increased, tune using the lower-reading module. Its
   expected target is `350 - difference / 2` microjoules.
6. Select the reachable discrete setting that places the expected midpoint
   closest to 350 microjoules without violating the approved direction,
   increments, or limits. If the current discrete setting is already the
   closest reachable setting, no adjustment is required.
7. Perform a complete cross-check by measuring both left and right again.
8. Pass when both valid readings are within 300-400 microjoules. The report
   also records their midpoint, distance from 350, and asymmetry about 350.
9. If either reading is outside the acceptance interval, recompute from the
   latest valid pair and repeat the approved adjustment and complete
   cross-check.
10. A maximum of three complete cross-checks is allowed. If both modules are
    not in range after the third, fail/NCR immediately.

For a one-sensor unit:

1. Measure only the declared side.
2. Apply the same direction-specific controls and discrete increments to
   move the reading as close as practical to 350 microjoules.
3. Pass when a valid final reading is within 300-400 microjoules.
4. Fail/NCR when the permitted pulse-width ceiling, TA-current floor, or
   maximum adjustment attempts are exhausted without an in-range reading.
5. Do not execute a second-module cross-check.

An energy failure/NCR is terminal for that execution. It returns a nonzero
result and prohibits final tuned/safety configuration persistence. There is
no development override that continues after the failure.

### 3.5 Final laser readback

Before Safety Calibration calculations, compare the requested final
`TA_CURRENT_DRV` and `TA_PULSE_WIDTH` with their active hardware/UI
readbacks. Each readback must be within plus or minus 2 percent of its
requested value. Record the requested value, readback, absolute difference,
percent difference, and result. A value outside tolerance fails the process.

### 3.6 Successful tuned-configuration handoff

After final energy and readback acceptance, write the final
`TA_CURRENT_DRV` and `TA_PULSE_WIDTH` into the complete User Configuration and
check the operation and immediate complete readback. Preserve the other
approved default keys.

When upward pulse-width tuning was used, retain 660 microseconds for both
pulse-width upper limits as a provisional safe handoff to Safety Calibration.
Otherwise retain the default 550-microsecond limits. Safety Calibration owns
the final pulse-width-limit calculation and subsequent power-cycle
verification.

Laser Calibration does not power-cycle the unit. Its passing configuration is
the authoritative input to standalone Safety Calibration. A failed/NCR Laser
Calibration must not perform this tuned-configuration handoff; the earlier
required default configuration remains the only User Configuration written by
that failed execution.

## 4. Safety Calibration addenda

The safety ADC calibration is internal to the console and does not require a
sensor module or beam-containment fixture. Sensor modules do not influence
the safety ADC values.

### 4.1 ADC acquisition and calculations

Start the laser using the normal persisted tuned configuration. While it is
firing:

1. collect at least 10 valid scaled-mA readings from `SAFETY_OPT` `ADC_DATA`;
2. collect at least 10 valid scaled-mA readings from `SAFETY_EE` `ADC_DATA`;
3. reject read failures and non-finite values;
4. calculate and record the arithmetic mean for each controller;
5. set `OPT_DRIVE_CL` to the OPT mean multiplied by 1.3 and rounded to the
   nearest integer; and
6. set `EE_DRIVE_CL` to the EE mean multiplied by 1.1 and rounded to the
   nearest integer.

The values used in these calculations are engineering-unit mA values, not
raw ADC counts. Record every accepted sample, sample count, mean, multiplier,
unrounded product, rounding rule, and final integer.

### 4.2 Pulse-width safety limits

Read `TA_PULSE_WIDTH` from the User Configuration.

- If it is less than 600 microseconds, set both `EE_PULSE_WIDTH_UL` and
  `OPT_PULSE_WIDTH_UL` to `TA_PULSE_WIDTH * 1.1`, rounded to the nearest
  integer.
- If it is 600 microseconds, retain the 660-microsecond limits established by
  the approved upward-tuning path.

### 4.3 Write, restart, and normal-scan verification

Write the complete updated User Configuration and check the operation and
immediate readback. Power the unit off for at least 15 seconds, restart it,
and verify every intended key is unchanged.

Then run a normal 30-second sensor-data scan using the unit's declared
shipping topology and the persisted calibrated configuration. Do not supply
temporary trigger, laser, safety, or calibration overrides. Fail if the scan
cannot complete or if a laser safety warning is raised. This verification
scan requires the unit's normal sensor topology, although the preceding ADC
calculation itself is console-only.

## 5. Measurement Calibration corrections and addenda

Calibrate one sensor module at a time on the static phantom. Do not
parallelize left and right Measurement Calibration. A two-sensor unit must
calibrate both sides sequentially; a one-sensor unit calibrates only its
declared side.

For each targeted side:

1. Place the module on the static phantom in the required orientation with
   its weight. Do not touch, bump, or expose the setup to vibration during
   acquisition.
2. Run a 15-second calibration scan and calculate the time average for every
   active camera's image mean and contrast. Contrast is the average of each
   frame's `standard deviation / mean`, not the ratio of averaged standard
   deviation to averaged mean.
3. Calculate the dark image mean for every active camera from the applicable
   laser-off frames.
4. Apply the versioned factory thresholds per active camera:
   - minimum mean: `[40, 80, 80, 80, 80, 80, 80, 40]`;
   - minimum average contrast: `0.25` for every camera; and
   - maximum dark image mean: `3.0` for every camera.
5. Equality at a threshold passes. If any active camera is below its mean or
   contrast minimum, above its dark maximum, missing data, or non-finite,
   fail calibration and stop before writing calibration data.
6. For each passing active camera calculate:
   - `I_min = 0`;
   - `I_max = 2 * average image mean`;
   - `C_min = 0`; and
   - `C_max = average contrast`.
7. Append/update only the targeted side's calibration data in User
   Configuration. Preserve the already-calibrated or otherwise existing
   non-target side. Check the write result and immediate readback.
8. Run a 2-second validation scan and calculate BFI and BVI per active
   camera across the scan.
9. Require every active camera to have BFI from -0.5 through 0.5 and BVI from
   4.5 through 5.5, inclusive. Do not average cameras together for the pass
   decision. Any camera failure fails Measurement Calibration.

After all required sides pass, power the unit off for at least 15 seconds,
restart it, and verify the complete final User Configuration, including the
laser, safety, and calibration values. A failed write, failed validation,
failed restart proof, or persistence mismatch fails the process.

## 6. Automated report contents

The generated report is the authoritative execution evidence and replaces
the WI's requested application screenshots. It must clearly identify the
process version and include:

- terminal outcome and, when applicable, the exact failed condition and NCR
  disposition;
- operator, execution date/time, software/build revision, SDK version, and
  relevant procedure/reference revisions;
- test fixture identity and calibration status;
- declared unit topology and selected side for a one-sensor unit;
- console and required sensor identities, serial numbers, firmware, hardware
  IDs, and FPGA firmware where available;
- Ophir meter and energy-sensor identities, serial numbers, calibration due
  information, applied settings, and setting readbacks for Laser Calibration;
- the complete pre-existing User Configuration;
- the exact default configuration and its write/readback evidence;
- every valid and invalid energy observation, including raw accepted sample
  count, discarded count, mean, standard deviation, rate, minimum, maximum,
  and each measurement-quality result;
- initial left/right differential, every requested and quantized parameter
  value, every adjustment, each cross-check pair, midpoint, distance from
  350, asymmetry, and acceptance result;
- a default-versus-final configuration comparison with every changed value
  visibly highlighted;
- the step-28 requested/readback values and percent differences;
- every accepted safety ADC sample, mean, multiplier, unrounded product,
  rounded threshold, and write/readback evidence;
- the pulse-width-limit calculation;
- each power-cycle dwell, restart evidence, and complete persistence
  comparison;
- the 30-second normal-scan result and any laser safety warnings;
- per side and camera: calibration-scan mean, average contrast, dark image
  mean, thresholds, individual results, calculated `I_min`, `I_max`,
  `C_min`, and `C_max`;
- per side and camera: validation-scan BFI, BVI, thresholds, and individual
  results;
- calibration CSV/JSON artifact names and paths; and
- final complete User Configuration and post-calibration restart comparison.

The report must not claim that a later operation occurred after a terminal
failure. Raw run-state and CSV/JSON artifacts remain available alongside the
human-readable report.

## 7. Phase independence

The normal guided workflow executes Laser Calibration, Safety Calibration,
and Measurement Calibration in that order. The scripts do not impose a hard
stored-state prerequisite requiring evidence that an earlier stage passed
when an operator invokes a later stage independently. Each stage still
enforces its own connection, configuration, measurement, write, and
acceptance gates.
