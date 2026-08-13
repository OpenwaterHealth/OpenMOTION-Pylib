"""UI-neutral WI-00015 dual-sensor laser calibration workflow."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import math
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping, Protocol

try:
    from omotion import __version__ as _RUNTIME_SDK_VERSION
except (ImportError, AttributeError):
    _RUNTIME_SDK_VERSION = "unavailable"

from .laser import (
    CURRENT_FLOOR_MA,
    CURRENT_STEP_MA,
    MAX_PULSE_WIDTH_US,
    PULSE_WIDTH_STEP_US,
    TARGET_ENERGY_UJ,
    TEMPORARY_PULSE_WIDTH_LIMIT_US,
    CriterionResult,
    DeviceIdentity,
    EnergyMeasurement,
    FailureKind,
    FinalSettingCheck,
    OphirIdentity,
    PairMetrics,
    ProcedureStatus,
    SensorSide,
    SettingReadback,
    TopologySnapshot,
    both_energies_accepted,
    calculate_pair_metrics,
    default_user_configuration,
    percent_difference,
    select_closest_valid_setting_to_target,
    validate_energy_measurement,
    validate_exact_dual_topology,
    validate_serial,
    within_percent,
)
from .single_sensor_laser import (
    OphirEvidenceApplicability,
    OphirSettingEvidence,
    ProcedureEvent,
    ReportArtifactEvidence,
)


def _deeply_immutable(value):
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _deeply_immutable(item) for key, item in value.items()}
        )
    if isinstance(value, tuple | list):
        return tuple(_deeply_immutable(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_deeply_immutable(item) for item in value)
    return value


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
class TuningSelection:
    direction: str
    selected_side: SensorSide | None
    target_uj: float
    requested_current_ma: float
    requested_pulse_width_us: float
    selected_mean_uj: float
    rationale: str


@dataclass(frozen=True)
class TuningRound:
    number: int
    label: str
    input_pair: PairObservation
    direction: str
    selected_side: SensorSide | None
    reason: str
    target_uj: float
    steps: tuple[TuningStep, ...]
    selection: TuningSelection | None


@dataclass(frozen=True)
class CrossCheck:
    number: int
    label: str
    pair: PairObservation
    accepted: bool


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
    adjustments: tuple[SettingReadback, ...] = ()
    requested_final_config: Mapping[str, float] | None = None
    final_config_readback: Mapping[str, float] | None = None
    final_setting_checks: tuple[FinalSettingCheck, ...] = ()
    active_default_restore: tuple[SettingReadback, ...] = ()
    active_default_restore_failure: str | None = None
    trigger_cleanup_failure: str | None = None
    resource_cleanup_failure: str | None = None
    events: tuple[ProcedureEvent, ...] = ()
    report_paths: tuple[Path | str, ...] = ()
    report_artifact: ReportArtifactEvidence | None = None

    def __post_init__(self) -> None:
        for name in (
            "pre_existing_config",
            "requested_default_config",
            "default_config_readback",
            "requested_final_config",
            "final_config_readback",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _deeply_immutable(value))


class DualLaserCalibrationBench(Protocol):
    def preflight_dual(self) -> DualPreflightSnapshot: ...

    def revalidate_dual_topology(self) -> TopologySnapshot: ...

    def read_user_configuration(self) -> Mapping[str, float]: ...

    def write_user_configuration(
        self, configuration: Mapping[str, float]
    ) -> Mapping[str, float] | None: ...

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


PlacementCallback = Callable[[PlacementChangeRequest], bool]


@dataclass(frozen=True)
class _ProcedureFailure(Exception):
    kind: FailureKind
    reason: str


@dataclass
class _RunState:
    request: DualSensorLaserCalibrationRequest
    preflight: DualPreflightSnapshot | None = None
    topology_revalidation: TopologySnapshot | None = None
    pre_existing_config: Mapping[str, float] | None = None
    requested_default_config: Mapping[str, float] | None = None
    default_config_readback: Mapping[str, float] | None = None
    configurations: list[SettingReadback] = field(default_factory=list)
    placements: list[PlacementAcknowledgement] = field(default_factory=list)
    observations: list[SensorEnergyObservation] = field(default_factory=list)
    initial_pair: PairObservation | None = None
    tuning_rounds: list[TuningRound] = field(default_factory=list)
    crosschecks: list[CrossCheck] = field(default_factory=list)
    adjustments: list[SettingReadback] = field(default_factory=list)
    requested_final_config: Mapping[str, float] | None = None
    final_config_readback: Mapping[str, float] | None = None
    final_setting_checks: list[FinalSettingCheck] = field(default_factory=list)
    active_default_restore: list[SettingReadback] = field(default_factory=list)
    active_default_restore_failure: str | None = None
    trigger_cleanup_failure: str | None = None
    events: list[ProcedureEvent] = field(default_factory=list)
    current_requested_ma: float = 5000.0
    pulse_requested_us: float = 500.0
    used_upward_tuning: bool = False
    active_defaults_established: bool = False
    measurement_started: bool = False

    def result(
        self,
        status: ProcedureStatus,
        *,
        failure: _ProcedureFailure | None = None,
        ended_at: datetime | None = None,
    ) -> DualSensorLaserCalibrationResult:
        preflight = self.preflight
        identities = (
            (
                preflight.console_identity,
                preflight.left_sensor_identity,
                preflight.right_sensor_identity,
            )
            if preflight
            else ()
        )
        return DualSensorLaserCalibrationResult(
            status=status,
            sdk_version=self.request.sdk_version,
            started_at=self.request.started_at,
            ended_at=ended_at,
            failure_kind=failure.kind if failure else None,
            failure_reason=failure.reason if failure else None,
            topology=preflight.topology if preflight else None,
            topology_revalidation=self.topology_revalidation,
            identities=identities,
            ophir_identity=preflight.ophir_identity if preflight else None,
            ophir_setting_evidence=(
                preflight.ophir_setting_evidence if preflight else ()
            ),
            pre_existing_config=self.pre_existing_config,
            requested_default_config=self.requested_default_config,
            default_config_readback=self.default_config_readback,
            configurations=tuple(self.configurations),
            placements=tuple(self.placements),
            observations=tuple(self.observations),
            initial_pair=self.initial_pair,
            tuning_rounds=tuple(self.tuning_rounds),
            crosschecks=tuple(self.crosschecks),
            adjustments=tuple(self.adjustments),
            requested_final_config=self.requested_final_config,
            final_config_readback=self.final_config_readback,
            final_setting_checks=tuple(self.final_setting_checks),
            active_default_restore=tuple(self.active_default_restore),
            active_default_restore_failure=self.active_default_restore_failure,
            trigger_cleanup_failure=self.trigger_cleanup_failure,
            events=tuple(self.events),
        )


class DualSensorLaserCalibrationWorkflow:
    """Run the approved two-sensor midpoint calibration without owning UI."""

    _MAX_CROSSCHECKS = 3

    def __init__(
        self,
        bench: DualLaserCalibrationBench,
        recorder: DualRunRecorder,
        placement_callback: PlacementCallback,
    ):
        self._bench = bench
        self._recorder = recorder
        self._placement_callback = placement_callback
        self._seated_side: SensorSide | None = None

    def run(
        self, request: DualSensorLaserCalibrationRequest
    ) -> DualSensorLaserCalibrationResult:
        self._seated_side = None
        state = _RunState(request=request)
        failure: _ProcedureFailure | None = None
        stage = "setup"
        try:
            self._preflight(state)
            stage = "configuration"
            self._establish_defaults(state)
            stage = "measurement"
            state.measurement_started = True
            state.initial_pair = self._measure_pair(
                state,
                phase="Initial paired measurement",
                pair_label="Initial paired measurement — paired baseline",
            )
            self._checkpoint(state)
            if state.initial_pair.metrics.difference_uj > 100.0:
                self._record_event(
                    state,
                    "initial_differential",
                    "Initial differential gate — NCR because the paired differential exceeded 100 uJ.",
                )
                raise _ProcedureFailure(
                    FailureKind.NCR,
                    "Initial left/right energy differential exceeded 100 uJ.",
                )
            self._record_event(
                state,
                "initial_differential",
                "Initial differential gate — accepted because the paired differential was 100 uJ or below.",
            )
            latest_pair = state.initial_pair
            for crosscheck_number in range(1, self._MAX_CROSSCHECKS + 1):
                tuning_round = self._tune_from_pair(
                    state, latest_pair, crosscheck_number
                )
                self._record_event(state, "tuning", tuning_round.label)
                self._checkpoint(state)

                pair = self._measure_pair(
                    state,
                    phase=f"Cross-check {crosscheck_number}",
                    pair_label=f"Cross-check {crosscheck_number} — paired verification result",
                )
                accepted = both_energies_accepted(
                    pair.metrics.left_mean_uj, pair.metrics.right_mean_uj
                )
                crosscheck = CrossCheck(
                    number=crosscheck_number,
                    label=self._crosscheck_label(crosscheck_number, accepted),
                    pair=pair,
                    accepted=accepted,
                )
                state.crosschecks.append(crosscheck)
                self._record_event(state, "crosscheck", crosscheck.label)
                self._checkpoint(state)
                if accepted:
                    self._write_passing_configuration(state)
                    break
                latest_pair = pair
            else:
                raise _ProcedureFailure(
                    FailureKind.NCR,
                    "Both sensors were not within 300 to 400 uJ after three complete cross-checks.",
                )
        except _ProcedureFailure as caught:
            failure = caught
        except Exception as error:
            if stage == "measurement":
                failure = _ProcedureFailure(
                    FailureKind.MEASUREMENT,
                    str(error) or "Dual-sensor energy measurement failed.",
                )
            elif stage == "configuration":
                failure = _ProcedureFailure(
                    FailureKind.CONFIGURATION,
                    str(error) or "Dual-sensor configuration failed.",
                )
            else:
                failure = _ProcedureFailure(
                    FailureKind.SETUP,
                    str(error) or "Dual-sensor bench preflight failed.",
                )
        finally:
            try:
                self._bench.stop_trigger()
            except Exception:
                state.trigger_cleanup_failure = "Trigger stop failed."

        if failure is None and state.trigger_cleanup_failure:
            failure = _ProcedureFailure(
                FailureKind.MEASUREMENT, state.trigger_cleanup_failure
            )
        if failure is None:
            result = state.result(
                ProcedureStatus.PASSED, ended_at=datetime.now(timezone.utc)
            )
            self._recorder.checkpoint(result)
            return result

        assert failure is not None
        if state.active_defaults_established and state.measurement_started:
            self._restore_active_defaults(state)
        if state.trigger_cleanup_failure:
            self._record_event(
                state, "trigger_cleanup", state.trigger_cleanup_failure
            )
        self._record_event(state, "failure", failure.reason)
        status = (
            ProcedureStatus.FAILED_NCR
            if failure.kind is FailureKind.NCR
            else ProcedureStatus.CANCELED
            if failure.kind is FailureKind.CANCELED
            else ProcedureStatus.FAILED
        )
        result = state.result(
            status, failure=failure, ended_at=datetime.now(timezone.utc)
        )
        self._recorder.checkpoint(result)
        return result

    def _preflight(self, state: _RunState) -> None:
        preflight = self._bench.preflight_dual()
        state.preflight = preflight
        self._record_event(state, "preflight", "Dual-sensor bench preflight completed.")
        self._checkpoint(state)
        topology = validate_exact_dual_topology(preflight.topology)
        if not topology.passed:
            raise _ProcedureFailure(FailureKind.SETUP, topology.detail)
        if not preflight.console_responsive:
            raise _ProcedureFailure(
                FailureKind.SETUP, "Console must be responsive before continuing."
            )
        for identity, label in (
            (preflight.console_identity, "Console"),
            (preflight.left_sensor_identity, "Left-sensor"),
            (preflight.right_sensor_identity, "Right-sensor"),
        ):
            if not validate_serial(identity.serial).passed:
                raise _ProcedureFailure(
                    FailureKind.SETUP, f"{label} serial must be nonblank text."
                )
        if not preflight.ophir_ready:
            raise _ProcedureFailure(
                FailureKind.SETUP,
                preflight.ophir_failure_reason or "Ophir preflight failed.",
            )
        if preflight.ophir_identity is None:
            raise _ProcedureFailure(
                FailureKind.SETUP, "Ophir identity must be present."
            )
        if not self._has_complete_ophir_identity(preflight.ophir_identity):
            raise _ProcedureFailure(
                FailureKind.SETUP, "Ophir identity fields must be nonblank text."
            )
        if not self._has_valid_ophir_setting_evidence(
            preflight.ophir_setting_evidence
        ):
            raise _ProcedureFailure(
                FailureKind.SETUP,
                "Ophir setting evidence is incomplete or invalid.",
            )

    def _establish_defaults(self, state: _RunState) -> None:
        approved = default_user_configuration()
        state.pre_existing_config = dict(self._bench.read_user_configuration())
        self._checkpoint(state)
        state.requested_default_config = dict(approved)
        self._checkpoint(state)
        try:
            topology = self._bench.revalidate_dual_topology()
        except Exception as error:
            raise _ProcedureFailure(
                FailureKind.SETUP,
                "Dual topology revalidation failed before configuration mutation.",
            ) from error
        state.topology_revalidation = topology
        if not validate_exact_dual_topology(topology).passed:
            raise _ProcedureFailure(
                FailureKind.SETUP,
                "Exact dual-sensor topology changed before configuration mutation.",
            )
        immediate = self._bench.write_user_configuration(dict(approved))
        if not isinstance(immediate, Mapping):
            raise _ProcedureFailure(
                FailureKind.CONFIGURATION,
                "Default User Configuration write did not return a complete readback mapping.",
            )
        state.default_config_readback = dict(immediate)
        self._checkpoint(state)
        if state.default_config_readback != state.requested_default_config:
            raise _ProcedureFailure(
                FailureKind.CONFIGURATION,
                "Default User Configuration readback must exactly match the request.",
            )
        self._record_event(
            state,
            "default_configuration",
            "Exact default User Configuration was written and read back.",
        )
        self._checkpoint(state)
        self._bench.bring_up_laser_configuration()
        self._record_event(
            state,
            "laser_configuration_bringup",
            "Laser configuration bring-up completed.",
        )
        self._checkpoint(state)
        self._verify_active_defaults(state, approved)
        self._verify_trigger_rate(state)
        state.current_requested_ma = float(approved["TA_CURRENT_DRV"])
        state.pulse_requested_us = float(approved["TA_PULSE_WIDTH"])
        state.active_defaults_established = True

    def _measure_pair(
        self, state: _RunState, *, phase: str, pair_label: str
    ) -> PairObservation:
        left = self._measure_side(state, "left", phase)
        right = self._measure_side(state, "right", phase)
        pair = PairObservation(
            label=pair_label,
            left=left,
            right=right,
            metrics=calculate_pair_metrics(
                left.measurement.mean_uj, right.measurement.mean_uj
            ),
        )
        return pair

    def _measure_side(
        self, state: _RunState, side: SensorSide, phase: str
    ) -> SensorEnergyObservation:
        self._acknowledge_placement_if_needed(state, side, phase)
        label = f"{phase} — {side} sensor"
        try:
            measurement = self._bench.measure_energy()
        except Exception as error:
            raise _ProcedureFailure(
                FailureKind.MEASUREMENT,
                f"{label} acquisition failed.",
            ) from error
        finally:
            try:
                self._bench.stop_trigger()
            except Exception as error:
                state.trigger_cleanup_failure = "Trigger stop failed."
                raise _ProcedureFailure(
                    FailureKind.MEASUREMENT, state.trigger_cleanup_failure
                ) from error
        serial = self._sensor_serial(state, side)
        raw = SensorEnergyObservation(side, serial, label, measurement, ())
        state.observations.append(raw)
        self._checkpoint(state)
        criteria = validate_energy_measurement(measurement)
        observation = SensorEnergyObservation(
            side, serial, label, measurement, criteria
        )
        state.observations[-1] = observation
        self._checkpoint(state)
        if not all(item.passed for item in criteria):
            raise _ProcedureFailure(
                FailureKind.MEASUREMENT,
                f"{label} failed the WI-00015 measurement-quality criteria.",
            )
        self._record_event(
            state,
            "measurement",
            f"{label} completed with valid Ophir evidence.",
        )
        return observation

    def _acknowledge_placement_if_needed(
        self, state: _RunState, side: SensorSide, phase: str
    ) -> None:
        if self._seated_side == side:
            return
        request = PlacementChangeRequest(
            from_side=self._seated_side,
            to_side=side,
            sensor_serial=self._sensor_serial(state, side),
            phase=phase,
            label=f"{phase} — place the {side} sensor in the Ophir 0 cm fixture",
        )
        try:
            response = self._placement_callback(request)
        except (EOFError, KeyboardInterrupt) as error:
            raise _ProcedureFailure(
                FailureKind.CANCELED,
                f"Operator canceled the requested switch to the {side} sensor.",
            ) from error
        except Exception as error:
            raise _ProcedureFailure(
                FailureKind.CANCELED,
                f"Placement acknowledgement failed for the {side} sensor.",
            ) from error
        acknowledgement = PlacementAcknowledgement(
            request=request,
            acknowledged=response is True,
            timestamp=datetime.now(timezone.utc),
        )
        state.placements.append(acknowledgement)
        self._checkpoint(state)
        if response is not True:
            raise _ProcedureFailure(
                FailureKind.CANCELED,
                f"Operator did not confirm the requested switch to the {side} sensor.",
            )
        self._seated_side = side
        self._record_event(
            state,
            "placement",
            f"Operator confirmed {phase.lower()} placement for the {side} sensor "
            f"(serial {request.sensor_serial}).",
        )

    def _tune_from_pair(
        self, state: _RunState, pair: PairObservation, round_number: int
    ) -> TuningRound:
        midpoint = pair.metrics.midpoint_uj
        if midpoint == TARGET_ENERGY_UJ:
            reason = "the paired midpoint was already exactly 350 uJ"
            selection = TuningSelection(
                direction="none",
                selected_side=None,
                target_uj=TARGET_ENERGY_UJ,
                requested_current_ma=state.current_requested_ma,
                requested_pulse_width_us=state.pulse_requested_us,
                selected_mean_uj=midpoint,
                rationale="No setting changed because the paired midpoint was already exactly 350 uJ.",
            )
            tuning_round = TuningRound(
                number=round_number,
                label=f"Midpoint adjustment round {round_number} — no setting changed because {reason}.",
                input_pair=pair,
                direction="none",
                selected_side=None,
                reason=reason,
                target_uj=TARGET_ENERGY_UJ,
                steps=(),
                selection=selection,
            )
            state.tuning_rounds.append(tuning_round)
            self._checkpoint(state)
            return tuning_round
        if midpoint > TARGET_ENERGY_UJ:
            return self._tune_downward(state, pair, round_number)
        return self._tune_upward(state, pair, round_number)

    def _tune_downward(
        self, state: _RunState, pair: PairObservation, round_number: int
    ) -> TuningRound:
        side: SensorSide = (
            "left"
            if pair.left.measurement.mean_uj >= pair.right.measurement.mean_uj
            else "right"
        )
        source = pair.left if side == "left" else pair.right
        target = TARGET_ENERGY_UJ + pair.metrics.difference_uj / 2.0
        reason = f"the {side} sensor had the higher energy reading"
        tuning_round = TuningRound(
            number=round_number,
            label=(
                f"Midpoint adjustment round {round_number} — selected {side} sensor "
                f"because it had the higher energy reading; target {target:g} uJ."
            ),
            input_pair=pair,
            direction="downward_current",
            selected_side=side,
            reason=reason,
            target_uj=target,
            steps=(),
            selection=None,
        )
        state.tuning_rounds.append(tuning_round)
        self._checkpoint(state)
        candidates = [(state.current_requested_ma, source.measurement)]
        steps: list[TuningStep] = []
        active_setting = state.current_requested_ma
        while active_setting > CURRENT_FLOOR_MA:
            requested = max(CURRENT_FLOOR_MA, active_setting - CURRENT_STEP_MA)
            readback = self._checked_register_write(
                state, "TA_CURRENT_DRV", requested
            )
            observation = self._measure_side(
                state,
                side,
                f"Midpoint adjustment round {round_number}, step {len(steps) + 1}",
            )
            step = TuningStep(
                number=len(steps) + 1,
                label=(
                    f"Adjustment step {len(steps) + 1} — reduced TA current "
                    f"from {active_setting:g} mA to {requested:g} mA for the {side} sensor"
                ),
                side=side,
                register_name="TA_CURRENT_DRV",
                requested_value=requested,
                readback=readback,
                observation=observation,
            )
            steps.append(step)
            tuning_round = replace(tuning_round, steps=tuple(steps))
            state.tuning_rounds[-1] = tuning_round
            candidates.append((requested, observation.measurement))
            self._checkpoint(state)
            active_setting = requested
            if observation.measurement.mean_uj <= target:
                break
        selected = select_closest_valid_setting_to_target(candidates, target)
        if selected is None:
            raise _ProcedureFailure(
                FailureKind.MEASUREMENT,
                "No valid downward-current candidate was available for selection.",
            )
        selected_setting, selected_measurement = selected
        if (
            active_setting == CURRENT_FLOOR_MA
            and not both_energies_accepted(
                selected_measurement.mean_uj, selected_measurement.mean_uj
            )
        ):
            raise _ProcedureFailure(
                FailureKind.NCR,
                "The current floor was reached without an acceptable selected-sensor setting.",
            )
        if selected_setting != active_setting:
            self._checked_register_write(
                state, "TA_CURRENT_DRV", selected_setting
            )
        state.current_requested_ma = selected_setting
        selection = TuningSelection(
            direction="downward_current",
            selected_side=side,
            target_uj=target,
            requested_current_ma=state.current_requested_ma,
            requested_pulse_width_us=state.pulse_requested_us,
            selected_mean_uj=selected_measurement.mean_uj,
            rationale=(
                f"Selected {selected_setting:g} mA because its valid {side}-sensor "
                f"reading was closest to the calculated {target:g} uJ target."
            ),
        )
        tuning_round = replace(tuning_round, selection=selection)
        state.tuning_rounds[-1] = tuning_round
        self._checkpoint(state)
        return tuning_round

    def _tune_upward(
        self, state: _RunState, pair: PairObservation, round_number: int
    ) -> TuningRound:
        side: SensorSide = (
            "left"
            if pair.left.measurement.mean_uj <= pair.right.measurement.mean_uj
            else "right"
        )
        source = pair.left if side == "left" else pair.right
        target = TARGET_ENERGY_UJ - pair.metrics.difference_uj / 2.0
        reason = f"the {side} sensor had the lower energy reading"
        tuning_round = TuningRound(
            number=round_number,
            label=(
                f"Midpoint adjustment round {round_number} — selected {side} sensor "
                f"because it had the lower energy reading; target {target:g} uJ."
            ),
            input_pair=pair,
            direction="upward_pulse",
            selected_side=side,
            reason=reason,
            target_uj=target,
            steps=(),
            selection=None,
        )
        state.tuning_rounds.append(tuning_round)
        self._checkpoint(state)
        if (
            state.pulse_requested_us >= MAX_PULSE_WIDTH_US
            and source.measurement.mean_uj < 300.0
        ):
            raise _ProcedureFailure(
                FailureKind.NCR,
                "Energy remained below 300 uJ at the 600 us pulse-width ceiling.",
            )
        if not state.used_upward_tuning:
            self._checked_register_write(
                state, "EE_PULSE_WIDTH_UL", TEMPORARY_PULSE_WIDTH_LIMIT_US
            )
            self._checked_register_write(
                state, "OPT_PULSE_WIDTH_UL", TEMPORARY_PULSE_WIDTH_LIMIT_US
            )
            state.used_upward_tuning = True
        candidates = [(state.pulse_requested_us, source.measurement)]
        steps: list[TuningStep] = []
        active_setting = state.pulse_requested_us
        while active_setting < MAX_PULSE_WIDTH_US:
            requested = min(
                MAX_PULSE_WIDTH_US, active_setting + PULSE_WIDTH_STEP_US
            )
            readback = self._checked_register_write(
                state, "TA_PULSE_WIDTH", requested
            )
            observation = self._measure_side(
                state,
                side,
                f"Midpoint adjustment round {round_number}, step {len(steps) + 1}",
            )
            step = TuningStep(
                number=len(steps) + 1,
                label=(
                    f"Adjustment step {len(steps) + 1} — increased TA pulse width "
                    f"from {active_setting:g} us to {requested:g} us for the {side} sensor"
                ),
                side=side,
                register_name="TA_PULSE_WIDTH",
                requested_value=requested,
                readback=readback,
                observation=observation,
            )
            steps.append(step)
            tuning_round = replace(tuning_round, steps=tuple(steps))
            state.tuning_rounds[-1] = tuning_round
            candidates.append((requested, observation.measurement))
            self._checkpoint(state)
            active_setting = requested
            if (
                active_setting == MAX_PULSE_WIDTH_US
                and observation.measurement.mean_uj < 300.0
            ):
                raise _ProcedureFailure(
                    FailureKind.NCR,
                    "Energy remained below 300 uJ at the 600 us pulse-width ceiling.",
                )
            if observation.measurement.mean_uj >= target:
                break
        selected = select_closest_valid_setting_to_target(candidates, target)
        if selected is None:
            raise _ProcedureFailure(
                FailureKind.MEASUREMENT,
                "No valid upward-pulse candidate was available for selection.",
            )
        selected_setting, selected_measurement = selected
        if selected_setting != active_setting:
            self._checked_register_write(state, "TA_PULSE_WIDTH", selected_setting)
        state.pulse_requested_us = selected_setting
        selection = TuningSelection(
            direction="upward_pulse",
            selected_side=side,
            target_uj=target,
            requested_current_ma=state.current_requested_ma,
            requested_pulse_width_us=state.pulse_requested_us,
            selected_mean_uj=selected_measurement.mean_uj,
            rationale=(
                f"Selected {selected_setting:g} us because its valid {side}-sensor "
                f"reading was closest to the calculated {target:g} uJ target."
            ),
        )
        tuning_round = replace(tuning_round, selection=selection)
        state.tuning_rounds[-1] = tuning_round
        self._checkpoint(state)
        return tuning_round

    def _write_passing_configuration(self, state: _RunState) -> None:
        for name, requested in (
            ("TA_CURRENT_DRV", state.current_requested_ma),
            ("TA_PULSE_WIDTH", state.pulse_requested_us),
        ):
            try:
                actual = self._bench.read_register(name)
            except Exception as error:
                raise _ProcedureFailure(
                    FailureKind.CONFIGURATION,
                    f"Final active {name} readback failed.",
                ) from error
            state.configurations.append(SettingReadback(name, requested, actual))
            passed = within_percent(requested, actual, 2.0)
            state.final_setting_checks.append(
                FinalSettingCheck(
                    name=name,
                    requested=requested,
                    actual=actual,
                    absolute_difference=abs(actual - requested),
                    percent_difference=percent_difference(requested, actual),
                    tolerance_percent=2.0,
                    passed=passed,
                )
            )
            self._checkpoint(state)
            if not passed:
                raise _ProcedureFailure(
                    FailureKind.CONFIGURATION,
                    f"Final active {name} is outside the allowed 2 percent tolerance.",
                )
        requested = default_user_configuration()
        requested["TA_CURRENT_DRV"] = state.current_requested_ma
        requested["TA_PULSE_WIDTH"] = state.pulse_requested_us
        if state.used_upward_tuning:
            requested["EE_PULSE_WIDTH_UL"] = TEMPORARY_PULSE_WIDTH_LIMIT_US
            requested["OPT_PULSE_WIDTH_UL"] = TEMPORARY_PULSE_WIDTH_LIMIT_US
        state.requested_final_config = requested
        self._checkpoint(state)
        try:
            immediate = self._bench.write_user_configuration(dict(requested))
        except Exception as error:
            raise _ProcedureFailure(
                FailureKind.CONFIGURATION,
                "Passing User Configuration write or readback failed.",
            ) from error
        if not isinstance(immediate, Mapping):
            raise _ProcedureFailure(
                FailureKind.CONFIGURATION,
                "Passing User Configuration write did not return a complete readback mapping.",
            )
        state.final_config_readback = dict(immediate)
        self._checkpoint(state)
        if state.final_config_readback != state.requested_final_config:
            raise _ProcedureFailure(
                FailureKind.CONFIGURATION,
                "Passing User Configuration readback must exactly match the request.",
            )
        self._record_event(
            state,
            "final_configuration",
            "Final configuration verification — passing tuned User Configuration was written and read back exactly.",
        )

    def _verify_active_defaults(
        self, state: _RunState, approved: Mapping[str, float]
    ) -> None:
        for name in (
            "TA_CURRENT_DRV",
            "TA_PULSE_WIDTH",
            "SEED_CW_GAIN",
            "EE_PULSE_WIDTH_UL",
            "OPT_PULSE_WIDTH_UL",
        ):
            requested = approved[name]
            actual = self._bench.read_register(name)
            state.configurations.append(SettingReadback(name, requested, actual))
            self._checkpoint(state)
            if not within_percent(requested, actual, 2.0):
                raise _ProcedureFailure(
                    FailureKind.CONFIGURATION,
                    f"Active {name} is outside the allowed 2 percent tolerance.",
                )

    def _verify_trigger_rate(self, state: _RunState) -> None:
        rate_hz = self._bench.read_trigger_rate_hz()
        state.configurations.append(
            SettingReadback("trigger_rate_hz_initial", 40.0, rate_hz)
        )
        self._checkpoint(state)
        if rate_hz != 40.0:
            write_result = self._bench.write_trigger_rate_hz(40.0)
            if not isinstance(write_result, SettingReadback):
                raise _ProcedureFailure(
                    FailureKind.CONFIGURATION,
                    "Trigger-rate correction returned malformed readback evidence.",
                )
            state.configurations.append(write_result)
            self._checkpoint(state)
            if (
                write_result.name != "trigger_rate_hz_write"
                or write_result.requested != 40.0
                or not math.isfinite(write_result.actual)
                or write_result.actual != 40.0
            ):
                raise _ProcedureFailure(
                    FailureKind.CONFIGURATION,
                    "Trigger-rate correction immediate readback must exactly match 40 Hz.",
                )
            rate_hz = self._bench.read_trigger_rate_hz()
        state.configurations.append(SettingReadback("trigger_rate_hz", 40.0, rate_hz))
        self._checkpoint(state)
        if not self._valid_trigger_rate(rate_hz):
            raise _ProcedureFailure(
                FailureKind.CONFIGURATION,
                "Active trigger rate must be between 39 and 41 Hz inclusive.",
            )

    def _checked_register_write(
        self, state: _RunState, name: str, requested: float
    ) -> SettingReadback:
        try:
            result = self._bench.write_register(name, requested)
        except Exception as error:
            raise _ProcedureFailure(
                FailureKind.CONFIGURATION, f"Active {name} write failed."
            ) from error
        if not isinstance(result, SettingReadback):
            raise _ProcedureFailure(
                FailureKind.CONFIGURATION,
                f"Active {name} write returned malformed readback evidence.",
            )
        state.adjustments.append(result)
        self._checkpoint(state)
        if result.name != name or result.requested != requested:
            raise _ProcedureFailure(
                FailureKind.CONFIGURATION,
                f"Active {name} write returned mismatched readback identity.",
            )
        if not within_percent(requested, result.actual, 2.0):
            raise _ProcedureFailure(
                FailureKind.CONFIGURATION,
                f"Active {name} is outside the allowed 2 percent tolerance.",
            )
        return result

    def _restore_active_defaults(self, state: _RunState) -> None:
        failures: list[str] = []
        approved = default_user_configuration()
        for name in (
            "TA_CURRENT_DRV",
            "TA_PULSE_WIDTH",
            "SEED_CW_GAIN",
            "EE_PULSE_WIDTH_UL",
            "OPT_PULSE_WIDTH_UL",
        ):
            requested = approved[name]
            try:
                result = self._bench.write_register(name, requested)
                if not isinstance(result, SettingReadback):
                    failures.append(f"{name} restore returned malformed evidence")
                else:
                    state.active_default_restore.append(result)
                    if result.name != name or result.requested != requested:
                        failures.append(f"{name} restore identity did not match")
                    elif not within_percent(requested, result.actual, 2.0):
                        failures.append(f"{name} restore was outside 2 percent")
            except Exception:
                failures.append(f"{name} restore raised an exception")
            state.active_default_restore_failure = "; ".join(failures) or None
            self._checkpoint(state)
        self._record_event(
            state,
            "active_default_restore",
            state.active_default_restore_failure
            or "Active default laser registers were restored after the failed run.",
        )

    def _checkpoint(self, state: _RunState) -> None:
        self._recorder.checkpoint(state.result(ProcedureStatus.IN_PROGRESS))

    def _record_event(self, state: _RunState, stage: str, message: str) -> None:
        event = ProcedureEvent(datetime.now(timezone.utc), stage, message)
        state.events.append(event)
        self._recorder.record(event)

    @staticmethod
    def _crosscheck_label(number: int, accepted: bool) -> str:
        if accepted:
            return (
                f"Cross-check {number} — paired result: both sensors were within "
                "the approved 300-400 uJ range."
            )
        return (
            f"Cross-check {number} — paired result: at least one sensor was outside "
            "the approved 300-400 uJ range."
        )

    @staticmethod
    def _valid_trigger_rate(rate_hz: object) -> bool:
        try:
            return math.isfinite(float(rate_hz)) and 39.0 <= float(rate_hz) <= 41.0
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _has_complete_ophir_identity(identity: OphirIdentity) -> bool:
        return all(
            isinstance(value, str) and bool(value.strip())
            for value in (
                identity.meter_model,
                identity.meter_serial,
                identity.sensor_model,
                identity.sensor_serial,
                identity.calibration_due,
            )
        )

    @staticmethod
    def _has_valid_ophir_setting_evidence(
        evidence: tuple[OphirSettingEvidence, ...],
    ) -> bool:
        expected = {
            "measurement_mode": ("Energy", OphirEvidenceApplicability.APPLICABLE),
            "range_mj": (2.0, OphirEvidenceApplicability.APPLICABLE),
            "wavelength_nm": (795, OphirEvidenceApplicability.APPLICABLE),
            "pulse_length_ms": (1.0, OphirEvidenceApplicability.APPLICABLE),
            "threshold": (
                "minimum_available",
                OphirEvidenceApplicability.APPLICABLE,
            ),
            "display_averaging_s": (3, None),
            "graph_mode": ("Statistics", None),
        }
        if len(evidence) != len(expected):
            return False
        by_name = {item.name: item for item in evidence}
        if len(by_name) != len(evidence) or set(by_name) != set(expected):
            return False
        for name, (requested, required_applicability) in expected.items():
            item = by_name[name]
            if item.requested != requested or not item.passed:
                return False
            if required_applicability is not None:
                if (
                    item.applicability is not required_applicability
                    or item.actual != requested
                ):
                    return False
            elif (
                item.applicability is not OphirEvidenceApplicability.NOT_APPLICABLE
                or item.actual is not None
            ):
                return False
        return True

    @staticmethod
    def _sensor_serial(state: _RunState, side: SensorSide) -> str:
        assert state.preflight is not None
        serial = (
            state.preflight.left_sensor_identity.serial
            if side == "left"
            else state.preflight.right_sensor_identity.serial
        )
        assert isinstance(serial, str) and serial.strip()
        return serial.strip()
