# WI-00015 Single- and Two-Sensor Runner Design

**Date:** 2026-08-12

**Status:** Procedure decomposition approved; detailed specifications awaiting
written-review confirmation

**Branch:** `feature/214-wi15-tuning-runner`

## 1. Purpose

Correct the scripted WI-00015 implementation, make the existing guided
workflow explicitly two-sensor, add a separate one-sensor guided workflow,
and create a draft one-sensor work instruction.

The authoritative deterministic process requirements are in
`docs/WI-00015-automated-process-addendum.md`. This design defines how the SDK
and scripts implement that process without coupling safety logic to a CLI,
so the same workflow can later be invoked by TestApp buttons.

The four operator-facing procedure specifications are:

- `docs/superpowers/specs/2026-08-12-wi15-single-sensor-laser-calibration.md`;
- `docs/superpowers/specs/2026-08-12-wi15-dual-sensor-laser-calibration.md`;
- `docs/superpowers/specs/2026-08-12-wi15-safety-calibration.md`; and
- `docs/superpowers/specs/2026-08-12-wi15-measurement-calibration.md`.

## 2. Sources and precedence

The implementation is based on:

1. the team-approved automated process and addenda in
   `docs/WI-00015-automated-process-addendum.md`;
2. the review decisions recorded on 2026-08-12;
3. the accepted dispositions in `docs/WI-00015-redline-suggestions.md`; and
4. WI-00015 revision 2.

The team-approved automated process intentionally changes some literal WI
behavior. The process addendum controls where the sources conflict.

## 3. Scope

### Included

- Correct the existing two-sensor runner.
- Add a separate one-sensor runner that requires a left/right declaration.
- Enforce declared shipping topology and required serial numbers.
- Gate Laser Calibration on live Ophir communication and configuration.
- Enforce energy-measurement quality and the approved adjustment algorithm.
- Target a two-sensor midpoint near 350 microjoules and allow at most three
  complete cross-checks.
- Make energy failure/NCR terminal and prohibit final persistence afterward.
- Implement Safety Calibration with averaged scaled-mA ADC reads, checked
  calculations, write/readback, restart, and a normal 30-second scan.
- Correct Measurement Calibration thresholds, calculations, side
  preservation, validation, and final persistence verification.
- Produce complete report evidence in place of application screenshots.
- Add automated unit tests using fakes/mocks.
- Create and visually verify a draft one-sensor DOCX with document-control
  metadata marked `TBD`.

### Excluded

- TestApp buttons, dialogs, and screens.
- Released document numbers, revisions, ECOs, approvals, or signatures.
- Firmware and hardware changes.
- Hard stored-state dependencies between independently invoked stages.

## 4. Architecture for scripts now and TestApp later

The operator-facing scripts own prompts, terminal display, and confirmation
of physical actions. Shared SDK workflow code:

- never calls `input()`;
- accepts declared topology and target side as explicit inputs;
- accepts injected meter, interface, power-cycle, scan, and reporting
  dependencies where practical;
- returns structured outcomes or typed failures;
- records structured observations and calculations; and
- independently checks all mutation prerequisites.

The future TestApp buttons will call this shared layer and present its
structured prompts/results. The current task does not add that UI logic.

## 5. Entry points and topology

### 5.1 Two-sensor guided runner

Add `scripts/wi15_dual_sensor_laser_calibration.py` as the two-sensor entry
point. It declares both `left` and `right` as required. It fails when either
module is absent or a required console/sensor serial is `None` or empty.

### 5.2 One-sensor guided runner

Add `scripts/wi15_single_sensor_laser_calibration.py`. It asks the operator to
choose `left` or `right` before mutation or laser activity, echoes the choice,
and requires confirmation. It accepts exactly the selected module and rejects
neither, both, or the opposite module.

A two-sensor unit cannot be executed as two one-sensor runs.

### 5.3 Stage-specific connections

- Laser Calibration requires the console, declared sensor topology, and live
  configured Ophir meter.
- Safety ADC calculation requires only the console. Its final normal scan
  uses the declared shipping sensor topology.
- Measurement Calibration requires the console and the one targeted module
  on the static phantom. Two sides run sequentially.
- Ophir is not required for Safety or Measurement Calibration.
- No beam-containment fixture is required by the process.

## 6. State and fail-closed gates

The state model records:

- workflow kind and expected sides;
- validated device and, when applicable, Ophir identities;
- pre-default, default, tuned, safety, calibrated, and post-restart
  configurations;
- raw observations, validity results, requested values, readbacks,
  calculations, and rounding;
- laser differential, tuning iterations, cross-check count and pairs;
- scan and safety-warning results;
- calibration outcomes by side/camera;
- report artifact paths; and
- terminal disposition (`passed`, `failed`, or `failed_ncr`) and reason.

A terminal energy failure prevents the safety/final configuration write even
when a lower-level phase is called directly. The guided runner has no
continue-anyway prompt. All failures return a nonzero process code.

The three stages ordinarily run in process order, but an independently
invoked stage does not require stored evidence that a previous stage passed.
It must still satisfy every gate local to that stage.

## 7. Laser Calibration

The shared preflight verifies topology, serials, Ophir COM/open/sensor/identity
operations, and all required meter settings. It executes before the default
configuration write or laser action.

The default write captures the previous configuration, replaces it with the
ten-key configuration in the process addendum, checks the operation, reads it
back, and verifies active operating values and 40 Hz triggering.

Every energy decision requires more than 25 valid pulses, standard deviation
below 40 microjoules, a 39-41 Hz rate, and finite statistics.

For two sensors, initial differential above 100 microjoules is an immediate
NCR. Otherwise the workflow uses the approved 50 mA downward-current or 10
microsecond upward-pulse-width controls to minimize midpoint distance from
350. It measures both sides after adjustment and permits at most three
complete cross-checks. Both must be within 300-400 inclusive.

For one sensor, the same direction-specific controls move the declared
module as close as practical to 350. Acceptance requires a final valid result
within 300-400 inclusive; no second-side cross-check runs.

The upward path temporarily uses 660-microsecond pulse-width limits and may
not exceed a 600-microsecond TA pulse. The downward path retains the existing
conservative current floor. Exhausting either bound without acceptance is an
NCR.

Before Safety Calibration, requested TA current and pulse width readbacks
must each be within plus or minus 2 percent.

After energy and readback acceptance, Laser Calibration writes the passing TA
values into the complete User Configuration and requires exact immediate
readback. Upward pulse-width tuning retains provisional 660-microsecond
limits; other paths retain the default 550 limits. Laser Calibration does not
power-cycle. Safety Calibration consumes this readback, calculates final
safety limits, and owns persistence verification. A terminal NCR cannot
perform the passing tuned-configuration write.

## 8. Safety Calibration

The workflow fires at the normal tuned point and collects at least ten valid
scaled-mA ADC readings from each safety controller while firing. It records
the sample sets and uses arithmetic means.

- `OPT_DRIVE_CL = nearest_integer(mean_OPT_mA * 1.3)`
- `EE_DRIVE_CL = nearest_integer(mean_EE_mA * 1.1)`
- for TA pulse width below 600, both pulse-width limits are
  `nearest_integer(TA_PULSE_WIDTH * 1.1)`;
- at TA pulse width 600, retain the 660 limits from upward tuning.

The complete configuration write and immediate readback are checked. After a
minimum 15-second power-off dwell, every intended key is checked again.

The final safety verification runs a normal 30-second sensor-data scan using
the declared shipping topology and persisted configuration, with no trigger,
laser, safety, or calibration override. Failure to complete or any laser
safety warning fails the stage.

## 9. Measurement Calibration

Each side runs separately on the static phantom. The 15-second calibration
scan computes per-camera mean, average-of-per-frame contrast, and dark mean.
Before any calibration write, every active camera must pass the versioned
factory gates in the process addendum.

Passing observations create `I_min = 0`, `I_max = 2 * mean`, `C_min = 0`, and
`C_max = average contrast`. Only the targeted side is updated; the non-target
side is preserved.

After checked write/readback, a 2-second validation scan requires every
active camera to pass BFI -0.5 through 0.5 and BVI 4.5 through 5.5,
inclusive. Results are never averaged across cameras for acceptance.

After all required sides pass, a minimum 15-second power-off dwell and
restart must prove persistence of the complete laser, safety, and calibration
configuration.

## 10. Reporting

One report renderer consumes structured stage records. It includes every
identity, setup, observation, calculation, threshold, write/readback,
cross-check, scan, calibration artifact, restart, and disposition item listed
in section 6 of the process addendum.

The default-versus-final comparison visibly highlights changes. The report
is the application-screenshot equivalence evidence. It never claims a later
step ran after a terminal failure.

## 11. Automated tests

Tests cover:

- valid and invalid one-/two-sensor topology and serial values;
- Ophir COM, scan, open, sensor-head, identity, setting, and success paths;
- proof that Ophir failure prevents default writes and laser action;
- default configuration write failures and mismatches;
- every energy-quality boundary, including exactly 25 pulses;
- two-sensor differential, target selection, cross-check pass, and failure
  after the third cross-check;
- one-sensor upward, downward, no-adjustment, and bound-failure paths;
- terminal NCR prevention of final writes;
- plus/minus 2 percent readback boundaries;
- ADC sample count, invalid values, means, multipliers, and rounding;
- safety configuration and pulse-width-limit rules;
- restart dwell/readback and normal-scan warnings;
- per-camera mean, contrast, dark, BFI, and BVI boundaries;
- failed pre-write calibration causing no write;
- sequential side update preserving prior calibration;
- final post-calibration persistence; and
- report contents and changed-value highlighting.

The existing default software suite must continue to pass. Hardware
execution remains a separate operator activity.

## 12. Documentation

- Create and maintain `scripts/WI15_PROCEDURES.md` as the operator-facing index
  for the four independent procedures.
- Retain `docs/WI-00015-automated-process-addendum.md` as the deterministic
  process record.
- Create `docs/WI-00015-Single-Sensor-DRAFT.docx`, visibly marked `DRAFT` and
  `NOT A RELEASED MANUFACTURING INSTRUCTION`, with document number, revision,
  ECO/change order, and approval fields set to `TBD`.
- Render and visually inspect every page of the draft DOCX.

## 13. Completion criteria

The work is complete when the automated process addendum is implemented, all
requested tests and the existing default suite pass, documentation is
consistent, the draft one-sensor DOCX passes visual inspection, terminal NCR
paths cannot write final configuration, and no TestApp UI logic has been
added.
