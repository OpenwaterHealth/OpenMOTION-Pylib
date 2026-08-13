# WI-00015 Dual-Sensor Laser Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Use
> `superpowers:test-driven-development` for every production behavior change.

**Goal:** Build the standalone dual-sensor WI-00015 laser procedure that
validates a console plus left and right modules, gates on Ophir readiness,
rejects an excessive initial differential, tunes the paired midpoint, permits
at most three complete cross-checks, and produces auditor-readable JSON and
HTML evidence.

**Architecture:** Add a separate immutable dual workflow and dual report while
reusing the live-verified domain, Motion/Ophir adapter, recorder, and cleanup
seams. An injected placement callback bridges the UI-neutral workflow to a
thin CLI and prompts only when the required sensor side changes. Preserve the
single-sensor workflow and its public behavior.

**Tech Stack:** Python 3.12, frozen dataclasses, enum, `typing.Protocol`,
standard-library JSON/HTML, MotionInterface/MotionConsole, Ophir COM through
the existing deferred adapter, pytest fakes/monkeypatch, Ruff, PowerShell.

## Global Constraints

- Governing process: `docs/WI-00015-automated-process-addendum.md` and
  `docs/superpowers/specs/2026-08-12-wi15-dual-sensor-laser-calibration.md`.
- Implementation design:
  `docs/superpowers/specs/2026-08-13-wi15-dual-sensor-laser-calibration-design.md`.
- Require exactly a console, left sensor, and right sensor; all three serials
  must be non-`None`, nonempty text.
- Complete Ophir preflight and exact topology validation occur before default
  configuration mutation or firing.
- Initial absolute left/right differential greater than 100 microjoules is a
  terminal NCR; exactly 100 may continue.
- Every usable observation has more than 25 valid samples, standard deviation
  below 40 microjoules, rate 39-41 Hz inclusive, and finite statistics.
- Both final readings must be 300-400 microjoules inclusive.
- Use 50 mA downward-current and 10 microsecond upward-pulse-width steps; do
  not go below 2000 mA or above 600 microseconds.
- Permit at most three complete post-adjustment left/right cross-checks.
- Prompt before the first placement and thereafter only when the required side
  differs from the side currently acknowledged as seated.
- Record sensor side and serial on every observation, even when no new prompt
  is needed.
- Operator and report labels state phase, sensor side, action/reason, and
  result in plain audit language.
- No NCR or other failure may write the passing tuned User Configuration.
- The SDK workflow never calls `input()` and contains no Test App UI logic.
- Do not import or restore the historical `omotion.tuning` runner.
- Do not fire real hardware while implementing or running automated tests.

---

### Task 1: Add pure dual topology, pair metrics, and target selection

**Files:**

- Modify: `omotion/calibration/laser.py`
- Modify: `tests/test_wi15_laser_calibration.py`

**Interfaces:**

- Consumes: existing `TopologySnapshot`, `CriterionResult`,
  `EnergyMeasurement`, `SensorSide`, and energy constants.
- Produces: `PairMetrics`, `validate_exact_dual_topology()`,
  `calculate_pair_metrics()`, `both_energies_accepted()`, and
  `select_closest_valid_setting_to_target()`.

- [ ] **Step 1: Write failing dual-domain tests**

Add these behavior tests before production changes:

```python
@pytest.mark.parametrize(
    ("topology", "passed"),
    [
        (TopologySnapshot(True, True, True), True),
        (TopologySnapshot(True, True, False), False),
        (TopologySnapshot(True, False, True), False),
        (TopologySnapshot(False, True, True), False),
    ],
)
def test_exact_dual_topology_requires_console_left_and_right(topology, passed):
    assert validate_exact_dual_topology(topology).passed is passed


def test_pair_metrics_preserve_side_values_and_midpoint_math():
    metrics = calculate_pair_metrics(300.0, 400.0)
    assert metrics == PairMetrics(
        left_mean_uj=300.0,
        right_mean_uj=400.0,
        difference_uj=100.0,
        midpoint_uj=350.0,
        midpoint_distance_uj=0.0,
        left_offset_uj=-50.0,
        right_offset_uj=50.0,
    )


@pytest.mark.parametrize(
    ("left", "right", "accepted"),
    [(300, 400, True), (299.999, 350, False), (350, 400.001, False)],
)
def test_dual_acceptance_requires_both_in_inclusive_window(left, right, accepted):
    assert both_energies_accepted(left, right) is accepted


def test_closest_setting_uses_the_supplied_dual_target():
    candidates = [(5000, valid_measurement(390)), (4950, valid_measurement(370))]
    setting, observation = select_closest_valid_setting_to_target(candidates, 375)
    assert (setting, observation.mean_uj) == (4950, 370)
```

- [ ] **Step 2: Run the focused domain tests and confirm RED**

Run:

```powershell
$env:PYTHONPATH=(Get-Location).Path
python -m pytest tests/test_wi15_laser_calibration.py -q
```

Expected: collection or assertion failures because the dual helpers and
`PairMetrics` do not exist.

- [ ] **Step 3: Implement the pure dual helpers minimally**

Add frozen evidence and pure functions:

```python
@dataclass(frozen=True)
class PairMetrics:
    left_mean_uj: float
    right_mean_uj: float
    difference_uj: float
    midpoint_uj: float
    midpoint_distance_uj: float
    left_offset_uj: float
    right_offset_uj: float


def validate_exact_dual_topology(topology: TopologySnapshot) -> CriterionResult:
    passed = (
        topology.console_connected
        and topology.left_connected
        and topology.right_connected
    )
    return CriterionResult(
        "topology",
        passed,
        "Expected a console with both left and right sensors connected.",
    )


def calculate_pair_metrics(left_mean_uj: float, right_mean_uj: float) -> PairMetrics:
    difference = abs(left_mean_uj - right_mean_uj)
    midpoint = (left_mean_uj + right_mean_uj) / 2.0
    return PairMetrics(
        left_mean_uj,
        right_mean_uj,
        difference,
        midpoint,
        abs(midpoint - TARGET_ENERGY_UJ),
        left_mean_uj - TARGET_ENERGY_UJ,
        right_mean_uj - TARGET_ENERGY_UJ,
    )


def both_energies_accepted(left_mean_uj: float, right_mean_uj: float) -> bool:
    return all(
        _is_finite(value)
        and MIN_ACCEPTABLE_ENERGY_UJ <= value <= MAX_ACCEPTABLE_ENERGY_UJ
        for value in (left_mean_uj, right_mean_uj)
    )


def select_closest_valid_setting_to_target(candidates, target_uj):
    valid = [
        candidate
        for candidate in candidates
        if all(item.passed for item in validate_energy_measurement(candidate[1]))
    ]
    return min(valid, key=lambda item: (abs(item[1].mean_uj - target_uj), item[0])) if valid else None
```

Reject nonfinite pair inputs and targets by returning no selection or
nonpassing criteria as appropriate; do not allow NaN ordering to select a
candidate.

- [ ] **Step 4: Run RED tests GREEN and protect existing single rules**

Run:

```powershell
python -m pytest tests/test_wi15_laser_calibration.py -q
git diff --check
```

Expected: all domain tests pass.

- [ ] **Step 5: Commit the domain increment**

```powershell
git add omotion/calibration/laser.py tests/test_wi15_laser_calibration.py
git commit -m "feat: add WI15 dual-sensor domain rules"
```

---

### Task 2: Define the placement-aware dual workflow shell

**Files:**

- Create: `omotion/calibration/dual_sensor_laser.py`
- Create: `tests/test_wi15_dual_sensor_laser_calibration.py`

**Interfaces:**

- Consumes: common domain types and the existing Ophir evidence records.
- Produces: `DualSensorLaserCalibrationRequest`, `DualPreflightSnapshot`,
  `PlacementChangeRequest`, `PlacementAcknowledgement`,
  `SensorEnergyObservation`, `PairObservation`, `TuningStep`, `TuningRound`,
  `CrossCheck`, `DualSensorLaserCalibrationResult`,
  `DualLaserCalibrationBench`, `DualRunRecorder`, and
  `DualSensorLaserCalibrationWorkflow`.

Use these exact immutable evidence shapes. Reuse `ProcedureEvent`,
`OphirSettingEvidence`, `ReportArtifactEvidence`, and `ReportArtifactStatus`
from the existing laser workflow module without changing or duplicating their
behavior.

```python
@dataclass(frozen=True)
class DualSensorLaserCalibrationRequest:
    operator: str
    build_id: str
    fixture_id: str
    procedure_id: str
    output_root: Path | str
    run_id: str
    fixture_calibration_status: str | None = None
    sdk_version: str = _RUNTIME_SDK_VERSION
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class DualPreflightSnapshot:
    topology: TopologySnapshot
    console_identity: DeviceIdentity
    left_sensor_identity: DeviceIdentity
    right_sensor_identity: DeviceIdentity
    console_responsive: bool
    ophir_identity: OphirIdentity | None
    ophir_ready: bool
    ophir_setting_evidence: tuple[OphirSettingEvidence, ...]
    ophir_failure_reason: str | None = None


@dataclass(frozen=True)
class SensorEnergyObservation:
    side: SensorSide
    sensor_serial: str
    label: str
    measurement: EnergyMeasurement
    criteria: tuple[CriterionResult, ...]


@dataclass(frozen=True)
class PairObservation:
    label: str
    left: SensorEnergyObservation
    right: SensorEnergyObservation
    metrics: PairMetrics


@dataclass(frozen=True)
class TuningStep:
    number: int
    label: str
    side: SensorSide
    register_name: str
    requested_value: float
    readback: SettingReadback
    observation: SensorEnergyObservation


@dataclass(frozen=True)
class TuningRound:
    number: int
    label: str
    input_pair: PairObservation
    direction: str
    selected_side: SensorSide
    reason: str
    target_uj: float
    steps: tuple[TuningStep, ...]
    selected_step: TuningStep | None


@dataclass(frozen=True)
class CrossCheck:
    number: int
    label: str
    pair: PairObservation
    accepted: bool
```

Use this complete result boundary:

```python
@dataclass(frozen=True)
class DualSensorLaserCalibrationResult:
    status: ProcedureStatus
    sdk_version: str = _RUNTIME_SDK_VERSION
    started_at: datetime | None = None
    ended_at: datetime | None = None
    failure_kind: FailureKind | None = None
    failure_reason: str | None = None
    topology: TopologySnapshot | None = None
    topology_revalidation: TopologySnapshot | None = None
    identities: tuple[DeviceIdentity, ...] = ()
    ophir_identity: OphirIdentity | None = None
    ophir_setting_evidence: tuple[OphirSettingEvidence, ...] = ()
    pre_existing_config: Mapping[str, float] | None = None
    requested_default_config: Mapping[str, float] | None = None
    default_config_readback: Mapping[str, float] | None = None
    configurations: tuple[SettingReadback, ...] = ()
    placements: tuple[PlacementAcknowledgement, ...] = ()
    observations: tuple[SensorEnergyObservation, ...] = ()
    initial_pair: PairObservation | None = None
    tuning_rounds: tuple[TuningRound, ...] = ()
    crosschecks: tuple[CrossCheck, ...] = ()
    requested_final_config: Mapping[str, float] | None = None
    final_config_readback: Mapping[str, float] | None = None
    final_setting_checks: tuple[FinalSettingCheck, ...] = ()
    active_default_restore: tuple[SettingReadback, ...] = ()
    active_default_restore_failure: str | None = None
    trigger_cleanup_failure: str | None = None
    events: tuple[ProcedureEvent, ...] = ()
    report_paths: tuple[Path | str, ...] = ()
    report_artifact: ReportArtifactEvidence | None = None
```

Define the complete bench and recorder Protocols:

```python
class DualLaserCalibrationBench(Protocol):
    def preflight_dual(self) -> DualPreflightSnapshot: ...
    def revalidate_dual_topology(self) -> TopologySnapshot: ...
    def read_user_configuration(self) -> Mapping[str, float]: ...
    def write_user_configuration(self, configuration: Mapping[str, float]) -> Mapping[str, float] | None: ...
    def bring_up_laser_configuration(self) -> None: ...
    def read_register(self, name: str) -> float: ...
    def write_register(self, name: str, value: float) -> SettingReadback | None: ...
    def read_trigger_rate_hz(self) -> float: ...
    def write_trigger_rate_hz(self, rate_hz: float) -> SettingReadback | None: ...
    def measure_energy(self) -> EnergyMeasurement: ...
    def stop_trigger(self) -> None: ...


class DualRunRecorder(Protocol):
    def record(self, event: ProcedureEvent) -> None: ...
    def checkpoint(self, result: DualSensorLaserCalibrationResult) -> None: ...
```

- [ ] **Step 1: Write failing workflow-contract and preflight tests**

Create a `FakeDualLaserBench`, `FakeRecorder`, and list-backed placement
callback. Prove exact ordering and no mutation/firing on setup failure:

```python
def test_dual_preflight_rejects_missing_side_before_default_write_or_measurement():
    bench = FakeDualLaserBench(topology=TopologySnapshot(True, True, False))
    result = workflow(bench).run(valid_request())
    assert result.status is ProcedureStatus.FAILED
    assert result.failure_kind is FailureKind.SETUP
    assert "write_user_configuration" not in bench.calls
    assert "measure_energy" not in bench.calls


@pytest.mark.parametrize("role", ["console", "left sensor", "right sensor"])
def test_dual_preflight_requires_each_reportable_serial(role):
    bench = FakeDualLaserBench(blank_serial_role=role)
    result = workflow(bench).run(valid_request())
    assert result.failure_kind is FailureKind.SETUP
    assert role in result.failure_reason


def test_ophir_preflight_precedes_topology_revalidation_and_mutation():
    bench = FakeDualLaserBench()
    workflow(bench).run(valid_request())
    assert bench.calls.index("preflight_dual") < bench.calls.index("revalidate_dual_topology")
    assert bench.calls.index("revalidate_dual_topology") < bench.calls.index("write_user_configuration")
```

- [ ] **Step 2: Run the workflow file and confirm RED**

Run:

```powershell
python -m pytest tests/test_wi15_dual_sensor_laser_calibration.py -q
```

Expected: import failure because the dual workflow module does not exist.

- [ ] **Step 3: Add immutable contracts and preflight-only sequencing**

Define the key UI-neutral callback boundary exactly:

```python
@dataclass(frozen=True)
class PlacementChangeRequest:
    from_side: SensorSide | None
    to_side: SensorSide
    sensor_serial: str
    phase: str
    label: str


@dataclass(frozen=True)
class PlacementAcknowledgement:
    request: PlacementChangeRequest
    acknowledged: bool
    timestamp: datetime


PlacementCallback = Callable[[PlacementChangeRequest], bool]


class DualSensorLaserCalibrationWorkflow:
    def __init__(self, bench, recorder, placement_callback: PlacementCallback):
        self._bench = bench
        self._recorder = recorder
        self._placement_callback = placement_callback
```

`DualPreflightSnapshot` carries console, left, and right identities plus the
same Ophir identity/readiness/setting evidence used by the single workflow.
`DualSensorLaserCalibrationResult` carries timestamps, status/failure,
topology/revalidation, identities, configurations, acknowledgements,
observations, initial pair, tuning rounds, cross-checks, final checks,
artifact evidence, events, and cleanup diagnostics. Deep-freeze every mapping
and sequence on construction.

Implement only request validation, preflight, identity/topology/Ophir gates,
late topology revalidation, structured failure conversion, checkpointing,
and trigger cleanup. Task 2 tests exercise setup exits and the placement-aware
observation seam directly; the first successful terminal `run()` path is
introduced test-first by Tasks 3 and 4.

- [ ] **Step 4: Add failing placement-transition tests**

Test the private measurement seam through public workflow behavior:

```python
def test_initial_left_then_right_requests_two_placements_with_serials():
    result, requests = run_through_initial_pair(left=340, right=360)
    assert [(item.from_side, item.to_side, item.sensor_serial) for item in requests] == [
        (None, "left", "LEFT-001"),
        ("left", "right", "RIGHT-001"),
    ]


def test_same_side_consecutive_tuning_measurements_do_not_prompt_again():
    result, requests = run_downward_sweep(selected_side="right", steps=[430, 390])
    assert [item.to_side for item in requests].count("right") == 1


def test_declined_switch_fails_before_associated_measurement():
    bench = FakeDualLaserBench(measurements=[valid_measurement(340)])
    result = workflow(bench, acknowledgements=[True, False]).run(valid_request())
    assert result.status is ProcedureStatus.CANCELED
    assert bench.measurement_count == 1
```

- [ ] **Step 5: Implement side-change-only acknowledgement and observation seam**

Add a workflow-owned `_seated_side`. Before measurement, compare it with the
requested side. Only invoke the callback when they differ. Require the
callback result to be exactly `True`, checkpoint the acknowledgement before
firing, then call `measure_energy()`. Record this label shape with each raw
observation:

```python
label = f"{phase} — {side} sensor"
observation = SensorEnergyObservation(
    side=side,
    sensor_serial=serial,
    label=label,
    measurement=measurement,
    criteria=validate_energy_measurement(measurement),
)
```

- [ ] **Step 6: Verify and commit the workflow shell**

```powershell
python -m pytest tests/test_wi15_dual_sensor_laser_calibration.py -q
python -m pytest tests/test_wi15_single_sensor_laser_calibration.py -q
git diff --check
git add omotion/calibration/dual_sensor_laser.py tests/test_wi15_dual_sensor_laser_calibration.py
git commit -m "feat: add placement-aware WI15 dual workflow"
```

---

### Task 3: Implement checked defaults and the initial differential gate

**Files:**

- Modify: `omotion/calibration/dual_sensor_laser.py`
- Modify: `tests/test_wi15_dual_sensor_laser_calibration.py`

**Interfaces:**

- Consumes: Task 2 workflow contracts and bench protocol.
- Produces: a complete checked-default phase and authoritative
  `initial_pair: PairObservation` evidence.

- [ ] **Step 1: Write failing checked-default tests**

```python
def test_dual_default_phase_preserves_prior_config_and_requires_exact_immediate_readback():
    bench = passing_bench()
    result = workflow(bench).run(valid_request())
    assert result.pre_existing_config == bench.original_config
    assert result.requested_default_config == default_user_configuration()
    assert result.default_config_readback == default_user_configuration()
    assert bench.calls.index("write_user_configuration") < bench.calls.index("bring_up_laser_configuration")


@pytest.mark.parametrize(
    "readback",
    [None, {"TA_CURRENT_DRV": 5000}, {**default_user_configuration(), "EXTRA": 1}],
)
def test_dual_default_mismatch_stops_before_measurement(readback):
    bench = passing_bench(default_write_result=readback)
    result = workflow(bench).run(valid_request())
    assert result.failure_kind is FailureKind.CONFIGURATION
    assert bench.measurement_count == 0
```

Also prove active current, pulse width, seed, both pulse limits, and exact 40
Hz behavior use typed immediate readbacks and the inclusive ±2 percent rules.

- [ ] **Step 2: Run the new tests and confirm RED**

```powershell
python -m pytest tests/test_wi15_dual_sensor_laser_calibration.py -q
```

Expected: assertions fail because the dual workflow stops after preflight.

- [ ] **Step 3: Implement checked default configuration**

Reuse fresh `default_user_configuration()` values and copy the single
workflow's proven ordering without calling its private methods:

```python
prior = dict(self._bench.read_user_configuration())
requested_default = default_user_configuration()
immediate = self._bench.write_user_configuration(requested_default)
if immediate != requested_default:
    raise _ProcedureFailure(FailureKind.CONFIGURATION, "Default User Configuration immediate readback did not match exactly.")
self._bench.bring_up_laser_configuration()
```

Checkpoint the prior, requested, and immediate configurations independently.
Revalidate exact dual topology with no callback/checkpoint seam between the
last revalidation and first write.

- [ ] **Step 4: Write failing initial-pair and differential tests**

```python
@pytest.mark.parametrize(("difference", "status"), [(100.0, ProcedureStatus.PASSED), (100.001, ProcedureStatus.FAILED_NCR)])
def test_initial_differential_gate_is_strictly_greater_than_100(difference, status):
    left = 350.0 - difference / 2.0
    right = 350.0 + difference / 2.0
    result = run_no_adjustment_pair(left, right)
    assert result.initial_pair.metrics.difference_uj == pytest.approx(difference)
    assert result.status is status


def test_invalid_initial_right_observation_is_recorded_but_not_used_as_a_pair():
    result = run_initial_measurements(valid_measurement(350), invalid_measurement(n=25))
    assert len(result.observations) == 2
    assert result.initial_pair is None
    assert result.failure_kind is FailureKind.MEASUREMENT
```

- [ ] **Step 5: Implement the initial pair and immediate NCR**

Acquire left then right through the placement-aware seam. Checkpoint raw
measurements before deriving criteria and criteria before constructing the
pair. Use `calculate_pair_metrics()` once both are valid. If
`difference_uj > 100`, raise an NCR before any adjustment or final write.

Use these normative labels:

```python
"Initial paired measurement — left sensor"
"Initial paired measurement — right sensor"
"Initial differential gate — accepted at 100 uJ or below"
"Initial differential gate — NCR: differential exceeded 100 uJ"
```

- [ ] **Step 6: Verify and commit defaults plus initial pair**

```powershell
python -m pytest tests/test_wi15_laser_calibration.py tests/test_wi15_dual_sensor_laser_calibration.py -q
git diff --check
git add omotion/calibration/dual_sensor_laser.py tests/test_wi15_dual_sensor_laser_calibration.py
git commit -m "feat: gate WI15 dual initial sensor pair"
```

---

### Task 4: Implement midpoint tuning, three cross-checks, and final handoff

**Files:**

- Modify: `omotion/calibration/dual_sensor_laser.py`
- Modify: `tests/test_wi15_dual_sensor_laser_calibration.py`

**Interfaces:**

- Consumes: valid latest `PairObservation`, checked register/config bench API,
  placement-aware observation seam, and Task 1 target-selection helper.
- Produces: immutable `TuningRound`, `TuningStep`, `CrossCheck`, final ±2
  percent checks, and authoritative tuned configuration readback.

- [ ] **Step 1: Write failing direction and target tests**

```python
def test_above_midpoint_selects_higher_side_and_calculates_upper_target():
    result = run_pair_and_one_round(left=360, right=400)
    round_one = result.tuning_rounds[0]
    assert round_one.selected_side == "right"
    assert round_one.target_uj == 370
    assert "higher energy reading" in round_one.reason


def test_below_midpoint_selects_lower_side_and_calculates_lower_target():
    result = run_pair_and_one_round(left=280, right=320)
    round_one = result.tuning_rounds[0]
    assert round_one.selected_side == "left"
    assert round_one.target_uj == 330
    assert "lower energy reading" in round_one.reason


def test_exact_350_makes_no_adjustment_but_still_runs_a_complete_crosscheck():
    result, bench = run_pair_and_one_round(left=340, right=360)
    assert result.tuning_rounds[0].direction == "none"
    assert result.tuning_rounds[0].steps == ()
    assert len(result.crosschecks) == 1
    assert not bench.register_writes_after_default_setup
```

- [ ] **Step 2: Run direction tests RED, then implement round selection GREEN**

Implement a pure workflow helper returning side, direction, target, and
plain-language rationale from the latest pair. Equal side readings use left
as the deterministic selected side, though midpoint 350 performs no
adjustment.

Run:

```powershell
python -m pytest tests/test_wi15_dual_sensor_laser_calibration.py -q -k "midpoint_selects"
```

- [ ] **Step 3: Write failing downward-current sweep tests**

Prove 50 mA steps, higher-side placement, authoritative readback, target
crossing/closest selection, reapplication, no pulse write, and 2000 mA bound:

```python
def test_downward_round_keeps_selected_side_seated_across_all_steps():
    result, placement_requests, bench = run_downward_round(
        pair=(360, 420), selected_measurements=[410, 385, 365]
    )
    assert bench.requested_values("TA_CURRENT_DRV") == [4950, 4900, 4850]
    assert [request.to_side for request in placement_requests].count("right") == 1
    assert not bench.requested_values("TA_PULSE_WIDTH")
    assert "selected right sensor because it had the higher energy reading" in result.tuning_rounds[0].label
```

- [ ] **Step 4: Implement downward sweep minimally and run it GREEN**

For each setting: write, require typed name/requested/actual evidence,
checkpoint it, acquire a valid selected-side observation, checkpoint it, and
stop on target crossing or floor. Choose the observed candidate nearest the
calculated side target; reapply/read back the selected setting when it is not
already active. An unreachable approved setting at the floor is NCR.

- [ ] **Step 5: Write failing upward-pulse sweep tests**

```python
def test_upward_round_verifies_limits_then_uses_10_us_steps_on_lower_side():
    result, placement_requests, bench = run_upward_round(
        pair=(270, 330), selected_measurements=[285, 305, 325]
    )
    assert bench.first_register_writes == [
        ("EE_PULSE_WIDTH_UL", 660),
        ("OPT_PULSE_WIDTH_UL", 660),
    ]
    assert bench.requested_values("TA_PULSE_WIDTH") == [510, 520, 530]
    assert not bench.requested_values("TA_CURRENT_DRV")


def test_upward_round_at_600_below_300_fails_ncr_without_final_config():
    result, _, bench = run_upward_to_bound(final_energy=299.999)
    assert result.status is ProcedureStatus.FAILED_NCR
    assert bench.user_configuration_writes == [default_user_configuration()]
```

- [ ] **Step 6: Implement upward sweep and run it GREEN**

Write/read back both 660 limits before pulse adjustment. Keep current fixed,
step pulse width by 10 microseconds, never exceed 600, select the closest
observed setting to the calculated target, and fail NCR at 600 when the
selected energy is below 300.

- [ ] **Step 7: Write failing complete cross-check-loop tests**

```python
@pytest.mark.parametrize("passing_crosscheck", [1, 2, 3])
def test_dual_workflow_may_pass_on_any_of_three_complete_crosschecks(passing_crosscheck):
    result = run_crosschecks(passing_crosscheck=passing_crosscheck)
    assert result.status is ProcedureStatus.PASSED
    assert len(result.crosschecks) == passing_crosscheck
    assert result.crosschecks[-1].accepted is True


def test_fourth_crosscheck_is_never_started():
    result, bench = run_crosschecks(passing_crosscheck=None)
    assert result.status is ProcedureStatus.FAILED_NCR
    assert len(result.crosschecks) == 3
    assert bench.complete_pair_count == 4  # initial pair plus three cross-checks


def test_invalid_partial_pair_fails_without_consuming_crosscheck_number():
    result = run_partial_crosscheck_failure()
    assert result.failure_kind is FailureKind.MEASUREMENT
    assert result.crosschecks == ()
```

- [ ] **Step 8: Implement cross-check loop and auditor labels**

Every cross-check measures left then right through the placement seam,
increments only after both valid observations exist, derives all metrics, and
accepts only when both readings are in range. Use labels such as:

```python
f"Cross-check {number} — left sensor verification"
f"Cross-check {number} — right sensor verification"
f"Cross-check {number} — paired result: both sensors within the approved 300-400 uJ range"
```

- [ ] **Step 9: Write failing final ±2 percent and configuration tests**

Prove inclusive bounds, typed identity-preserving readbacks, complete tuned
configuration, provisional 660 limits only after upward tuning, exact
immediate complete readback, and no final write for every failure path.

```python
def test_passing_pair_writes_complete_tuned_configuration_only_after_final_checks():
    result, bench = run_passing_crosscheck()
    assert all(check.passed for check in result.final_setting_checks)
    assert result.final_config_readback == result.requested_final_config
    assert bench.calls.index("final_setting_checks") < bench.calls.index("write_final_user_configuration")


def test_failed_final_current_check_never_writes_passing_configuration():
    result, bench = run_passing_crosscheck(active_current=0.97999 * 5000)
    assert result.status is ProcedureStatus.FAILED
    assert bench.user_configuration_writes == [default_user_configuration()]


def test_real_workflow_and_recorder_persist_complete_downward_pass(tmp_path):
    recorder = JsonRunRecorder(tmp_path, "WI-00015", "dual-pass")
    result = workflow(passing_downward_bench(), recorder=recorder).run(valid_request())
    payload = json.loads(recorder.json_path.read_text(encoding="utf-8"))
    assert result.status is ProcedureStatus.PASSED
    assert payload["initial_pair"]["metrics"]["difference_uj"] <= 100
    assert len(payload["crosschecks"]) == 1
    assert payload["final_config_readback"] == payload["requested_final_config"]
```

- [ ] **Step 10: Implement final handoff, cleanup preservation, and commit**

Construct `FinalSettingCheck` values in the workflow and checkpoint them
before the final write. Write requested settings, not quantized actuals. Mark
passed only after exact immediate complete readback. Preserve primary failure
and checkpoint best-effort active-default restoration/trigger cleanup errors.

Run:

```powershell
python -m pytest tests/test_wi15_laser_calibration.py tests/test_wi15_dual_sensor_laser_calibration.py tests/test_wi15_single_sensor_laser_calibration.py -q
git diff --check
git add omotion/calibration/dual_sensor_laser.py tests/test_wi15_dual_sensor_laser_calibration.py
git commit -m "feat: tune and cross-check WI15 dual sensors"
```

---

### Task 5: Extend the production bench for exact dual topology

**Files:**

- Modify: `omotion/calibration/laser_hardware.py`
- Modify: `tests/test_wi15_laser_calibration_hardware.py`

**Interfaces:**

- Consumes: `DualPreflightSnapshot`, `validate_exact_dual_topology()`, and
  existing Motion/Ophir adapter behavior.
- Produces: `MotionLaserCalibrationBench.preflight_dual()`,
  `revalidate_dual_topology()`, and a topology-mode-aware pre-fire guard while
  retaining existing `preflight(side)` and `revalidate_topology(side)`.

- [ ] **Step 1: Write failing dual hardware tests**

```python
def test_dual_preflight_waits_for_two_sensors_and_returns_both_identities():
    bench, interface = make_motion_bench(left=True, right=True)
    snapshot = bench.preflight_dual()
    assert interface.wait_calls == [{"console": True, "sensors": 2, "timeout": 10.0}]
    assert snapshot.left_sensor_identity.serial == "LEFT-001"
    assert snapshot.right_sensor_identity.serial == "RIGHT-001"


def test_dual_measurement_revalidates_both_sensors_immediately_before_firing():
    bench, interface = make_preflighted_dual_bench()
    interface.right.connected = False
    with pytest.raises(RuntimeError, match="exact declared dual topology"):
        bench.measure_energy()
    assert interface.console.start_trigger_calls == 0


def test_single_preflight_and_prefire_contract_remain_unchanged():
    bench, interface = make_motion_bench(left=True, right=False)
    bench.preflight("left")
    bench.measure_energy()
    assert interface.wait_calls[0]["sensors"] == 1
```

- [ ] **Step 2: Run hardware tests and confirm RED**

```powershell
python -m pytest tests/test_wi15_laser_calibration_hardware.py -q
```

Expected: missing `preflight_dual()` and dual topology-mode behavior.

- [ ] **Step 3: Implement dual methods without changing single defaults**

Change startup to accept a required count on first use:

```python
def _ensure_started(self, required_sensor_count: int = 1) -> None:
    if self._started:
        return
    self._interface.start(wait=False)
    self._started = True
    self._interface.wait_for_ready(
        console=True, sensors=required_sensor_count, timeout=self._wait_timeout
    )
```

Track a declared topology mode rather than inferring from current inventory.
`preflight_dual()` uses count two, completes the same Ophir preflight, waits
for a quiet exact snapshot, records both sensor identities, and authorizes
only dual pre-fire checks. `measure_energy()` dispatches to the exact single
or dual validator based on that stored declaration.

- [ ] **Step 4: Run focused and adjacent regression tests GREEN**

```powershell
python -m pytest tests/test_wi15_laser_calibration_hardware.py tests/test_wi15_single_sensor_laser_calibration.py tests/test_wi15_dual_sensor_laser_calibration.py -q
python -m ruff check omotion/calibration/laser_hardware.py tests/test_wi15_laser_calibration_hardware.py
git diff --check
```

- [ ] **Step 5: Commit the adapter increment**

```powershell
git add omotion/calibration/laser_hardware.py tests/test_wi15_laser_calibration_hardware.py
git commit -m "feat: support exact dual topology in WI15 bench"
```

---

### Task 6: Add the auditor-readable dual HTML report

**Files:**

- Create: `omotion/calibration/dual_sensor_laser_report.py`
- Create: `tests/test_wi15_dual_sensor_laser_calibration_report.py`
- Reuse without behavior change: `omotion/calibration/reporting.py`

**Interfaces:**

- Consumes: `JsonRunRecorder`, `HtmlRunReport.write()`, `json_safe_value()`,
  dual request, and dual result.
- Produces: `DualSensorHtmlRunReport(HtmlRunReport)` with overridden
  `render()` and inherited atomic `write()`.

- [ ] **Step 1: Write failing report tests**

```python
def test_dual_report_uses_plain_language_for_complete_passing_evidence(tmp_path):
    html = DualSensorHtmlRunReport(tmp_path).render(request(), passing_result(), "run.json")
    assert "Initial paired measurement — left sensor" in html
    assert "Initial differential" in html
    assert "selected right sensor because it had the lower energy reading" in html
    assert "Cross-check 1" in html
    assert "Final configuration verification" in html
    assert "LEFT-001" in html and "RIGHT-001" in html


def test_dual_report_omits_unreached_sections_after_initial_ncr(tmp_path):
    html = DualSensorHtmlRunReport(tmp_path).render(request(), initial_ncr_result(), "run.json")
    assert "Initial differential" in html
    assert "Midpoint adjustment round" not in html
    assert "Passing tuned User Configuration" not in html


def test_dual_report_renders_supplied_metrics_without_recalculation(tmp_path):
    result = passing_result(pair_metrics=PairMetrics(1, 2, 91, 92, 93, 94, 95))
    html = DualSensorHtmlRunReport(tmp_path).render(request(), result, "run.json")
    assert all(str(value) in html for value in (91, 92, 93, 94, 95))


def test_real_workflow_recorder_and_report_preserve_initial_ncr_scope(tmp_path):
    recorder = JsonRunRecorder(tmp_path, "WI-00015", "dual-ncr")
    result = workflow(initial_differential_ncr_bench(), recorder=recorder).run(request())
    report = DualSensorHtmlRunReport(recorder.run_directory)
    report.write(request(), result, recorder.json_path)
    html = report.report_path.read_text(encoding="utf-8")
    assert result.status is ProcedureStatus.FAILED_NCR
    assert "Initial differential" in html
    assert "Midpoint adjustment round" not in html
    assert "Passing tuned User Configuration" not in html
```

Also test HTML escaping, placement acknowledgements, every tuning step,
three-cross-check NCR, default/final changed-value highlighting, cleanup
diagnostics, JSON link, and requested-but-unconfirmed final configuration.

- [ ] **Step 2: Run report tests and confirm RED**

```powershell
python -m pytest tests/test_wi15_dual_sensor_laser_calibration_report.py -q
```

Expected: import failure because the dual report module does not exist.

- [ ] **Step 3: Implement the dual renderer minimally**

Subclass the existing report only to reuse atomic file writing and shared
escaping/table helpers. Override the page title and full body. Gate every
section on supplied evidence:

```python
class DualSensorHtmlRunReport(HtmlRunReport):
    def render(self, request, result, json_path=None) -> str:
        request_data = json_safe_value(request)
        result_data = json_safe_value(result)
        sections = [self._summary(request_data, result_data)]
        if result_data.get("initial_pair") is not None:
            sections.append(self._initial_pair(result_data["initial_pair"]))
        if result_data.get("tuning_rounds"):
            sections.append(self._tuning_rounds(result_data["tuning_rounds"]))
        if result_data.get("crosschecks"):
            sections.append(self._crosschecks(result_data["crosschecks"]))
        return self._page(sections, json_path)
```

Render the workflow's labels, acceptance booleans, and metrics verbatim. Do
not calculate a differential, midpoint, or pass/fail in the renderer.

- [ ] **Step 4: Verify report and recorder regressions GREEN**

```powershell
python -m pytest tests/test_wi15_laser_calibration_report.py tests/test_wi15_dual_sensor_laser_calibration_report.py -q
python -m ruff check omotion/calibration/dual_sensor_laser_report.py tests/test_wi15_dual_sensor_laser_calibration_report.py
git diff --check
```

- [ ] **Step 5: Commit the report increment**

```powershell
git add omotion/calibration/dual_sensor_laser_report.py tests/test_wi15_dual_sensor_laser_calibration_report.py
git commit -m "feat: report WI15 dual-sensor calibration"
```

---

### Task 7: Add the thin dual-sensor operator script

**Files:**

- Create: `scripts/wi15_dual_sensor_laser_calibration.py`
- Create: `tests/test_wi15_dual_sensor_laser_script.py`
- Modify: `scripts/WI15_PROCEDURES.md`

**Interfaces:**

- Consumes: `JsonRunRecorder`, `OphirEnergyMeter`,
  `MotionLaserCalibrationBench`, `DualSensorLaserCalibrationWorkflow`, and
  `DualSensorHtmlRunReport`.
- Produces: `main(argv, input_func, output_func) -> int` and a courteous
  placement callback passed into the workflow.

- [ ] **Step 1: Write failing script tests**

```python
def test_script_source_is_ascii_safe_and_has_no_legacy_runner():
    source = SCRIPT_PATH.read_bytes()
    source.decode("ascii")
    assert b"omotion.tuning" not in source


def test_placement_callback_names_side_serial_and_phase_without_duplicate_prompt(monkeypatch, tmp_path):
    outputs, prompts = run_script_with_fake_workflow(monkeypatch, tmp_path)
    assert any("left sensor module (serial LEFT-001)" in prompt for prompt in prompts)
    assert any("right sensor module (serial RIGHT-001)" in prompt for prompt in prompts)
    assert not any("_measure_once" in text for text in outputs + prompts)


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [(ProcedureStatus.PASSED, 0), (ProcedureStatus.FAILED, 1), (ProcedureStatus.FAILED_NCR, 1), (ProcedureStatus.CANCELED, 1)],
)
def test_terminal_status_controls_exit_code(status, exit_code, monkeypatch, tmp_path):
    assert run_script(monkeypatch, tmp_path, status=status) == exit_code
```

Also prove metadata collection precedes hardware construction, EOF/cancel
cleanup, one run identifier shared by request/recorder, JSON checkpoint before
HTML, stale passed JSON cannot survive report failure, claimed report exists,
and all resources close on exceptions.

- [ ] **Step 2: Run script tests and confirm RED**

```powershell
python -m pytest tests/test_wi15_dual_sensor_laser_script.py -q
```

Expected: script import/path failure.

- [ ] **Step 3: Implement the CLI with kind audit wording**

Keep ASCII source text by spelling units `uJ` and `us`. Construct the callback
inside `main()`:

```python
def acknowledge_placement(change: PlacementChangeRequest) -> bool:
    output_func(change.label)
    prompt = (
        f"Please place the {change.to_side} sensor module "
        f"(serial {change.sensor_serial}) in the Ophir 0 cm fixture. "
        "Confirm when it is securely seated [y/N]: "
    )
    return _confirmed(prompt, input_func)
```

Use the single script's metadata arguments, factories, cleanup, report
finalization, and exit conventions. Do not ask the user to re-confirm a side
inside the CLI; the workflow calls the placement callback only for a required
transition. Print full JSON/report paths and terminal status.

- [ ] **Step 4: Update the supported-procedure index**

Mark Dual-Sensor Laser Calibration implemented in the clean architecture and
document this invocation:

```powershell
python scripts/wi15_dual_sensor_laser_calibration.py --output-dir C:\WI15_runs
```

State that both sensor modules remain connected and the operator moves only
the requested module into the Ophir 0 cm fixture.

- [ ] **Step 5: Verify and commit the CLI increment**

```powershell
python -m pytest tests/test_wi15_dual_sensor_laser_script.py tests/test_wi15_single_sensor_laser_script.py -q
python -m py_compile scripts/wi15_dual_sensor_laser_calibration.py
python -m ruff check scripts/wi15_dual_sensor_laser_calibration.py tests/test_wi15_dual_sensor_laser_script.py
git diff --check
git add scripts/wi15_dual_sensor_laser_calibration.py tests/test_wi15_dual_sensor_laser_script.py scripts/WI15_PROCEDURES.md
git commit -m "feat: add dual-sensor WI15 laser script"
```

---

### Task 8: Integrate, review, and publish the dual procedure

**Files:**

- Verify without planned production changes: all WI15 files from Tasks 1-7.
- Modify after verification:
  `docs/superpowers/specs/2026-08-12-wi15-dual-sensor-laser-calibration.md`.
- If a check exposes a defect, return to the responsible task, add a focused
  failing regression test, make the minimal production correction, and rerun
  that task's focused checks before resuming this task.

**Interfaces:**

- Consumes: all Task 1-7 deliverables.
- Produces: final spec mapping, fresh verification evidence, verified commits,
  and the updated existing PR branch.

- [ ] **Step 1: Run the exact WI15 suite**

```powershell
$env:PYTHONPATH=(Get-Location).Path
python -m pytest `
  tests/test_wi15_laser_calibration.py `
  tests/test_wi15_single_sensor_laser_calibration.py `
  tests/test_wi15_dual_sensor_laser_calibration.py `
  tests/test_wi15_laser_calibration_hardware.py `
  tests/test_wi15_laser_calibration_report.py `
  tests/test_wi15_dual_sensor_laser_calibration_report.py `
  tests/test_wi15_single_sensor_laser_script.py `
  tests/test_wi15_dual_sensor_laser_script.py -q
```

Expected: all pass with no hardware access, skips, or warnings.

- [ ] **Step 2: Run static and forbidden-dependency checks**

```powershell
python -m py_compile `
  omotion/calibration/laser.py `
  omotion/calibration/single_sensor_laser.py `
  omotion/calibration/dual_sensor_laser.py `
  omotion/calibration/laser_hardware.py `
  omotion/calibration/reporting.py `
  omotion/calibration/dual_sensor_laser_report.py `
  scripts/wi15_single_sensor_laser_calibration.py `
  scripts/wi15_dual_sensor_laser_calibration.py
python -m ruff check `
  omotion/calibration/laser.py `
  omotion/calibration/dual_sensor_laser.py `
  omotion/calibration/laser_hardware.py `
  omotion/calibration/dual_sensor_laser_report.py `
  scripts/wi15_dual_sensor_laser_calibration.py `
  tests/test_wi15_laser_calibration.py `
  tests/test_wi15_dual_sensor_laser_calibration.py `
  tests/test_wi15_laser_calibration_hardware.py `
  tests/test_wi15_dual_sensor_laser_calibration_report.py `
  tests/test_wi15_dual_sensor_laser_script.py
rg -n "omotion\.tuning|input\(" `
  omotion/calibration/dual_sensor_laser.py `
  omotion/calibration/dual_sensor_laser_report.py `
  omotion/calibration/laser_hardware.py
git diff --check
```

Expected: compile/Ruff/diff checks succeed; forbidden scan has no matches.

- [ ] **Step 3: Run the hardware-independent repository suite**

```powershell
python -m pytest -m "not fpga and not imu and not console and not sensor and not slow and not sequence and not destructive"
```

Record exact passed/skipped/deselected totals and preserve the exact
command/output in the task report.

- [ ] **Step 4: Perform a requirement-by-requirement diff review**

```powershell
git diff --check origin/next...HEAD
git status --short --branch
git log --oneline origin/next..HEAD
```

Check each Global Constraint against code plus a named test. Confirm the
single-sensor live path was not semantically changed, no Test App logic was
added, report acceptance is workflow-owned, and no generated run artifact is
tracked.

- [ ] **Step 5: Mark the dual spec software-verified and commit mapping**

Change the spec status only after Steps 3-6 pass to:

```text
Implemented and software-verified; dual-sensor hardware execution pending
```

Add an implementation mapping naming the dual workflow, hardware adapter,
report, CLI, and five focused test files. Commit:

```powershell
git add docs/superpowers/specs/2026-08-12-wi15-dual-sensor-laser-calibration.md
git commit -m "docs: map WI15 dual-sensor implementation"
```

- [ ] **Step 6: Run fresh post-commit verification and push**

Repeat the exact WI15 suite, compile/Ruff/forbidden scans, `git diff --check`,
and clean-status check from the committed tree. Push
`feature/214-wi15-calibration-procedures` to update the existing PR. Report
hardware validation as pending; do not run the script against a one-sensor
bench.
