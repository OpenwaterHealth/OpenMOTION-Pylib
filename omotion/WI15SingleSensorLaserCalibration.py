"""UI-neutral workflow shell for WI-00015 single-sensor laser calibration."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Mapping, Protocol

from omotion.WI15LaserCalibration import (
    DEFAULT_USER_CONFIG,
    CURRENT_FLOOR_MA,
    CURRENT_STEP_MA,
    MAX_ACCEPTABLE_ENERGY_UJ,
    MAX_PULSE_WIDTH_US,
    MIN_ACCEPTABLE_ENERGY_UJ,
    PULSE_WIDTH_STEP_US,
    TEMPORARY_PULSE_WIDTH_LIMIT_US,
    CriterionResult,
    DeviceIdentity,
    EnergyMeasurement,
    FailureKind,
    OphirIdentity,
    ProcedureStatus,
    SensorSide,
    SettingReadback,
    TopologySnapshot,
    validate_energy_measurement,
    validate_exact_single_topology,
    validate_serial,
    select_closest_valid_setting,
    within_percent,
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


@dataclass(frozen=True)
class ProcedureEvent:
    timestamp: datetime
    stage: str
    message: str
    data: Mapping[str, object] = field(default_factory=dict)


class OphirEvidenceApplicability(str, Enum):
    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class OphirSettingEvidence:
    name: str
    requested: str | float | int
    actual: str | float | int | None
    applicability: OphirEvidenceApplicability
    passed: bool


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
    failure_kind: FailureKind | None = None
    failure_reason: str | None = None
    topology: TopologySnapshot | None = None
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
    active_default_restore: tuple[SettingReadback, ...] = ()
    active_default_restore_failure: str | None = None
    events: tuple[ProcedureEvent, ...] = ()
    report_paths: tuple[Path | str, ...] = ()


class LaserCalibrationBench(Protocol):
    def preflight(self, side: SensorSide) -> PreflightSnapshot: ...

    def read_user_configuration(self) -> Mapping[str, float]: ...

    def write_user_configuration(
        self, configuration: Mapping[str, float]
    ) -> Mapping[str, float] | None: ...

    def bring_up_laser_configuration(self) -> None: ...

    def read_register(self, name: str) -> float: ...

    def write_register(self, name: str, value: float) -> SettingReadback | None: ...

    def read_trigger_rate_hz(self) -> float: ...

    def write_trigger_rate_hz(self, rate_hz: float) -> float | None: ...

    def measure_energy(self) -> EnergyMeasurement: ...

    def stop_trigger(self) -> None: ...


class RunRecorder(Protocol):
    def record(self, event: ProcedureEvent) -> None: ...

    def checkpoint(self, result: SingleSensorLaserCalibrationResult) -> None: ...


@dataclass(frozen=True)
class _ProcedureFailure(Exception):
    kind: FailureKind
    reason: str


class SingleSensorLaserCalibrationWorkflow:
    """Run fail-closed preflight before later configuration and firing phases."""

    def __init__(self, bench: LaserCalibrationBench, recorder: RunRecorder):
        self._bench = bench
        self._recorder = recorder

    def run(
        self, request: SingleSensorLaserCalibrationRequest
    ) -> SingleSensorLaserCalibrationResult:
        events: list[ProcedureEvent] = []
        side: SensorSide | None = None
        preflight: PreflightSnapshot | None = None
        pre_existing_config: Mapping[str, float] | None = None
        requested_default_config: Mapping[str, float] | None = None
        default_config_readback: Mapping[str, float] | None = None
        configurations: list[SettingReadback] = []
        measurements: list[EnergyMeasurement] = []
        measurement_criteria: list[tuple[CriterionResult, ...]] = []
        adjustments: list[SettingReadback] = []
        candidates: list[TuningCandidate] = []
        selection: TuningSelection | None = None
        requested_final_config: Mapping[str, float] | None = None
        final_config_readback: Mapping[str, float] | None = None
        active_default_restore: list[SettingReadback] = []
        active_default_restore_failure: str | None = None
        result: SingleSensorLaserCalibrationResult | None = None
        failure: _ProcedureFailure | None = None
        trigger_cleanup_failure: str | None = None
        trigger_stopped_after_measurement = False
        configuration_started = False
        measurement_started = False
        active_defaults_established = False
        used_upward_tuning = False
        try:
            side = self._confirmed_side(request)
            self._record_event(events, "confirmation", "Operator confirmations accepted.")
            preflight = self._bench.preflight(side)
            self._record_event(events, "preflight", "Bench preflight completed.")
            topology_result = validate_exact_single_topology(preflight.topology, side)
            if not topology_result.passed:
                raise _ProcedureFailure(FailureKind.SETUP, topology_result.detail)
            if not preflight.console_responsive:
                raise _ProcedureFailure(
                    FailureKind.SETUP,
                    "Console must be responsive before continuing.",
                )
            if not validate_serial(preflight.console_identity.serial).passed:
                raise _ProcedureFailure(
                    FailureKind.SETUP, "Console serial must be nonblank text."
                )
            if not validate_serial(preflight.selected_sensor_identity.serial).passed:
                raise _ProcedureFailure(
                    FailureKind.SETUP,
                    "Selected-sensor serial must be nonblank text.",
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
                    FailureKind.SETUP,
                    "Ophir identity fields must be nonblank text.",
                )
            if not self._has_valid_ophir_setting_evidence(
                preflight.ophir_setting_evidence
            ):
                raise _ProcedureFailure(
                    FailureKind.SETUP,
                    "Ophir setting evidence is incomplete or invalid.",
                )
            configuration_started = True
            pre_existing_config = dict(self._bench.read_user_configuration())
            requested_default_config = dict(DEFAULT_USER_CONFIG)
            if (
                self._bench.write_user_configuration(
                    dict(requested_default_config)
                )
                is None
            ):
                raise _ProcedureFailure(
                    FailureKind.CONFIGURATION,
                    "Default User Configuration write did not return a result.",
                )
            default_config_readback = dict(self._bench.read_user_configuration())
            if default_config_readback != requested_default_config:
                raise _ProcedureFailure(
                    FailureKind.CONFIGURATION,
                    "Default User Configuration readback must exactly match the request.",
                )
            self._record_event(
                events,
                "default_configuration",
                "Exact default User Configuration was written and read back.",
            )
            self._bench.bring_up_laser_configuration()
            self._verify_active_default_configuration(configurations)
            self._verify_trigger_rate(configurations)
            active_defaults_established = True
            measurement_started = True
            measurement = self._measure_once()
            trigger_stopped_after_measurement = True
            criteria = validate_energy_measurement(measurement)
            measurements.append(measurement)
            measurement_criteria.append(criteria)
            if not all(criterion.passed for criterion in criteria):
                raise _ProcedureFailure(
                    FailureKind.MEASUREMENT,
                    "Initial energy measurement failed quality criteria.",
                )
            self._record_event(
                events,
                "tuning",
                "Initial energy measurement passed quality criteria; tuning is next.",
            )
            requested_current = DEFAULT_USER_CONFIG["TA_CURRENT_DRV"]
            requested_pulse = DEFAULT_USER_CONFIG["TA_PULSE_WIDTH"]
            candidates.append(
                TuningCandidate(
                    requested_current,
                    configurations[0].actual,
                    requested_pulse,
                    configurations[1].actual,
                    measurement,
                )
            )
            if measurement.mean_uj == 350.0:
                selection = TuningSelection(
                    "none",
                    "no_adjustment",
                    True,
                    requested_current,
                    requested_pulse,
                    measurement.mean_uj,
                    "The initial valid mean was exactly 350 uJ; no adjustment was required.",
                )
            elif measurement.mean_uj > 350.0:
                current_setting = requested_current
                while True:
                    next_setting = max(
                        CURRENT_FLOOR_MA, current_setting - CURRENT_STEP_MA
                    )
                    actual_current = self._checked_register_write(
                        "TA_CURRENT_DRV", next_setting, adjustments
                    )
                    trigger_stopped_after_measurement = False
                    candidate_measurement = self._measure_once()
                    trigger_stopped_after_measurement = True
                    candidate_criteria = validate_energy_measurement(
                        candidate_measurement
                    )
                    measurements.append(candidate_measurement)
                    measurement_criteria.append(candidate_criteria)
                    if not all(item.passed for item in candidate_criteria):
                        raise _ProcedureFailure(
                            FailureKind.MEASUREMENT,
                            "Adjustment energy measurement failed quality criteria.",
                        )
                    candidates.append(
                        TuningCandidate(
                            next_setting,
                            actual_current,
                            requested_pulse,
                            configurations[1].actual,
                            candidate_measurement,
                        )
                    )
                    current_setting = next_setting
                    if (
                        candidate_measurement.mean_uj <= 350.0
                        or current_setting == CURRENT_FLOOR_MA
                    ):
                        break
                selected = select_closest_valid_setting(
                    (candidate.requested_current_ma, candidate.measurement)
                    for candidate in candidates
                )
                assert selected is not None
                requested_current, selected_measurement = selected
                accepted = (
                    MIN_ACCEPTABLE_ENERGY_UJ
                    <= selected_measurement.mean_uj
                    <= MAX_ACCEPTABLE_ENERGY_UJ
                )
                selection = TuningSelection(
                    "downward_current",
                    "closest_candidate",
                    accepted,
                    requested_current,
                    requested_pulse,
                    selected_measurement.mean_uj,
                    (
                        "Selected the valid requested current closest to 350 uJ; "
                        "lower requested setting wins a tie."
                        if accepted
                        else "The closest valid requested current was outside the "
                        "accepted 300 to 400 uJ range; lower requested setting "
                        "wins a tie."
                    ),
                )
                if not accepted:
                    raise _ProcedureFailure(
                        FailureKind.NCR,
                        "No downward-current candidate is within 300 to 400 uJ.",
                    )
                if requested_current != current_setting:
                    self._checked_register_write(
                        "TA_CURRENT_DRV", requested_current, adjustments
                    )
            else:
                used_upward_tuning = True
                self._checked_register_write(
                    "EE_PULSE_WIDTH_UL",
                    TEMPORARY_PULSE_WIDTH_LIMIT_US,
                    adjustments,
                )
                self._checked_register_write(
                    "OPT_PULSE_WIDTH_UL",
                    TEMPORARY_PULSE_WIDTH_LIMIT_US,
                    adjustments,
                )
                pulse_setting = requested_pulse
                while True:
                    next_setting = min(
                        MAX_PULSE_WIDTH_US, pulse_setting + PULSE_WIDTH_STEP_US
                    )
                    actual_pulse = self._checked_register_write(
                        "TA_PULSE_WIDTH", next_setting, adjustments
                    )
                    trigger_stopped_after_measurement = False
                    candidate_measurement = self._measure_once()
                    trigger_stopped_after_measurement = True
                    candidate_criteria = validate_energy_measurement(
                        candidate_measurement
                    )
                    measurements.append(candidate_measurement)
                    measurement_criteria.append(candidate_criteria)
                    if not all(item.passed for item in candidate_criteria):
                        raise _ProcedureFailure(
                            FailureKind.MEASUREMENT,
                            "Adjustment energy measurement failed quality criteria.",
                        )
                    candidates.append(
                        TuningCandidate(
                            requested_current,
                            configurations[0].actual,
                            next_setting,
                            actual_pulse,
                            candidate_measurement,
                        )
                    )
                    pulse_setting = next_setting
                    if (
                        pulse_setting == MAX_PULSE_WIDTH_US
                        and candidate_measurement.mean_uj
                        < MIN_ACCEPTABLE_ENERGY_UJ
                    ):
                        selection = TuningSelection(
                            "upward_pulse",
                            "bound",
                            False,
                            requested_current,
                            pulse_setting,
                            candidate_measurement.mean_uj,
                            "The 600 us pulse-width bound was reached with energy "
                            "below 300 uJ, so closest-candidate selection was "
                            "intentionally bypassed.",
                        )
                        raise _ProcedureFailure(
                            FailureKind.NCR,
                            "Energy remained below 300 uJ at the 600 us pulse-width ceiling.",
                        )
                    if (
                        candidate_measurement.mean_uj >= 350.0
                        or pulse_setting == MAX_PULSE_WIDTH_US
                    ):
                        break
                selected = select_closest_valid_setting(
                    (candidate.requested_pulse_width_us, candidate.measurement)
                    for candidate in candidates
                )
                assert selected is not None
                requested_pulse, selected_measurement = selected
                accepted = (
                    MIN_ACCEPTABLE_ENERGY_UJ
                    <= selected_measurement.mean_uj
                    <= MAX_ACCEPTABLE_ENERGY_UJ
                )
                selection = TuningSelection(
                    "upward_pulse",
                    "closest_candidate",
                    accepted,
                    requested_current,
                    requested_pulse,
                    selected_measurement.mean_uj,
                    (
                        "Selected the valid requested pulse width closest to 350 uJ; "
                        "lower requested setting wins a tie."
                        if accepted
                        else "The closest valid requested pulse width was outside the "
                        "accepted 300 to 400 uJ range; lower requested setting wins "
                        "a tie."
                    ),
                )
                if not accepted:
                    raise _ProcedureFailure(
                        FailureKind.NCR,
                        "No upward-pulse candidate is within 300 to 400 uJ.",
                    )
                if requested_pulse != pulse_setting:
                    self._checked_register_write(
                        "TA_PULSE_WIDTH", requested_pulse, adjustments
                    )
            trigger_stopped_after_measurement = False
            final_measurement = self._measure_once()
            trigger_stopped_after_measurement = True
            final_criteria = validate_energy_measurement(final_measurement)
            measurements.append(final_measurement)
            measurement_criteria.append(final_criteria)
            if not all(criterion.passed for criterion in final_criteria):
                raise _ProcedureFailure(
                    FailureKind.MEASUREMENT,
                    "Final energy measurement failed quality criteria.",
                )
            if not (
                MIN_ACCEPTABLE_ENERGY_UJ
                <= final_measurement.mean_uj
                <= MAX_ACCEPTABLE_ENERGY_UJ
            ):
                raise _ProcedureFailure(
                    FailureKind.NCR,
                    "Final energy must be between 300 and 400 uJ inclusive.",
                )
            for name, requested in (
                ("TA_CURRENT_DRV", requested_current),
                ("TA_PULSE_WIDTH", requested_pulse),
            ):
                try:
                    actual = self._bench.read_register(name)
                except Exception as error:
                    raise _ProcedureFailure(
                        FailureKind.CONFIGURATION,
                        f"Final active {name} readback failed.",
                    ) from error
                readback = SettingReadback(name, requested, actual)
                configurations.append(readback)
                if not within_percent(requested, actual, 2.0):
                    raise _ProcedureFailure(
                        FailureKind.CONFIGURATION,
                        f"Final active {name} is outside the allowed 2 percent tolerance.",
                    )
            requested_final_config = dict(DEFAULT_USER_CONFIG)
            requested_final_config["TA_CURRENT_DRV"] = requested_current
            requested_final_config["TA_PULSE_WIDTH"] = requested_pulse
            if used_upward_tuning:
                requested_final_config["EE_PULSE_WIDTH_UL"] = (
                    TEMPORARY_PULSE_WIDTH_LIMIT_US
                )
                requested_final_config["OPT_PULSE_WIDTH_UL"] = (
                    TEMPORARY_PULSE_WIDTH_LIMIT_US
                )
            try:
                if (
                    self._bench.write_user_configuration(
                        dict(requested_final_config)
                    )
                    is None
                ):
                    raise _ProcedureFailure(
                        FailureKind.CONFIGURATION,
                        "Passing User Configuration write did not return a result.",
                    )
                final_config_readback = dict(self._bench.read_user_configuration())
            except _ProcedureFailure:
                raise
            except Exception as error:
                raise _ProcedureFailure(
                    FailureKind.CONFIGURATION,
                    "Passing User Configuration write or readback failed.",
                ) from error
            if final_config_readback != requested_final_config:
                raise _ProcedureFailure(
                    FailureKind.CONFIGURATION,
                    "Passing User Configuration readback must exactly match the request.",
                )
            self._record_event(
                events,
                "final_configuration",
                "Passing tuned User Configuration was written and read back exactly.",
            )
            result = SingleSensorLaserCalibrationResult(
                status=ProcedureStatus.PASSED,
                side=side,
                topology=preflight.topology,
                identities=(
                    preflight.console_identity,
                    preflight.selected_sensor_identity,
                ),
                ophir_identity=preflight.ophir_identity,
                ophir_setting_evidence=preflight.ophir_setting_evidence,
                pre_existing_config=pre_existing_config,
                requested_default_config=requested_default_config,
                default_config_readback=default_config_readback,
                configurations=tuple(configurations),
                measurements=tuple(measurements),
                measurement_criteria=tuple(measurement_criteria),
                adjustments=tuple(adjustments),
                candidates=tuple(candidates),
                selection=selection,
                requested_final_config=requested_final_config,
                final_config_readback=final_config_readback,
                events=tuple(events),
            )
        except _ProcedureFailure as caught_failure:
            failure = caught_failure
        except Exception:
            if measurement_started:
                failure = _ProcedureFailure(
                    FailureKind.MEASUREMENT, "Energy measurement failed."
                )
            elif configuration_started:
                failure = _ProcedureFailure(
                    FailureKind.CONFIGURATION,
                    "Default configuration or active-setting check failed.",
                )
            else:
                failure = _ProcedureFailure(FailureKind.SETUP, "Bench preflight failed.")
        finally:
            if not trigger_stopped_after_measurement:
                try:
                    self._bench.stop_trigger()
                except Exception:
                    trigger_cleanup_failure = "Trigger stop failed."
        if failure is None and trigger_cleanup_failure is not None:
            failure = _ProcedureFailure(
                FailureKind.MEASUREMENT if measurement_started else FailureKind.SETUP,
                trigger_cleanup_failure,
            )
        if failure is not None and active_defaults_established and measurement_started:
            (
                active_default_restore,
                active_default_restore_failure,
            ) = self._restore_active_defaults()
            self._record_event(
                events,
                "active_default_restore",
                active_default_restore_failure
                or "Active default laser registers were restored.",
            )
        if failure is not None:
            result = self._failed_result(
                events,
                side,
                preflight,
                failure,
                pre_existing_config,
                requested_default_config,
                default_config_readback,
                configurations,
                measurements,
                measurement_criteria,
                trigger_cleanup_failure,
                adjustments,
                candidates,
                selection,
                requested_final_config,
                final_config_readback,
                active_default_restore,
                active_default_restore_failure,
            )
            self._recorder.checkpoint(result)
        assert result is not None
        return result

    @staticmethod
    def _confirmed_side(request: SingleSensorLaserCalibrationRequest) -> SensorSide:
        if request.side not in ("left", "right") or not request.side_confirmed:
            raise _ProcedureFailure(
                FailureKind.SETUP,
                "Confirm the selected sensor side before continuing.",
            )
        if not request.fixture_confirmed:
            raise _ProcedureFailure(
                FailureKind.SETUP,
                "Confirm fixture placement before continuing.",
            )
        return request.side

    def _record_event(
        self, events: list[ProcedureEvent], stage: str, message: str
    ) -> None:
        event = ProcedureEvent(datetime.now(timezone.utc), stage, message)
        events.append(event)
        self._recorder.record(event)

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

    def _verify_active_default_configuration(
        self, configurations: list[SettingReadback]
    ) -> None:
        for name in (
            "TA_CURRENT_DRV",
            "TA_PULSE_WIDTH",
            "SEED_CW_GAIN",
            "EE_PULSE_WIDTH_UL",
            "OPT_PULSE_WIDTH_UL",
        ):
            requested = DEFAULT_USER_CONFIG[name]
            actual = self._bench.read_register(name)
            readback = SettingReadback(name, requested, actual)
            configurations.append(readback)
            if not within_percent(requested, actual, 2.0):
                raise _ProcedureFailure(
                    FailureKind.CONFIGURATION,
                    f"Active {name} is outside the allowed 2 percent tolerance.",
                )

    def _verify_trigger_rate(self, configurations: list[SettingReadback]) -> None:
        rate_hz = self._bench.read_trigger_rate_hz()
        if rate_hz != 40.0:
            if self._bench.write_trigger_rate_hz(40.0) is None:
                raise _ProcedureFailure(
                    FailureKind.CONFIGURATION,
                    "Trigger-rate correction did not return a result.",
                )
            rate_hz = self._bench.read_trigger_rate_hz()
        readback = SettingReadback("trigger_rate_hz", 40.0, rate_hz)
        configurations.append(readback)
        if not self._is_valid_trigger_rate(rate_hz):
            raise _ProcedureFailure(
                FailureKind.CONFIGURATION,
                "Active trigger rate must be between 39 and 41 Hz inclusive.",
            )

    @staticmethod
    def _is_valid_trigger_rate(rate_hz: object) -> bool:
        try:
            return 39.0 <= float(rate_hz) <= 41.0
        except (TypeError, ValueError):
            return False

    def _measure_once(self) -> EnergyMeasurement:
        try:
            return self._bench.measure_energy()
        finally:
            self._bench.stop_trigger()

    def _checked_register_write(
        self,
        name: str,
        requested: float,
        adjustments: list[SettingReadback],
    ) -> float:
        try:
            write_result = self._bench.write_register(name, requested)
        except Exception as error:
            raise _ProcedureFailure(
                FailureKind.CONFIGURATION,
                f"Active {name} write failed.",
            ) from error
        if write_result is None:
            raise _ProcedureFailure(
                FailureKind.CONFIGURATION,
                f"Active {name} write did not return a result.",
            )
        try:
            actual = self._bench.read_register(name)
        except Exception as error:
            raise _ProcedureFailure(
                FailureKind.CONFIGURATION,
                f"Active {name} readback failed.",
            ) from error
        readback = SettingReadback(name, requested, actual)
        adjustments.append(readback)
        if not within_percent(requested, actual, 2.0):
            raise _ProcedureFailure(
                FailureKind.CONFIGURATION,
                f"Active {name} is outside the allowed 2 percent tolerance.",
            )
        return actual

    def _restore_active_defaults(
        self,
    ) -> tuple[list[SettingReadback], str | None]:
        restored: list[SettingReadback] = []
        failures: list[str] = []
        for name in (
            "TA_CURRENT_DRV",
            "TA_PULSE_WIDTH",
            "SEED_CW_GAIN",
            "EE_PULSE_WIDTH_UL",
            "OPT_PULSE_WIDTH_UL",
        ):
            requested = DEFAULT_USER_CONFIG[name]
            try:
                if self._bench.write_register(name, requested) is None:
                    failures.append(f"{name} write returned no result")
                actual = self._bench.read_register(name)
                restored.append(SettingReadback(name, requested, actual))
                if not within_percent(requested, actual, 2.0):
                    failures.append(f"{name} readback was outside 2 percent")
            except Exception:
                failures.append(f"{name} restore raised an exception")
        return restored, "; ".join(failures) or None

    def _failed_result(
        self,
        events: list[ProcedureEvent],
        side: SensorSide | None,
        preflight: PreflightSnapshot | None,
        failure: _ProcedureFailure,
        pre_existing_config: Mapping[str, float] | None,
        requested_default_config: Mapping[str, float] | None,
        default_config_readback: Mapping[str, float] | None,
        configurations: list[SettingReadback],
        measurements: list[EnergyMeasurement],
        measurement_criteria: list[tuple[CriterionResult, ...]],
        trigger_cleanup_failure: str | None,
        adjustments: list[SettingReadback],
        candidates: list[TuningCandidate],
        selection: TuningSelection | None,
        requested_final_config: Mapping[str, float] | None,
        final_config_readback: Mapping[str, float] | None,
        active_default_restore: list[SettingReadback],
        active_default_restore_failure: str | None,
    ) -> SingleSensorLaserCalibrationResult:
        if trigger_cleanup_failure is not None:
            self._record_event(events, "trigger_cleanup", trigger_cleanup_failure)
        self._record_event(events, "failure", failure.reason)
        return SingleSensorLaserCalibrationResult(
            status=(
                ProcedureStatus.FAILED_NCR
                if failure.kind is FailureKind.NCR
                else ProcedureStatus.FAILED
            ),
            side=side,
            failure_kind=failure.kind,
            failure_reason=failure.reason,
            topology=preflight.topology if preflight else None,
            identities=(
                (preflight.console_identity, preflight.selected_sensor_identity)
                if preflight
                else ()
            ),
            ophir_identity=preflight.ophir_identity if preflight else None,
            ophir_setting_evidence=(
                preflight.ophir_setting_evidence if preflight else ()
            ),
            pre_existing_config=pre_existing_config,
            requested_default_config=requested_default_config,
            default_config_readback=default_config_readback,
            configurations=tuple(configurations),
            measurements=tuple(measurements),
            measurement_criteria=tuple(measurement_criteria),
            trigger_cleanup_failure=trigger_cleanup_failure,
            adjustments=tuple(adjustments),
            candidates=tuple(candidates),
            selection=selection,
            requested_final_config=requested_final_config,
            final_config_readback=final_config_readback,
            active_default_restore=tuple(active_default_restore),
            active_default_restore_failure=active_default_restore_failure,
            events=tuple(events),
        )
