"""UI-neutral workflow shell for WI-00015 single-sensor laser calibration."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from pathlib import Path
from typing import Mapping, Protocol

try:
    from omotion import __version__ as _RUNTIME_SDK_VERSION
except (ImportError, AttributeError):
    _RUNTIME_SDK_VERSION = "unavailable"

from .laser import (
    CURRENT_FLOOR_MA,
    CURRENT_STEP_MA,
    MAX_ACCEPTABLE_ENERGY_UJ,
    MAX_PULSE_WIDTH_US,
    MIN_ACCEPTABLE_ENERGY_UJ,
    PULSE_WIDTH_STEP_US,
    TARGET_ENERGY_UJ,
    TEMPORARY_PULSE_WIDTH_LIMIT_US,
    CriterionResult,
    DeviceIdentity,
    EnergyMeasurement,
    FailureKind,
    FinalSettingCheck,
    OphirIdentity,
    ProcedureStatus,
    SensorSide,
    SettingReadback,
    TopologySnapshot,
    select_closest_valid_setting_to_target,
    validate_energy_measurement,
    validate_exact_single_topology,
)
from ._procedure import (
    LaserBench,
    LaserWorkflowBase,
    OphirEvidenceApplicability,
    OphirSettingEvidence,
    ProcedureEvent,
    ProcedureFailure,
    ReportArtifactEvidence,
    ReportArtifactStatus,
    RunRecorder,
    deeply_immutable,
)

__all__ = [
    "LaserCalibrationBench",
    "OphirEvidenceApplicability",
    "OphirSettingEvidence",
    "PreflightSnapshot",
    "ProcedureEvent",
    "ReportArtifactEvidence",
    "ReportArtifactStatus",
    "RunRecorder",
    "SingleSensorLaserCalibrationRequest",
    "SingleSensorLaserCalibrationResult",
    "SingleSensorLaserCalibrationWorkflow",
    "TuningCandidate",
    "TuningSelection",
]


def _measurement_quality_failure_reason(
    phase: str,
    measurement: EnergyMeasurement,
    criteria: tuple[CriterionResult, ...],
) -> str:
    failed = "; ".join(
        f"{criterion.name} ({criterion.detail})"
        for criterion in criteria
        if not criterion.passed
    )
    return (
        f"{phase} energy measurement failed quality criteria: {failed}. "
        "Observed "
        f"n={measurement.n}, discarded={measurement.discarded}, "
        f"mean={measurement.mean_uj:.6g} uJ, "
        f"stdev={measurement.stdev_uj:.6g} uJ, "
        f"rate={measurement.rate_hz:.6g} Hz, "
        f"duration={measurement.duration_s:.6g} s."
    )


@dataclass(frozen=True)
class SingleSensorLaserCalibrationRequest:
    side: str | None
    side_confirmed: bool
    fixture_confirmed: bool
    operator: str
    build_id: str
    fixture_id: str
    procedure_id: str
    output_root: Path | str
    run_id: str
    fixture_calibration_status: str | None = None
    sdk_version: str = _RUNTIME_SDK_VERSION
    started_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass(frozen=True)
class PreflightSnapshot:
    topology: TopologySnapshot
    console_identity: DeviceIdentity
    selected_sensor_identity: DeviceIdentity
    console_responsive: bool
    ophir_identity: OphirIdentity | None
    ophir_ready: bool
    ophir_setting_evidence: tuple[OphirSettingEvidence, ...]
    ophir_failure_reason: str | None = None


@dataclass(frozen=True)
class TuningCandidate:
    requested_current_ma: float
    actual_current_ma: float
    requested_pulse_width_us: float
    actual_pulse_width_us: float
    measurement: EnergyMeasurement


@dataclass(frozen=True)
class TuningSelection:
    direction: str
    decision_kind: str
    accepted: bool
    selected_requested_current_ma: float
    selected_requested_pulse_width_us: float
    selected_mean_uj: float
    rationale: str


@dataclass(frozen=True)
class SingleSensorLaserCalibrationResult:
    status: ProcedureStatus
    side: SensorSide | None
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
    measurements: tuple[EnergyMeasurement, ...] = ()
    measurement_criteria: tuple[tuple[CriterionResult, ...], ...] = ()
    trigger_cleanup_failure: str | None = None
    adjustments: tuple[SettingReadback, ...] = ()
    candidates: tuple[TuningCandidate, ...] = ()
    selection: TuningSelection | None = None
    requested_final_config: Mapping[str, float] | None = None
    final_config_readback: Mapping[str, float] | None = None
    final_setting_checks: tuple[FinalSettingCheck, ...] = ()
    active_default_restore: tuple[SettingReadback, ...] = ()
    active_default_restore_failure: str | None = None
    events: tuple[ProcedureEvent, ...] = ()
    target_energy_uj: float = TARGET_ENERGY_UJ
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
                object.__setattr__(self, name, deeply_immutable(value))


class LaserCalibrationBench(LaserBench, Protocol):
    def preflight(self, side: SensorSide) -> PreflightSnapshot: ...

    def revalidate_topology(self, side: SensorSide) -> TopologySnapshot: ...


@dataclass
class _RunState:
    request: SingleSensorLaserCalibrationRequest
    target_energy_uj: float = TARGET_ENERGY_UJ
    side: SensorSide | None = None
    preflight: PreflightSnapshot | None = None
    topology_revalidation: TopologySnapshot | None = None
    pre_existing_config: Mapping[str, float] | None = None
    requested_default_config: Mapping[str, float] | None = None
    default_config_readback: Mapping[str, float] | None = None
    configurations: list[SettingReadback] = field(default_factory=list)
    measurements: list[EnergyMeasurement] = field(default_factory=list)
    measurement_criteria: list[tuple[CriterionResult, ...]] = field(
        default_factory=list
    )
    adjustments: list[SettingReadback] = field(default_factory=list)
    candidates: list[TuningCandidate] = field(default_factory=list)
    selection: TuningSelection | None = None
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
    trigger_stopped: bool = False

    def result(
        self,
        status: ProcedureStatus,
        *,
        failure: ProcedureFailure | None = None,
        ended_at: datetime | None = None,
    ) -> SingleSensorLaserCalibrationResult:
        preflight = self.preflight
        return SingleSensorLaserCalibrationResult(
            status=status,
            side=self.side,
            target_energy_uj=self.target_energy_uj,
            sdk_version=self.request.sdk_version,
            started_at=self.request.started_at,
            ended_at=ended_at,
            failure_kind=failure.kind if failure else None,
            failure_reason=failure.reason if failure else None,
            topology=preflight.topology if preflight else None,
            topology_revalidation=self.topology_revalidation,
            identities=(
                (preflight.console_identity, preflight.selected_sensor_identity)
                if preflight
                else ()
            ),
            ophir_identity=preflight.ophir_identity if preflight else None,
            ophir_setting_evidence=(
                preflight.ophir_setting_evidence if preflight else ()
            ),
            pre_existing_config=self.pre_existing_config,
            requested_default_config=self.requested_default_config,
            default_config_readback=self.default_config_readback,
            configurations=tuple(self.configurations),
            measurements=tuple(self.measurements),
            measurement_criteria=tuple(self.measurement_criteria),
            trigger_cleanup_failure=self.trigger_cleanup_failure,
            adjustments=tuple(self.adjustments),
            candidates=tuple(self.candidates),
            selection=self.selection,
            requested_final_config=self.requested_final_config,
            final_config_readback=self.final_config_readback,
            final_setting_checks=tuple(self.final_setting_checks),
            active_default_restore=tuple(self.active_default_restore),
            active_default_restore_failure=self.active_default_restore_failure,
            events=tuple(self.events),
        )


class SingleSensorLaserCalibrationWorkflow(LaserWorkflowBase):
    """Run fail-closed preflight before later configuration and firing phases."""

    def __init__(
        self,
        bench: LaserCalibrationBench,
        recorder: RunRecorder,
        *,
        target_energy_uj: float = TARGET_ENERGY_UJ,
    ):
        target_energy_uj = float(target_energy_uj)
        if not (
            math.isfinite(target_energy_uj)
            and MIN_ACCEPTABLE_ENERGY_UJ
            <= target_energy_uj
            <= MAX_ACCEPTABLE_ENERGY_UJ
        ):
            raise ValueError("target energy must be finite and between 300 and 400 uJ")
        self._bench = bench
        self._recorder = recorder
        self._target_energy_uj = target_energy_uj

    def run(
        self, request: SingleSensorLaserCalibrationRequest
    ) -> SingleSensorLaserCalibrationResult:
        state = _RunState(request=request, target_energy_uj=self._target_energy_uj)
        failure: ProcedureFailure | None = None
        configuration_started = False
        try:
            state.side = self._confirmed_side(request)
            self._record_event(
                state, "confirmation", "Operator confirmations accepted."
            )
            self._checkpoint(state)
            preflight = self._bench.preflight(state.side)
            state.preflight = preflight
            self._record_event(state, "preflight", "Bench preflight completed.")
            self._checkpoint(state)
            topology_result = validate_exact_single_topology(
                preflight.topology, state.side
            )
            if not topology_result.passed:
                raise ProcedureFailure(FailureKind.SETUP, topology_result.detail)
            self._validate_shared_preflight(
                preflight,
                (
                    (preflight.console_identity, "Console"),
                    (preflight.selected_sensor_identity, "Selected-sensor"),
                ),
            )
            configuration_started = True
            side = state.side
            self._establish_defaults(
                state,
                revalidate=lambda: self._bench.revalidate_topology(side),
                validate_topology=lambda topology: validate_exact_single_topology(
                    topology, side
                ),
                revalidation_failure=(
                    "Topology revalidation failed before configuration mutation."
                ),
                topology_changed_failure=(
                    "Exact single-sensor topology changed before configuration mutation."
                ),
            )
            state.measurement_started = True
            measurement = self._measure_once()
            state.trigger_stopped = True
            criteria = self._validated_measurement(state, measurement)
            if not all(criterion.passed for criterion in criteria):
                raise ProcedureFailure(
                    FailureKind.MEASUREMENT,
                    _measurement_quality_failure_reason(
                        "Initial", measurement, criteria
                    ),
                )
            self._record_event(
                state,
                "tuning",
                "Initial energy measurement passed quality criteria; tuning is next.",
            )
            state.candidates.append(
                TuningCandidate(
                    state.current_requested_ma,
                    state.configurations[0].actual,
                    state.pulse_requested_us,
                    state.configurations[1].actual,
                    measurement,
                )
            )
            self._checkpoint(state)
            if measurement.mean_uj == self._target_energy_uj:
                state.selection = TuningSelection(
                    "none",
                    "no_adjustment",
                    True,
                    state.current_requested_ma,
                    state.pulse_requested_us,
                    measurement.mean_uj,
                    f"The initial valid mean was exactly {self._target_energy_uj:g} uJ; "
                    "no adjustment was required.",
                )
            elif measurement.mean_uj > self._target_energy_uj:
                self._tune_downward(state)
            else:
                self._tune_upward(state)
            self._checkpoint(state)
            state.trigger_stopped = False
            final_measurement = self._measure_once()
            state.trigger_stopped = True
            final_criteria = self._validated_measurement(state, final_measurement)
            if not all(criterion.passed for criterion in final_criteria):
                raise ProcedureFailure(
                    FailureKind.MEASUREMENT,
                    _measurement_quality_failure_reason(
                        "Final", final_measurement, final_criteria
                    ),
                )
            if not (
                MIN_ACCEPTABLE_ENERGY_UJ
                <= final_measurement.mean_uj
                <= MAX_ACCEPTABLE_ENERGY_UJ
            ):
                raise ProcedureFailure(
                    FailureKind.NCR,
                    "Final energy must be between 300 and 400 uJ inclusive.",
                )
            self._write_passing_configuration(
                state,
                written_message=(
                    "Passing tuned User Configuration was written and read back exactly."
                ),
            )
            self._checkpoint(state)
        except ProcedureFailure as caught_failure:
            failure = caught_failure
        except Exception:
            if state.measurement_started:
                failure = ProcedureFailure(
                    FailureKind.MEASUREMENT, "Energy measurement failed."
                )
            elif configuration_started:
                failure = ProcedureFailure(
                    FailureKind.CONFIGURATION,
                    "Default configuration or active-setting check failed.",
                )
            else:
                failure = ProcedureFailure(FailureKind.SETUP, "Bench preflight failed.")
        finally:
            if not state.trigger_stopped:
                try:
                    self._bench.stop_trigger()
                except Exception:
                    state.trigger_cleanup_failure = "Trigger stop failed."
        if failure is None and state.trigger_cleanup_failure is not None:
            failure = ProcedureFailure(
                FailureKind.MEASUREMENT
                if state.measurement_started
                else FailureKind.SETUP,
                state.trigger_cleanup_failure,
            )
        if failure is None:
            result = state.result(
                ProcedureStatus.PASSED, ended_at=datetime.now(timezone.utc)
            )
            self._recorder.checkpoint(result)
            return result
        if state.active_defaults_established and state.measurement_started:
            self._restore_active_defaults(
                state,
                restored_message="Active default laser registers were restored.",
            )
        if state.trigger_cleanup_failure is not None:
            self._record_event(
                state, "trigger_cleanup", state.trigger_cleanup_failure
            )
        self._record_event(state, "failure", failure.reason)
        status = (
            ProcedureStatus.FAILED_NCR
            if failure.kind is FailureKind.NCR
            else ProcedureStatus.FAILED
        )
        result = state.result(
            status, failure=failure, ended_at=datetime.now(timezone.utc)
        )
        self._recorder.checkpoint(result)
        return result

    def _tune_downward(self, state: _RunState) -> None:
        current_setting = state.current_requested_ma
        while True:
            next_setting = max(CURRENT_FLOOR_MA, current_setting - CURRENT_STEP_MA)
            readback = self._checked_register_write(
                state, "TA_CURRENT_DRV", next_setting
            )
            candidate_measurement = self._tuning_measurement(state)
            state.candidates.append(
                TuningCandidate(
                    next_setting,
                    readback.actual,
                    state.pulse_requested_us,
                    state.configurations[1].actual,
                    candidate_measurement,
                )
            )
            self._checkpoint(state)
            current_setting = next_setting
            if (
                candidate_measurement.mean_uj <= self._target_energy_uj
                or current_setting == CURRENT_FLOOR_MA
            ):
                break
        selected = select_closest_valid_setting_to_target(
            (
                (candidate.requested_current_ma, candidate.measurement)
                for candidate in state.candidates
            ),
            self._target_energy_uj,
        )
        assert selected is not None
        selected_current, selected_measurement = selected
        accepted = (
            MIN_ACCEPTABLE_ENERGY_UJ
            <= selected_measurement.mean_uj
            <= MAX_ACCEPTABLE_ENERGY_UJ
        )
        state.selection = TuningSelection(
            "downward_current",
            "closest_candidate",
            accepted,
            selected_current,
            state.pulse_requested_us,
            selected_measurement.mean_uj,
            (
                "Selected the valid requested current closest to "
                f"{self._target_energy_uj:g} uJ; "
                "lower requested setting wins a tie."
                if accepted
                else "The closest valid requested current was outside the "
                "accepted 300 to 400 uJ range; lower requested setting "
                "wins a tie."
            ),
        )
        self._checkpoint(state)
        if not accepted:
            raise ProcedureFailure(
                FailureKind.NCR,
                "No downward-current candidate is within 300 to 400 uJ.",
            )
        if selected_current != current_setting:
            self._checked_register_write(state, "TA_CURRENT_DRV", selected_current)
        state.current_requested_ma = selected_current

    def _tune_upward(self, state: _RunState) -> None:
        state.used_upward_tuning = True
        self._checked_register_write(
            state, "EE_PULSE_WIDTH_UL", TEMPORARY_PULSE_WIDTH_LIMIT_US
        )
        self._checked_register_write(
            state, "OPT_PULSE_WIDTH_UL", TEMPORARY_PULSE_WIDTH_LIMIT_US
        )
        pulse_setting = state.pulse_requested_us
        while True:
            next_setting = min(MAX_PULSE_WIDTH_US, pulse_setting + PULSE_WIDTH_STEP_US)
            readback = self._checked_register_write(
                state, "TA_PULSE_WIDTH", next_setting
            )
            candidate_measurement = self._tuning_measurement(state)
            state.candidates.append(
                TuningCandidate(
                    state.current_requested_ma,
                    state.configurations[0].actual,
                    next_setting,
                    readback.actual,
                    candidate_measurement,
                )
            )
            self._checkpoint(state)
            pulse_setting = next_setting
            if (
                pulse_setting == MAX_PULSE_WIDTH_US
                and candidate_measurement.mean_uj < MIN_ACCEPTABLE_ENERGY_UJ
            ):
                state.selection = TuningSelection(
                    "upward_pulse",
                    "bound",
                    False,
                    state.current_requested_ma,
                    pulse_setting,
                    candidate_measurement.mean_uj,
                    "The 600 us pulse-width bound was reached with energy "
                    "below 300 uJ, so closest-candidate selection was "
                    "intentionally bypassed.",
                )
                self._checkpoint(state)
                raise ProcedureFailure(
                    FailureKind.NCR,
                    "Energy remained below 300 uJ at the 600 us pulse-width ceiling.",
                )
            if (
                candidate_measurement.mean_uj >= self._target_energy_uj
                or pulse_setting == MAX_PULSE_WIDTH_US
            ):
                break
        selected = select_closest_valid_setting_to_target(
            (
                (candidate.requested_pulse_width_us, candidate.measurement)
                for candidate in state.candidates
            ),
            self._target_energy_uj,
        )
        assert selected is not None
        selected_pulse, selected_measurement = selected
        accepted = (
            MIN_ACCEPTABLE_ENERGY_UJ
            <= selected_measurement.mean_uj
            <= MAX_ACCEPTABLE_ENERGY_UJ
        )
        state.selection = TuningSelection(
            "upward_pulse",
            "closest_candidate",
            accepted,
            state.current_requested_ma,
            selected_pulse,
            selected_measurement.mean_uj,
            (
                "Selected the valid requested pulse width closest to "
                f"{self._target_energy_uj:g} uJ; "
                "lower requested setting wins a tie."
                if accepted
                else "The closest valid requested pulse width was outside the "
                "accepted 300 to 400 uJ range; lower requested setting wins "
                "a tie."
            ),
        )
        self._checkpoint(state)
        if not accepted:
            raise ProcedureFailure(
                FailureKind.NCR,
                "No upward-pulse candidate is within 300 to 400 uJ.",
            )
        if selected_pulse != pulse_setting:
            self._checked_register_write(state, "TA_PULSE_WIDTH", selected_pulse)
        state.pulse_requested_us = selected_pulse

    def _tuning_measurement(self, state: _RunState) -> EnergyMeasurement:
        """One mid-tuning measurement that must itself pass quality criteria."""
        state.trigger_stopped = False
        candidate_measurement = self._measure_once()
        state.trigger_stopped = True
        candidate_criteria = self._validated_measurement(state, candidate_measurement)
        if not all(item.passed for item in candidate_criteria):
            raise ProcedureFailure(
                FailureKind.MEASUREMENT,
                _measurement_quality_failure_reason(
                    "Adjustment", candidate_measurement, candidate_criteria
                ),
            )
        return candidate_measurement

    @staticmethod
    def _confirmed_side(request: SingleSensorLaserCalibrationRequest) -> SensorSide:
        if request.side not in ("left", "right") or not request.side_confirmed:
            raise ProcedureFailure(
                FailureKind.SETUP,
                "Confirm the selected sensor side before continuing.",
            )
        if not request.fixture_confirmed:
            raise ProcedureFailure(
                FailureKind.SETUP,
                "Confirm fixture placement before continuing.",
            )
        return request.side

    def _measure_once(self) -> EnergyMeasurement:
        try:
            return self._bench.measure_energy()
        finally:
            self._bench.stop_trigger()

    def _validated_measurement(
        self, state: _RunState, measurement: EnergyMeasurement
    ) -> tuple[CriterionResult, ...]:
        state.measurements.append(measurement)
        self._checkpoint(state)
        criteria = validate_energy_measurement(measurement)
        state.measurement_criteria.append(criteria)
        self._checkpoint(state)
        return criteria
