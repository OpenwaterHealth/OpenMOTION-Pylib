# WI-00015 Dual-Sensor Laser Calibration Implementation Design

**Date:** 2026-08-13

**Status:** Approved, implemented, and verified in software and on dual-sensor hardware

**Procedure:** Dual-Sensor Laser Calibration

## 1. Design decision

Implement the two-sensor procedure as a separate SDK workflow and CLI entry
point. Reuse the live-verified Motion, Ophir, configuration, recording, and
cleanup seams from the single-sensor implementation without converting the
single-sensor workflow into a generalized algorithm.

This isolates the more complex paired-measurement and cross-check state
machine from the released single-sensor behavior while retaining the same
fail-closed hardware rules.

The governing process requirements remain in:

- `docs/WI-00015-automated-process-addendum.md`; and
- `docs/superpowers/specs/2026-08-12-wi15-dual-sensor-laser-calibration.md`.

This document fixes the implementation architecture and operator interaction
contract. The procedure specification controls calibration values and
acceptance criteria if this design omits a detail.

## 2. Alternatives considered

### 2.1 Separate dual workflow — selected

Create a typed dual-sensor workflow with paired observations, tuning rounds,
and cross-check records. Reuse the stable lower-level adapters and recorder.

Benefits:

- preserves the live-verified single-sensor execution path;
- makes dual-only rules visible in types, tests, and reports;
- gives the future Test App a UI-neutral SDK API; and
- limits shared-code changes to hardware behavior that is genuinely common.

### 2.2 Generalize the single workflow into one topology engine — rejected

This would reduce some duplicated sequencing, but it would substantially
rewrite the live-verified path and force paired state into a workflow designed
around a single observation. The regression risk is not justified.

### 2.3 Put the algorithm in the CLI — rejected

This would be quick for a terminal-only prototype, but it would duplicate
business logic when Test App buttons are added. It would also weaken unit
testing, checkpoint recovery, and report completeness.

## 3. Proposed modules

### 3.1 Shared domain rules

`omotion/WI15LaserCalibration.py` remains the home of topology-independent,
pure rules such as energy-quality validation, exact configuration values,
percent tolerance, typed identities, and typed register readbacks.

Add only pure dual rules that have no orchestration dependency, including:

- exact dual-topology validation;
- paired measurement metric calculation; and
- dual final-energy acceptance.

### 3.2 Dual workflow

Add `omotion/WI15DualSensorLaserCalibration.py` containing:

- the immutable request and result models;
- placement-change acknowledgement records;
- paired measurement and midpoint metrics;
- tuning-round and setting-step evidence;
- complete cross-check evidence; and
- the fail-closed dual workflow.

The workflow accepts injected bench, recorder, and placement-change callback
dependencies. It never calls `input()` and does not contain Test App concepts.

### 3.3 Hardware adapter

Extend the hardware boundary narrowly so the workflow can declare exact
`left` plus `right` topology. Preserve the current single-side API and its
behavior. The dual path revalidates the exact two-sensor topology immediately
before configuration mutation and immediately before every firing operation,
matching the single-sensor safety posture.

The Ophir adapter remains common. Its preflight, fresh-session acquisition,
valid-sample collection, bounded duration, and stream cleanup rules are
unchanged.

### 3.4 Recorder and report

Reuse `JsonRunRecorder` and its atomic checkpoint behavior. Add a
dual-specific HTML renderer rather than expanding the single-sensor report
with many conditional paired sections.

The renderer consumes supplied workflow evidence verbatim. It does not
recalculate acceptance and does not claim that an unreached phase ran.

### 3.5 CLI entry point

Add `scripts/wi15_dual_sensor_laser_calibration.py`. It owns terminal prompts,
argument parsing, dependency construction, artifact finalization, cleanup,
and exit codes. It delegates all calibration decisions to the SDK workflow.

## 4. Operator placement contract

The workflow tracks the side most recently acknowledged as seated in the
Ophir 0 cm fixture.

1. Before the first left measurement, request acknowledgement that the left
   sensor is seated.
2. Before a right measurement, request acknowledgement only if the tracked
   side is not already right.
3. Before a left measurement, request acknowledgement only if the tracked
   side is not already left.
4. Keep the selected sensor seated during consecutive steps of a tuning
   sweep. Do not repeat the acknowledgement for every acquisition.
5. Record the side and sensor serial number on every measurement, whether or
   not that acquisition required a placement change.
6. A declined, malformed, or canceled acknowledgement fails before firing or
   acquiring the associated observation.

The CLI acknowledgement should be courteous and precise, for example:

> Please place the left sensor module (serial 12345) in the Ophir 0 cm
> fixture. Confirm when it is securely seated [y/N]:

The callback boundary permits the future Test App to present the same request
without changing workflow rules.

## 5. End-to-end workflow

### 5.1 Setup and preflight

1. Create the run recorder before hardware work so setup failures remain
   auditable.
2. Require a responsive console and exactly one left plus one right sensor.
3. Require nonempty console, left, and right serial numbers.
4. Capture device and runtime SDK identities.
5. Fully preflight and configure the Ophir meter.
6. Revalidate topology before any configuration write or firing.

Any failure stops before default mutation or laser action where applicable.
The dual script never falls back to single-sensor operation.

### 5.2 Default configuration

1. Capture and checkpoint the complete pre-existing User Configuration.
2. Write the approved ten-key default configuration.
3. Treat the immediate write return and complete immediate readback as
   authoritative evidence.
4. Bring up the active laser settings and verify current, pulse width, seed,
   and 40 Hz triggering.

### 5.3 Initial pair

1. Acknowledge and measure the left sensor.
2. Acknowledge the change and measure the right sensor.
3. Validate each observation before using its mean.
4. Calculate and record absolute differential, midpoint, midpoint distance,
   and signed offsets.
5. A differential greater than 100 microjoules is an immediate NCR. Exactly
   100 microjoules remains eligible to continue.

The initial pair is baseline evidence and does not consume a cross-check.

### 5.4 Midpoint tuning

For each round, use the latest complete valid pair:

- Above 350 microjoules, select the higher-reading side, keep pulse width
  fixed, and step current downward by 50 mA toward the calculated target.
- Below 350 microjoules, select the lower-reading side, verify temporary 660
  microsecond upper limits, keep current fixed, and step pulse width upward by
  10 microseconds toward the calculated target.
- At 350 microjoules, or when the current discrete setting is already the
  closest permitted setting, make no adjustment.

If the selected side differs from the currently seated side, request one
placement change before the sweep. Consecutive measurements in that sweep do
not prompt again. Every requested setting, immediate typed readback, raw
observation, candidate, target, and selection is checkpointed before the next
operation.

Adjustment bounds and target-straddling selection follow the detailed
procedure specification. A terminal adjustment-bound condition is an NCR.

### 5.5 Complete cross-checks

After each adjustment decision:

1. ensure the selected setting is active and verified;
2. obtain a valid left observation;
3. obtain a valid right observation;
4. increment the cross-check number only after the complete valid pair exists;
5. record all pair metrics; and
6. pass immediately when both readings are within 300–400 microjoules,
   inclusive.

If either side is outside the range, use that latest pair for the next round.
After three complete nonpassing cross-checks, fail with an NCR. Invalid or
partial observations fail execution and never consume a complete
cross-check.

### 5.6 Final verification and write

After a passing pair:

1. require typed active current and pulse-width readbacks;
2. require each to be within ±2 percent of the requested value;
3. build the complete tuned User Configuration;
4. write it and require exact immediate complete readback; and
5. record that readback as the authoritative Safety Calibration handoff.

No final tuned configuration write may occur after an NCR or another terminal
failure.

### 5.7 Cleanup and artifacts

Always stop triggering, close the Ophir session, close the Motion session,
checkpoint the terminal result, and attempt both JSON and HTML finalization.
Cleanup and report failures remain visible and cannot leave a stale passing
artifact.

## 6. Auditor-facing language

Every visible step label explains the phase and purpose. The following style
is normative:

- `Initial paired measurement — left sensor`
- `Initial paired measurement — right sensor`
- `Initial differential gate — accepted at 100 µJ or below`
- `Midpoint adjustment round 1 — selected right sensor because it had the lower energy reading`
- `Adjustment step 2 — increased pulse width from 410 µs to 420 µs`
- `Cross-check 1 — left sensor verification`
- `Cross-check 1 — paired result: both sensors within the approved 300–400 µJ range`
- `Final configuration verification — active current within ±2% of the requested value`

Labels must not rely on method names such as `_measure_once`, terse codes such
as `adj_2`, or unexplained register names. Register names remain present as
technical evidence alongside a plain-language explanation.

The JSON retains stable machine-readable event kinds plus the full
human-readable label. The HTML report uses the human-readable label as the
primary description.

## 7. Failure semantics

The result distinguishes procedural failure from NCR while both return a
nonzero CLI exit code. The exact failure reason and last completed evidence
remain checkpointed.

NCR conditions include:

- initial differential greater than 100 microjoules;
- unreachable approved adjustment bound; and
- both-sensor acceptance not achieved after three complete cross-checks.

Connection, identity, acknowledgement, measurement-quality, write/readback,
and cleanup failures are procedural failures unless the governing procedure
explicitly classifies them as NCR.

There is no continue-anyway path.

## 8. Test strategy

Development follows strict red-green-refactor cycles. Fake hardware tests
cover:

- exact dual topology and all serial gates;
- proof that failed preflight performs no mutation or firing;
- placement prompts only when the required side changes;
- no repeated prompt across same-side tuning steps;
- side and serial attribution for every measurement;
- declined/canceled acknowledgement preventing the next firing;
- measurement-quality boundaries;
- differential 100 accepted and values above 100 NCR;
- both tuning directions and selected-side reasoning;
- straddling, closest-setting, no-adjustment, and bound behavior;
- one, two, and three complete cross-check outcomes;
- invalid partial pairs not consuming a cross-check;
- inclusive final energy and ±2 percent boundaries;
- authoritative immediate readbacks and no final write after failure;
- durable checkpoints after each irreversible observation or mutation;
- cleanup after every failure position;
- auditor-facing labels in JSON, terminal output, and HTML; and
- script exit codes and final artifact failure behavior.

The exact WI-00015 suite and the repository hardware-independent suite must
remain green. A successful live run requires both physical sensor modules and
is a separate operator activity after fake-driven verification.

## 9. Completion criteria

Implementation is ready for operator review when:

- the dual CLI and SDK workflow implement the approved procedure;
- the single-sensor behavior remains unchanged;
- all required fake-driven and regression tests pass;
- JSON and HTML provide complete, auditor-readable evidence;
- static checks and forbidden-dependency scans pass;
- the branch is clean and pushed to the existing review PR; and
- hardware validation is explicitly identified as pending until both sensors
  are available in the required fixture sequence.
