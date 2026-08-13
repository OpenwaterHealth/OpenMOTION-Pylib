# WI-00015 Single-Sensor Laser Calibration Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to
> implement this plan task-by-task, and use `superpowers:test-driven-development`
> for every behavior change.

**Goal:** Build a clean, standalone, fail-closed Single-Sensor Laser
Calibration procedure that validates exact topology and Ophir setup, writes
and verifies the WI default configuration, tunes one declared module toward
350 microjoules, saves the passing tuned configuration, and emits complete
JSON and human-readable report evidence.

**Architecture:** A thin interactive script owns operator prompts. A
UI-neutral workflow owns sequencing and acceptance. Pure domain helpers own
validation and calculations. Production hardware adapters own MotionInterface,
FPGA-register, and Ophir COM details. A report module consumes the structured
result. The clean procedure does not import or restore the old
`omotion.tuning` state machine. That prototype remains available only on the
historical `feature/214-wi15-tuning-runner` branch for reference while the four
procedures are rebuilt independently.

**Tech stack:** Python 3.12, dataclasses, enum, typing.Protocol, pathlib,
standard-library JSON/HTML, MotionInterface/MotionConsole, FpgaMap,
`apply_laser_power`, deferred pywin32 COM, pytest fakes/monkeypatch.

**Approved specification:**
`docs/superpowers/specs/2026-08-12-wi15-single-sensor-laser-calibration.md`

## Procedure boundary

This plan implements only Single-Sensor Laser Calibration. It does not
implement Safety Calibration, Measurement Calibration, the dual-sensor
midpoint/cross-check workflow, power cycling, or TestApp UI.

The procedure ends after a passing tuned User Configuration has been written
and read back immediately. Safety Calibration will later calculate final
safety limits and prove persistence across restart.

## Planned files

Create:

- `omotion/calibration/laser.py`
- `omotion/calibration/single_sensor_laser.py`
- `omotion/calibration/laser_hardware.py`
- `omotion/calibration/reporting.py`
- `scripts/wi15_single_sensor_laser_calibration.py`
- `scripts/WI15_PROCEDURES.md`
- `tests/test_wi15_laser_calibration.py`
- `tests/test_wi15_single_sensor_laser_calibration.py`
- `tests/test_wi15_laser_calibration_hardware.py`
- `tests/test_wi15_laser_calibration_report.py`
- `tests/test_wi15_single_sensor_laser_script.py`

Modify only for documentation/status:

- `docs/WI-00015-automated-process-addendum.md`
- `docs/superpowers/specs/2026-08-12-wi15-single-sensor-laser-calibration.md`
- `docs/superpowers/specs/2026-08-12-wi15-single-and-dual-runner-design.md`

Historical reference branch only; do not restore into this clean branch:

- `omotion/tuning.py`
- `scripts/wi15_runner.py`
- `scripts/wi15_guided.py`
- `scripts/wi15_calibration.py`
- untracked `wi15_out/` execution artifacts

## Task 1: Build the pure laser-calibration domain model

**Files:**

- Create: `tests/test_wi15_laser_calibration.py`
- Create: `omotion/calibration/laser.py`

### Step 1: Write failing tests for fixed constants and default config

Add unit tests asserting:

- target is 350 microjoules;
- acceptance is 300-400 inclusive;
- count must be strictly greater than 25;
- standard deviation must be strictly less than 40;
- rate is 39-41 inclusive;
- current step is 50 mA, floor is 2000 mA;
- pulse step is 10 microseconds, ceiling is 600 microseconds;
- temporary pulse limits are 660 microseconds; and
- `DEFAULT_USER_CONFIG` has exactly the ten approved keys with integer
  `TEC_TRIP: 40`.

Run:

```powershell
$env:PYTHONPATH=(Get-Location).Path
python -m pytest tests/test_wi15_laser_calibration.py -q
```

Expected: FAIL because `omotion.calibration.laser` does not exist.

### Step 2: Add minimal constants and immutable data classes

Implement:

- `SensorSide = Literal["left", "right"]`;
- `ProcedureStatus` enum: `passed`, `failed`, `failed_ncr`, `canceled`;
- `FailureKind` enum: `setup`, `configuration`, `measurement`, `ncr`,
  `canceled`;
- frozen `EnergyMeasurement` with `n`, `discarded`, `mean_uj`, `stdev_uj`,
  `rate_hz`, `min_uj`, `max_uj`, and `duration_s`;
- frozen `CriterionResult`;
- frozen `SettingReadback`;
- frozen `DeviceIdentity`, `OphirIdentity`, and `TopologySnapshot`; and
- exact constants/default configuration.

Keep the module independent of MotionInterface, COM, and file I/O.

### Step 3: Write failing validator tests

Cover:

- `validate_energy_measurement()` at 25/26 pulses;
- stdev at 39.999/40;
- rate at 38.999/39/41/41.001;
- every non-finite statistic;
- `validate_exact_single_topology()` for left/right success, neither, both,
  and wrong side;
- `validate_serial()` for `None`, empty, whitespace, and valid text;
- `percent_difference()` including zero requested value; and
- `within_percent()` at exactly plus/minus 2 percent and just outside.

Run the targeted file and confirm the new tests fail for missing helpers.

### Step 4: Implement the pure validators

Return structured criterion/failure information; do not print or raise for
ordinary pass/fail decisions. Use `math.isfinite` for every numerical gate.

The pulse-count criterion text and implementation must both say `> 25`, not
`>= 25`.

### Step 5: Add and test closest-setting selection

Implement a pure helper that accepts visited `(requested_setting,
measurement)` candidates and returns the valid candidate with minimum
absolute distance from 350. Define deterministic tie behavior as the lower
energy-driving setting:

- downward-current tuning: lower current wins a distance tie;
- upward-pulse tuning: lower pulse width wins a distance tie.

Test empty candidates, invalid candidates, normal closest selection, and
ties.

### Step 6: Verify and commit

```powershell
python -m pytest tests/test_wi15_laser_calibration.py -q
git diff --check
git add omotion/calibration/laser.py tests/test_wi15_laser_calibration.py
git commit -m "feat: add WI15 laser calibration domain model"
```

## Task 2: Define and test the UI-neutral single-sensor workflow shell

**Files:**

- Create: `tests/test_wi15_single_sensor_laser_calibration.py`
- Create: `omotion/calibration/single_sensor_laser.py`

### Step 1: Write fake-driven preflight tests

Create `FakeLaserBench` and `FakeRecorder`. The fake records every call in
order and returns queued measurements/readbacks.

Write failing tests proving:

- the request requires an explicitly confirmed `left` or `right` side;
- fixture confirmation must be true;
- exact topology is checked before any configuration write;
- console and selected-sensor serials are validated;
- Ophir preflight completes before any configuration write;
- a topology, serial, or Ophir failure produces a structured failed result;
- failed preflight performs no configuration write and no firing; and
- cleanup calls `stop_trigger()` on every exit.

### Step 2: Add request/result/event contracts and bench Protocol

Implement:

- `SingleSensorLaserCalibrationRequest` containing side, confirmations,
  operator/build/fixture/procedure metadata, and output/run identifier;
- `ProcedureEvent` with timestamp, stage, message, and structured data;
- `SingleSensorLaserCalibrationResult` with status, failure kind/reason,
  identities, configurations, measurements, adjustments, events, and report
  paths;
- `LaserCalibrationBench` Protocol with preflight/config/register/measure and
  guaranteed-stop methods; and
- `RunRecorder` Protocol with `record(event)` and `checkpoint(result)`.

The workflow takes dependencies in its constructor and never calls `input()`.

### Step 3: Implement only preflight sequencing

Implement `SingleSensorLaserCalibrationWorkflow.run(request)` far enough to:

1. validate confirmations;
2. call bench preflight;
3. validate topology and serials;
4. checkpoint structured failure or continue; and
5. stop the trigger in `finally`.

Use a typed internal `ProcedureFailure` to unwind expected failures into a
result; do not expose raw exceptions as successful outcomes.

### Step 4: Run tests and commit the workflow shell

```powershell
python -m pytest tests/test_wi15_single_sensor_laser_calibration.py -q
git add omotion/calibration/single_sensor_laser.py tests/test_wi15_single_sensor_laser_calibration.py
git commit -m "feat: add single-sensor laser workflow shell"
```

## Task 3: Implement checked default configuration and measurement gates

**Files:**

- Modify: `tests/test_wi15_single_sensor_laser_calibration.py`
- Modify: `omotion/calibration/single_sensor_laser.py`

### Step 1: Write failing default-configuration tests

Prove that the workflow:

- records the complete pre-existing configuration;
- writes exactly the approved default object;
- fails when the SDK write returns no result;
- fails on missing key, extra key, or mismatched value in immediate readback;
- does not fire after a failed write/readback;
- calls bring-up only after exact readback;
- verifies active TA current, TA pulse width, seed, and both pulse limits;
- accepts register quantization within 2 percent and rejects outside it; and
- enforces/corrects 40 Hz before firing.

Watch the tests fail before implementation.

### Step 2: Implement the checked default phase

Store all three objects in the result:

- `pre_existing_config`;
- `requested_default_config`; and
- `default_config_readback`.

The bench API returns checked operation details rather than relying on truthy
print output. Treat extra readback keys as a mismatch for the exact step-9
default write.

### Step 3: Write failing energy-validation tests

Queue one measurement at a time and prove:

- invalid pulse count, stdev, rate, or finite check fails immediately;
- an invalid observation is recorded but never used for tuning;
- `stop_trigger()` follows every measurement attempt; and
- a valid initial measurement advances to tuning.

### Step 4: Implement measurement validation and checkpointing

Every observation records all raw summary statistics and each criterion.
There is no acceptance-window override, development window, or continue
prompt.

### Step 5: Verify and commit

```powershell
python -m pytest tests/test_wi15_laser_calibration.py tests/test_wi15_single_sensor_laser_calibration.py -q
git diff --check
git add omotion/calibration/single_sensor_laser.py tests/test_wi15_single_sensor_laser_calibration.py
git commit -m "feat: enforce WI15 single-sensor preflight gates"
```

## Task 4: Implement closest-to-350 tuning and passing config handoff

**Files:**

- Modify: `tests/test_wi15_single_sensor_laser_calibration.py`
- Modify: `omotion/calibration/single_sensor_laser.py`

### Step 1: Write failing no-adjustment and final-verification tests

Test an initial 350 measurement followed by a distinct final verification.
Require:

- no setting write during tuning;
- a new final measurement;
- final 300 and 400 accepted, just outside rejected NCR; and
- requested TA current/pulse active readbacks within 2 percent.

### Step 2: Write failing downward-current tests

Queue deterministic measurements and assert:

- initial mean above 350 selects downward current only;
- writes occur in exact 50 mA decrements;
- every write is followed by checked readback and valid measurement;
- the closest adjacent measurement is reapplied if the prior setting wins;
- pulse width never changes;
- a closest in-range setting at the current floor can pass; and
- reaching the floor with every candidate outside 300-400 is NCR.

### Step 3: Implement downward tuning minimally

Keep all candidates and adjustment records. Stop after crossing 350 or at the
floor. Select via the tested pure helper, reapply when necessary, then perform
the separate final verification.

### Step 4: Write failing upward-pulse tests

Assert:

- initial mean below 350 first writes both temporary limits to 660;
- TA pulse writes occur in exact 10-microsecond increments;
- current never changes;
- closest adjacent candidate is selected/reapplied;
- 600 microseconds with energy below 300 is immediate NCR;
- a closest 300-400 result at 600 can pass; and
- no pulse write exceeds 600.

### Step 5: Implement upward tuning minimally

The temporary 660 limits are active-register writes only until the final
passing User Configuration handoff. On any failure, stop firing and record
NCR. Perform best-effort restoration of active default laser registers
without writing a passing config; record cleanup success/failure.

### Step 6: Write failing tuned-config handoff tests

For every successful branch prove:

- complete User Configuration is written only after final energy/readback
  acceptance;
- final TA current/pulse values are requested values, not quantized readback
  values;
- upward tuning persists provisional 660 pulse limits;
- downward/no-adjustment paths persist 550 pulse limits;
- write failure or exact complete-readback mismatch fails;
- no power-cycle method is called; and
- every NCR path has only the earlier default config write.

### Step 7: Implement the passing handoff and terminal status

The successful result contains the requested tuned object and immediate
readback. Mark `passed` only after exact readback. All bound/final-energy
failures are `failed_ncr`; setup/write/measurement failures are `failed`.

### Step 8: Verify and commit

```powershell
python -m pytest tests/test_wi15_laser_calibration.py tests/test_wi15_single_sensor_laser_calibration.py -q
git add omotion/calibration/single_sensor_laser.py tests/test_wi15_single_sensor_laser_calibration.py
git commit -m "feat: tune one WI15 sensor toward 350 uJ"
```

## Task 5: Build production Motion/Ophir hardware adapters

**Files:**

- Create: `tests/test_wi15_laser_calibration_hardware.py`
- Create: `omotion/calibration/laser_hardware.py`

### Step 1: Write failing MotionInterface adapter tests

Using fake interface/console/sensors, prove:

- startup waits for console plus one sensor and then checks exact topology;
- both, neither, and wrong side are reported accurately;
- serial/firmware/hardware identity reads are captured without substituting
  exception strings for valid serials;
- `close()` stops trigger then stops MotionInterface;
- configuration write requires non-`None` MotionConfig result and performs a
  fresh readback;
- trigger set requires a successful response and 40 Hz readback; and
- active register writes require truthy I2C result and return immediate
  readback.

### Step 2: Implement `FpgaRegisterIO` and Motion bench lifecycle

Reuse `FpgaMap` metadata, but do not import `ConsoleSession` from
`omotion.tuning`. Keep scaled/raw conversion in this adapter. Inject
`interface_factory`, clock/sleep, and map in tests.

Use one long-lived MotionInterface for console and selected sensor so there is
no inventory-session teardown/reconnect race.

### Step 3: Write failing Ophir adapter tests

Inject a fake COM object and test:

- COM construction failure;
- no USB serials;
- invalid/failed open handle;
- missing channel-0 energy sensor;
- identity/calibration-due failure;
- all five applicable COM setting getters/setters/readbacks;
- display averaging and graph mode recorded as `not_applicable` because the
  script streams samples directly;
- nonzero status samples discarded;
- fewer than two valid values returns finite-gate failure data; and
- close stops streams and closes devices.

### Step 4: Implement deferred `OphirEnergyMeter`

Import pywin32 only inside the default COM factory. Constructor/preflight must
not mutate console configuration. Make `measure()` return the domain
`EnergyMeasurement` and guarantee `StopStream` in `finally`.

### Step 5: Implement guaranteed firing wrapper

The Motion bench's `measure_energy()`:

1. verifies trigger frequency;
2. verifies TA pulse is below both active limits;
3. checks `start_trigger()` returned true;
4. invokes meter measurement; and
5. calls `stop_trigger()` in `finally`.

Test meter exceptions and start/stop failures.

### Step 6: Verify and commit

```powershell
python -m pytest tests/test_wi15_laser_calibration_hardware.py -q
git diff --check
git add omotion/calibration/laser_hardware.py tests/test_wi15_laser_calibration_hardware.py
git commit -m "feat: add Motion and Ophir WI15 adapters"
```

## Task 6: Add incremental JSON state and highlighted HTML report

**Files:**

- Create: `tests/test_wi15_laser_calibration_report.py`
- Create: `omotion/calibration/reporting.py`

### Step 1: Write failing serialization/checkpoint tests

Test that `JsonRunRecorder`:

- creates a unique procedure directory beneath the requested output root;
- serializes dataclasses, enums, tuples, non-finite values, and Path objects
  deterministically;
- writes a checkpoint after every recorded event;
- includes terminal failure data even when the workflow aborts; and
- does not overwrite an existing run directory.

Use `tmp_path`; never touch the worktree's existing `wi15_out/`.

### Step 2: Implement atomic checkpoint writes

Write a temporary sibling file and replace the JSON target only after a
complete serialization. The result owns all content; the recorder does not
derive acceptance independently.

### Step 3: Write failing HTML report tests

Assert that the report:

- HTML-escapes operator/device text;
- shows status and NCR reason prominently;
- includes identities, Ophir settings, every measurement/criterion,
  adjustments, final readback, and artifacts;
- presents default versus tuned configuration in a table;
- adds a `changed` CSS class only to keys whose value changed;
- displays no later-stage claim after terminal failure; and
- is generated for both pass and fail outcomes.

### Step 4: Implement a dependency-free HTML renderer

Use only the standard library. Link the raw JSON by relative filename. Do not
introduce reportlab as an SDK dependency. The report file is the
human-readable screenshot-equivalence evidence for this procedure.

### Step 5: Verify and commit

```powershell
python -m pytest tests/test_wi15_laser_calibration_report.py -q
git add omotion/calibration/reporting.py tests/test_wi15_laser_calibration_report.py
git commit -m "feat: report WI15 single-sensor laser calibration"
```

## Task 7: Add the thin operator script and supported-procedure index

**Files:**

- Create: `tests/test_wi15_single_sensor_laser_script.py`
- Create: `scripts/wi15_single_sensor_laser_calibration.py`
- Create: `scripts/WI15_PROCEDURES.md`
- Modify: `docs/WI-00015-redline-suggestions.md`

### Step 1: Write failing script tests

Load the script with `importlib.util` and monkeypatch its factories. Test:

- source decodes as ASCII so Windows cp1252 consoles are safe;
- side prompt accepts only `left` or `right`;
- selected side is echoed and separately confirmed;
- fixture placement is confirmed before workflow run;
- cancel/EOF exits nonzero without constructing hardware;
- required run metadata is collected through arguments or simple prompts;
- a structured passing result exits 0;
- failed/failed-NCR/canceled results exit nonzero;
- hardware/report cleanup occurs on exceptions; and
- the script never imports `omotion.tuning`.

### Step 2: Implement the CLI

Provide:

- `--output-dir` (default `./wi15_out`);
- `--operator`;
- `--build-revision`;
- `--fixture-id`;
- `--fixture-calibration-status`; and
- `--procedure-revision` defaulting to the approved addendum date/revision
  label.

If metadata arguments are omitted, prompt before hardware construction. Ask
the side and confirmations interactively as required by the spec. Do not
offer acceptance-window, threshold, dim-bench, skip, or continue-anyway
flags.

Construct the recorder, production bench/meter, workflow, and report renderer.
Print the terminal status and absolute report paths.

### Step 3: Create the procedure index

`scripts/WI15_PROCEDURES.md` lists the four approved procedures and marks only
Single-Sensor Laser Calibration as implemented in the new architecture. Note
that the old combined runner is prototype/reference code retained only on
`feature/214-wi15-tuning-runner`, not a supported procedure on this branch.

Update stale redline references to point to the process addendum/specs rather
than treating the prototype README as current execution authority.

### Step 4: Verify and commit

```powershell
python -m pytest tests/test_wi15_single_sensor_laser_script.py -q
git diff --check
git add scripts/wi15_single_sensor_laser_calibration.py scripts/WI15_PROCEDURES.md tests/test_wi15_single_sensor_laser_script.py docs/WI-00015-redline-suggestions.md
git commit -m "feat: add single-sensor WI15 laser script"
```

## Task 8: Run procedure-level integration tests and final review

**Files:**

- Modify as defects require: only the new files/tests from Tasks 1-7
- Modify status only after all checks pass:
  `docs/superpowers/specs/2026-08-12-wi15-single-sensor-laser-calibration.md`

### Step 1: Add an end-to-end fake-bench test

In `tests/test_wi15_single_sensor_laser_calibration.py`, run the real workflow,
recorder, and report renderer with a fake bench through:

- a passing downward-current path; and
- an upward ceiling NCR path.

Assert call order, JSON state, HTML report, final config handoff on pass, and
absence of that handoff on NCR.

Watch each new integration test fail before filling any missing behavior.

### Step 2: Run all WI15 tests

```powershell
$env:PYTHONPATH=(Get-Location).Path
python -m pytest `
  tests/test_wi15_laser_calibration.py `
  tests/test_wi15_single_sensor_laser_calibration.py `
  tests/test_wi15_laser_calibration_hardware.py `
  tests/test_wi15_laser_calibration_report.py `
  tests/test_wi15_single_sensor_laser_script.py -q
```

Expected: all pass, no hardware markers/skips.

### Step 3: Compile and inspect imports

```powershell
python -m py_compile `
  omotion/calibration/laser.py `
  omotion/calibration/single_sensor_laser.py `
  omotion/calibration/laser_hardware.py `
  omotion/calibration/reporting.py `
  scripts/wi15_single_sensor_laser_calibration.py
rg -n "omotion\.tuning|input\(" `
  omotion/calibration/laser.py `
  omotion/calibration/single_sensor_laser.py `
  omotion/calibration/laser_hardware.py `
  omotion/calibration/reporting.py
```

Expected: compile succeeds; no old-engine import or SDK-layer `input()`.

### Step 4: Run the full default suite

```powershell
python -m pytest
```

Expected: existing baseline remains green plus the new tests. Record exact
passed/skipped/deselected totals.

### Step 5: Review the complete diff

```powershell
git diff --check
git status --short
git diff --stat origin/feature/214-wi15-tuning-runner...HEAD
git diff origin/feature/214-wi15-tuning-runner...HEAD -- `
  omotion/calibration/laser.py `
  omotion/calibration/single_sensor_laser.py `
  omotion/calibration/laser_hardware.py `
  omotion/calibration/reporting.py `
  scripts/wi15_single_sensor_laser_calibration.py `
  tests/test_wi15_*.py
```

Check specifically:

- default configuration is exact;
- no final tuned config follows NCR;
- every firing path stops trigger;
- no development override exists;
- no power cycle or safety ADC work leaked into this procedure;
- report values come from the structured result, not recomputation; and
- `wi15_out/` remains untouched.

### Step 6: Mark the spec implemented and commit final verification fixes

Only after all checks pass, change the single-sensor spec status to
`Implemented and software-verified; hardware execution pending` and add a
short implementation mapping section listing the new module/script/tests.

```powershell
git add docs/superpowers/specs/2026-08-12-wi15-single-sensor-laser-calibration.md
git commit -m "docs: map single-sensor WI15 implementation"
```

Do not begin Dual-Sensor Laser Calibration, Safety Calibration, Measurement
Calibration, or TestApp integration in this plan.
