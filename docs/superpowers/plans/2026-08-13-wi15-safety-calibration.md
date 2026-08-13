# WI-00015 Safety Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone, fail-closed WI-00015 Safety Calibration SDK workflow and operator script that calculates safety limits from scaled console ADC samples, proves persistence across a measured power cycle, and completes a normal 30-second scan without safety warnings or overrides.

**Architecture:** Add separate pure-domain, workflow, hardware-adapter, report, and CLI modules. Reuse stable WI15 evidence, recorder, FPGA access, and normal `ScanWorkflow` APIs without changing the existing single- or dual-sensor laser scripts. The workflow owns all acceptance decisions; injected adapters own Motion I/O, manual or automated power cycling, and the normal scan.

**Tech Stack:** Python 3.13, frozen dataclasses, `MotionInterface`, `FpgaRegisterIO`, `ScanWorkflow`, `ConsoleTelemetryPoller`, pytest, Ruff, and atomic JSON/HTML evidence.

## Global Constraints

- Safety ADC acquisition requires the console only and must not construct or preflight Ophir.
- The console must be responsive with a nonblank serial before configuration or firing.
- Use engineering-unit mA from `OPT_ADC_DATA` and `EE_ADC_DATA`, never raw counts.
- Collect at least 10 valid samples from each controller during one active firing period; reject booleans, missing values, exceptions, NaN, and infinity.
- Calculate `OPT_DRIVE_CL = nearest_integer(opt_mean_mA * 1.3)` and `EE_DRIVE_CL = nearest_integer(ee_mean_mA * 1.1)` with one explicit half-up rule for nonnegative values.
- Below 600 us, both pulse limits are `nearest_integer(TA_PULSE_WIDTH * 1.1)`; at 600 us both must remain 660; above 600 us fails.
- Write and exactly read back the complete intended User Configuration.
- Restart proof requires observed console disconnect, at least 15 measured seconds off, observed reconnect, and exact complete post-restart configuration equality.
- The final scan uses the declared shipping topology for 30 seconds through ordinary `ScanWorkflow`, with `trigger_config=None`, `disable_laser=False`, and no calibration or safety override.
- Passing requires at least one known safety telemetry observation and no observed safety fault or warning.
- Every failure prints and records its exact gate and does not offer a continue-anyway path.
- Existing single- and dual-sensor laser scripts and their tests are owned by the parallel cleanup branch and are not modified here.

---

### Task 1: Add pure safety-calculation rules and immutable evidence

**Files:**

- Create: `omotion/calibration/safety.py`
- Create: `tests/test_wi15_safety_calibration.py`

**Interfaces:**

- Consumes: `CriterionResult`, `DeviceIdentity`, `FinalSettingCheck`, `ProcedureStatus`, `FailureKind`, `SettingReadback`, and `TopologySnapshot` from `omotion.calibration.laser`.
- Produces: `ShippingTopology`, `AdcReadEvidence`, `SafetyLimitCalculation`, `PulseLimitCalculation`, `PowerCycleEvidence`, `SafetyWarningEvidence`, `NormalScanEvidence`, `nearest_integer_half_up()`, `validate_current_configuration()`, `calculate_safety_limit()`, `calculate_pulse_limits()`, and `validate_shipping_topology()`.

- [ ] **Step 1: Write failing nearest-integer and safety-limit tests**

```python
@pytest.mark.parametrize(("value", "expected"), [(10.49, 10), (10.5, 11), (10.51, 11)])
def test_nearest_integer_uses_explicit_half_up_rule(value, expected):
    assert nearest_integer_half_up(value) == expected


def test_safety_limit_records_every_calculation_input():
    result = calculate_safety_limit("SAFETY_OPT", (100.0, 102.0), 1.3)
    assert result.mean_ma == 101.0
    assert result.unrounded_limit_ma == 131.3
    assert result.rounded_limit_ma == 131
    assert result.rounding_rule == "nearest integer; exact halves round upward"
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `python -m pytest tests/test_wi15_safety_calibration.py -q`

Expected: import failure because `omotion.calibration.safety` does not exist.

- [ ] **Step 3: Implement the pure helpers and frozen records**

Use finite nonnegative validation and an explicit rule:

```python
def nearest_integer_half_up(value: float) -> int:
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError("value must be finite and nonnegative")
    return math.floor(numeric + 0.5)
```

`calculate_safety_limit()` rejects empty/nonfinite samples. `calculate_pulse_limits()` requires an integer-valued positive pulse width, applies the below-600 calculation, preserves exactly 660 at 600, and rejects values above 600.

- [ ] **Step 4: Add configuration and topology boundary tests**

Cover missing keys, booleans, nonfinite/nonpositive TA settings, below/exactly/above 600 us, single-left, single-right, dual topology, nonblank required sensor serials, and unexpected extra connected sides.

- [ ] **Step 5: Run domain and adjacent laser-domain tests GREEN**

Run: `python -m pytest tests/test_wi15_safety_calibration.py tests/test_wi15_laser_calibration.py -q`

- [ ] **Step 6: Commit the domain increment**

```powershell
git add omotion/calibration/safety.py tests/test_wi15_safety_calibration.py
git commit -m "feat: add WI15 safety calibration rules"
```

---

### Task 2: Implement local preflight and active-setting verification

**Files:**

- Create: `omotion/calibration/safety_workflow.py`
- Create: `tests/test_wi15_safety_calibration_workflow.py`

**Interfaces:**

- Consumes: Task 1 records/helpers, existing `ProcedureEvent`, `JsonRunRecorder` checkpoint shape, and injected `SafetyCalibrationBench` protocol.
- Produces: `SafetyCalibrationRequest`, `ConsolePreflightSnapshot`, `SafetyCalibrationResult`, `SafetyCalibrationBench`, and `SafetyCalibrationWorkflow.run()`.

- [ ] **Step 1: Write failing setup-gate tests**

```python
@pytest.mark.parametrize("preflight", [disconnected_console(), blank_console_serial(), unresponsive_console()])
def test_setup_failure_prevents_bringup_adc_write_and_scan(preflight):
    result, bench = run_workflow(preflight=preflight)
    assert result.status is ProcedureStatus.FAILED
    assert result.failure_kind is FailureKind.SETUP
    assert bench.mutations == []
    assert bench.trigger_starts == 0
    assert bench.scan_requests == []
```

- [ ] **Step 2: Run focused tests RED**

Run: `python -m pytest tests/test_wi15_safety_calibration_workflow.py -k preflight -q`

- [ ] **Step 3: Implement immutable request/result and setup flow**

The request contains operator/build/fixture/procedure/run metadata plus declared topology. The result carries timestamps, SDK version, console identity, topology evidence, every later stage record, terminal reason, report paths, and cleanup diagnostics. Deep-freeze all mapping evidence.

- [ ] **Step 4: Write failing current-config, ±2%, and trigger tests**

Prove the complete current config is checkpointed before validation, active current/pulse exact ±2% boundaries are inclusive, only trigger frequency may be corrected to 40 Hz, and malformed immediate trigger write evidence fails before ADC firing.

- [ ] **Step 5: Implement checked bring-up and readback verification**

Read the full User Configuration, validate required keys, call normal persisted laser bring-up without overrides, record workflow-owned `FinalSettingCheck` values for TA current/pulse, and correct/verify trigger frequency exactly as the laser workflows do.

- [ ] **Step 6: Run focused and adjacent workflow tests GREEN, then commit**

```powershell
python -m pytest tests/test_wi15_safety_calibration.py tests/test_wi15_safety_calibration_workflow.py tests/test_wi15_single_sensor_laser_calibration.py tests/test_wi15_dual_sensor_laser_calibration.py -q
git add omotion/calibration/safety_workflow.py tests/test_wi15_safety_calibration_workflow.py
git commit -m "feat: preflight WI15 safety calibration"
```

---

### Task 3: Add ADC acquisition, warning capture, and threshold calculations

**Files:**

- Modify: `omotion/calibration/safety_workflow.py`
- Modify: `tests/test_wi15_safety_calibration_workflow.py`

**Interfaces:**

- Consumes: `SafetyCalibrationBench.start_trigger()`, `read_adc_ma(controller)`, `read_safety_warning()`, and `stop_trigger()`.
- Produces: checkpointed `AdcReadEvidence` sequences and both `SafetyLimitCalculation` records.

- [ ] **Step 1: Write failing ADC sample tests**

Cover exactly 10 valid reads per controller, interleaving, extra attempts after rejected exceptions/None/bool/NaN/infinity, fewer than 10 after the bounded attempt budget, and proof that every accepted sample is scaled mA supplied by the adapter.

- [ ] **Step 2: Verify RED, then implement bounded interleaved acquisition**

Use an injected policy with `minimum_valid_samples=10`, `maximum_attempts_per_controller=30`, and `interval_s=0.05`. Start the trigger once, interleave OPT/EE reads, checkpoint each accepted/rejected outcome immediately, poll safety state during the firing window, and always stop in `finally`.

- [ ] **Step 3: Write cleanup and warning regressions**

```python
def test_adc_exception_still_stops_trigger_and_preserves_partial_evidence(): ...
def test_adc_stop_failure_cannot_return_passed(): ...
def test_known_safety_fault_during_adc_firing_fails_with_decoded_faults(): ...
```

- [ ] **Step 4: Calculate and checkpoint thresholds only after both sample sets pass**

Use Task 1 helpers with multipliers 1.3 and 1.1. No intended configuration exists before both calculations succeed.

- [ ] **Step 5: Run focused tests GREEN and commit**

```powershell
python -m pytest tests/test_wi15_safety_calibration_workflow.py -q
git add omotion/calibration/safety_workflow.py tests/test_wi15_safety_calibration_workflow.py
git commit -m "feat: sample WI15 safety ADC limits"
```

---

### Task 4: Add exact configuration handoff and restart persistence proof

**Files:**

- Modify: `omotion/calibration/safety_workflow.py`
- Modify: `tests/test_wi15_safety_calibration_workflow.py`

**Interfaces:**

- Consumes: `write_user_configuration()`, `power_cycle(minimum_off_s=15.0)`, and `read_user_configuration()` after reconnect.
- Produces: intended/immediate/post-restart configurations plus `PowerCycleEvidence` and exact comparison results.

- [ ] **Step 1: Write failing complete-write/readback tests**

Prove only `OPT_DRIVE_CL`, `EE_DRIVE_CL`, `OPT_PULSE_WIDTH_UL`, and `EE_PULSE_WIDTH_UL` change; all other keys and permitted extras remain identical. Reject missing/extra/mismatched immediate readback and never request a power cycle after write failure.

- [ ] **Step 2: Implement exact intended configuration and immediate verification**

Build from the full current mapping, update four derived keys, checkpoint the request, require a mapping returned by the checked write, checkpoint it, then compare exact complete mappings.

- [ ] **Step 3: Write failing restart-proof tests**

Cover no observed disconnect, dwell `14.999`, no reconnect, console serial changing, missing restart evidence, read failure, and every-key post-restart mismatch. Exact 15.0 seconds passes.

- [ ] **Step 4: Implement power-cycle and persistence gates**

Require `PowerCycleEvidence(disconnect_observed=True, off_duration_s>=15.0, reconnect_observed=True, ...)`, revalidate the same console serial, reread the complete config, and require exact equality with the intended mapping.

- [ ] **Step 5: Run focused tests GREEN and commit**

```powershell
python -m pytest tests/test_wi15_safety_calibration_workflow.py -q
git add omotion/calibration/safety_workflow.py tests/test_wi15_safety_calibration_workflow.py
git commit -m "feat: persist WI15 safety limits"
```

---

### Task 5: Add declared-topology normal scan acceptance

**Files:**

- Modify: `omotion/calibration/safety_workflow.py`
- Modify: `tests/test_wi15_safety_calibration_workflow.py`

**Interfaces:**

- Consumes: `run_normal_scan(declared_topology, duration_s=30.0)`.
- Produces: final topology/identities, exact no-override scan request evidence, warnings, duration, outcome, and terminal disposition.

- [ ] **Step 1: Write failing topology and no-override tests**

Cover exact single-left, single-right, and dual topology; required nonblank serials; unexpected opposite side; disconnect during scan; and proof that the adapter receives no trigger, laser, safety, or calibration override.

- [ ] **Step 2: Write failing scan-result tests**

Cover refused start, async error, cancellation, duration below 30, no known safety telemetry, known warning/fault, interlock prevention, successful 30-second completion, and guaranteed scan/trigger cleanup.

- [ ] **Step 3: Implement the final scan gate**

The workflow passes only after a complete `NormalScanEvidence` has exact topology, duration at least 30 seconds, `completed=True`, `safety_state_known=True`, empty warnings/faults, and `overrides=mappingproxy({})`.

- [ ] **Step 4: Run workflow suite GREEN and commit**

```powershell
python -m pytest tests/test_wi15_safety_calibration_workflow.py -q
git add omotion/calibration/safety_workflow.py tests/test_wi15_safety_calibration_workflow.py
git commit -m "feat: verify WI15 normal safety scan"
```

---

### Task 6: Implement the production Motion safety adapter

**Files:**

- Create: `omotion/calibration/safety_hardware.py`
- Create: `tests/test_wi15_safety_calibration_hardware.py`
- Reuse without semantic changes: `omotion/calibration/laser_hardware.py`, `omotion/ScanWorkflow.py`, and `omotion/ConsoleTelemetry.py`

**Interfaces:**

- Consumes: `MotionInterface`, `MotionConfig`, `FpgaRegisterIO`, `ScanRequest`, console telemetry listeners, and an injected `PowerCycleCoordinator`.
- Produces: `MotionSafetyCalibrationBench` implementing the workflow protocol.

- [ ] **Step 1: Write failing console and scaled-ADC adapter tests**

Prove startup waits only for console during ADC calibration, identity fields remain independent, responsive echo is required, `OPT_ADC_DATA`/`EE_ADC_DATA` return scaled floats, config write returns a fresh complete readback, and no Ophir symbol or constructor is used.

- [ ] **Step 2: Implement console/config/register boundaries**

Reuse `FpgaRegisterIO`; expose controller names at the workflow boundary while mapping them internally to `OPT_ADC_DATA` and `EE_ADC_DATA`. Preserve authoritative immediate return/readback contracts.

- [ ] **Step 3: Write failing power-cycle integration tests**

Use fake clocks/coordinators to prove the adapter stops trigger/interface activity, observes disconnection, enforces the full 15-second monotonic dwell, waits for reconnect, rebinds console state, and records timestamps/serial evidence.

- [ ] **Step 4: Write failing ordinary-scan tests**

Assert the exact `ScanRequest` has duration 30, mask `0xFF` only for declared sides, `disable_laser=False`, `trigger_config=None`, and no realtime calibration call. Feed telemetry callbacks to test known-clear, unknown, and decoded fault states.

- [ ] **Step 5: Implement normal scan and cleanup**

Subscribe before `start_scan`, await completion with a bounded pad, measure monotonic duration, inspect `last_scan_error`/`last_scan_canceled`, revalidate topology after completion, remove the listener, and always call cancel/stop cleanup best-effort.

- [ ] **Step 6: Run hardware fakes and regressions GREEN, then commit**

```powershell
python -m pytest tests/test_wi15_safety_calibration_hardware.py tests/test_wi15_laser_calibration_hardware.py -q
python -m ruff check omotion/calibration/safety_hardware.py tests/test_wi15_safety_calibration_hardware.py
git add omotion/calibration/safety_hardware.py tests/test_wi15_safety_calibration_hardware.py
git commit -m "feat: adapt Motion for WI15 safety calibration"
```

---

### Task 7: Add the auditor-readable safety report

**Files:**

- Create: `omotion/calibration/safety_report.py`
- Create: `tests/test_wi15_safety_calibration_report.py`

**Interfaces:**

- Consumes: workflow-owned request/result and inherited atomic helpers from `HtmlRunReport`.
- Produces: `SafetyCalibrationHtmlRunReport`.

- [ ] **Step 1: Write failing report tests**

Require request/runtime metadata, console/final-scan identities, all accepted/rejected ADC reads, both means/multipliers/unrounded/rounded values and tie rule, pulse calculation, current/intended/immediate/post-restart full configs, changed-value highlighting, power-cycle timestamps/dwell/restart proof, no-override scan request, warnings, terminal reason, JSON link, and cleanup diagnostics.

- [ ] **Step 2: Add evidence-gating tests**

An early setup failure must not show ADC/write/restart/scan headings. ADC failure shows partial raw reads but no calculated or later phases. Write/restart failure must label the intended config requested/unconfirmed and omit the scan.

- [ ] **Step 3: Implement renderer without recalculation**

Subclass `HtmlRunReport` only for escaping/table/atomic-write helpers. Render every supplied value verbatim and gate sections on supplied evidence. Never derive a pass/fail, mean, limit, dwell, or topology in the report.

- [ ] **Step 4: Run report suites GREEN and commit**

```powershell
python -m pytest tests/test_wi15_safety_calibration_report.py tests/test_wi15_laser_calibration_report.py tests/test_wi15_dual_sensor_laser_calibration_report.py -q
git add omotion/calibration/safety_report.py tests/test_wi15_safety_calibration_report.py
git commit -m "feat: report WI15 safety calibration"
```

---

### Task 8: Add the safety operator CLI and manual power-cycle coordinator

**Files:**

- Create: `scripts/wi15_safety_calibration.py`
- Create: `tests/test_wi15_safety_calibration_script.py`
- Modify: `scripts/WI15_PROCEDURES.md`

**Interfaces:**

- Consumes: Task 2 workflow, Task 6 bench, Task 7 report, and `JsonRunRecorder`.
- Produces: `main(argv, input_func, output_func) -> int` plus an operator-confirmed power-cycle coordinator.

- [ ] **Step 1: Write failing CLI tests**

Cover required metadata and explicit `single-left`/`single-right`/`dual` declaration before hardware construction, manual power-off prompt, observed disconnect before dwell begins, no power-on prompt before 15 measured seconds, reconnect handling, terminal category/reason output, report failure replacing a prior pass, cleanup errors, one run ID, and all exit codes.

- [ ] **Step 2: Implement the thin ASCII-safe CLI**

The CLI constructs dependencies and owns `input()`. Its power coordinator gives courteous instructions, observes actual disconnection/reconnection through the bench, and returns timestamps/evidence; it never asserts success from acknowledgement alone. Workflow logic and calculations remain outside the script.

- [ ] **Step 3: Finalize JSON/HTML before claiming terminal success**

Use the hardened dual-script artifact pattern: checkpoint incomplete report state, atomically write HTML, verify the claimed path exists, checkpoint finalized state, print exact failure category/reason, then return status-derived exit code. Resource cleanup failures replace a pending pass.

- [ ] **Step 4: Update the supported-procedure index and commit**

```powershell
python -m pytest tests/test_wi15_safety_calibration_script.py -q
python -m py_compile scripts/wi15_safety_calibration.py
python -m ruff check scripts/wi15_safety_calibration.py tests/test_wi15_safety_calibration_script.py
git add scripts/wi15_safety_calibration.py tests/test_wi15_safety_calibration_script.py scripts/WI15_PROCEDURES.md
git commit -m "feat: add WI15 safety calibration script"
```

---

### Task 9: Integrate, review, and publish software verification

**Files:**

- Modify: `docs/superpowers/specs/2026-08-12-wi15-safety-calibration.md`
- Verify: all new safety files and existing WI15 single/dual files

**Interfaces:**

- Consumes: Tasks 1-8 and the user-provided cleanup commit when ready.
- Produces: mapped specification, fresh verification evidence, clean branch, and stacked review PR.

- [ ] **Step 1: Run the exact WI15 matrix**

```powershell
python -m pytest tests/test_wi15_laser_calibration.py tests/test_wi15_single_sensor_laser_calibration.py tests/test_wi15_dual_sensor_laser_calibration.py tests/test_wi15_safety_calibration.py tests/test_wi15_safety_calibration_workflow.py tests/test_wi15_laser_calibration_hardware.py tests/test_wi15_safety_calibration_hardware.py tests/test_wi15_laser_calibration_report.py tests/test_wi15_dual_sensor_laser_calibration_report.py tests/test_wi15_safety_calibration_report.py tests/test_wi15_single_sensor_laser_script.py tests/test_wi15_dual_sensor_laser_script.py tests/test_wi15_safety_calibration_script.py -q
```

- [ ] **Step 2: Run compile, Ruff, forbidden-dependency, and hardware-independent gates**

Prove the workflow/report contain no `input(` and the safety modules contain no Ophir import/reference. Then run:

```powershell
python -m pytest -m "not fpga and not imu and not console and not sensor and not slow and not sequence and not destructive"
git diff --check
```

- [ ] **Step 3: Integrate the cleanup branch without overwriting user work**

Inspect the user-provided commit, cherry-pick it only after confirming its paths, resolve integration at the new safety CLI/helper boundary, and rerun the exact matrix. Do not modify or discard unrelated cleanup changes.

- [ ] **Step 4: Map implementation and mark software-only status**

Record modules, tests, exact totals, and that live Safety Calibration remains pending until an operator is ready for derived-limit writes, a real 15-second power cycle, and a 30-second normal scan.

- [ ] **Step 5: Push and open a stacked PR**

Push `feature/214-wi15-safety-calibration` and open it against `feature/214-wi15-calibration-procedures` while PR #232 is unmerged. After #232 merges, retarget/rebase onto `next` before final merge.
