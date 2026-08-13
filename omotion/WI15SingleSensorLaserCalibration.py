"""UI-neutral workflow shell for WI-00015 single-sensor laser calibration."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Protocol

from omotion.WI15LaserCalibration import (
    DeviceIdentity,
    EnergyMeasurement,
    FailureKind,
    OphirIdentity,
    ProcedureStatus,
    SensorSide,
    SettingReadback,
    TopologySnapshot,
    validate_exact_single_topology,
    validate_serial,
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


@dataclass(frozen=True)
class PreflightSnapshot:
    topology: TopologySnapshot
    console_identity: DeviceIdentity
    selected_sensor_identity: DeviceIdentity
    console_responsive: bool
    ophir_identity: OphirIdentity | None
    ophir_ready: bool
    ophir_setup_readbacks: tuple[SettingReadback, ...]
    ophir_failure_reason: str | None = None


@dataclass(frozen=True)
class SingleSensorLaserCalibrationResult:
    status: ProcedureStatus
    side: SensorSide | None
    failure_kind: FailureKind | None = None
    failure_reason: str | None = None
    topology: TopologySnapshot | None = None
    identities: tuple[DeviceIdentity, ...] = ()
    ophir_identity: OphirIdentity | None = None
    ophir_setup_readbacks: tuple[SettingReadback, ...] = ()
    pre_existing_config: Mapping[str, float] | None = None
    requested_default_config: Mapping[str, float] | None = None
    default_config_readback: Mapping[str, float] | None = None
    configurations: tuple[SettingReadback, ...] = ()
    measurements: tuple[EnergyMeasurement, ...] = ()
    adjustments: tuple[SettingReadback, ...] = ()
    events: tuple[ProcedureEvent, ...] = ()
    report_paths: tuple[Path | str, ...] = ()


class LaserCalibrationBench(Protocol):
    def preflight(self, side: SensorSide) -> PreflightSnapshot: ...

    def read_user_configuration(self) -> Mapping[str, float]: ...

    def write_user_configuration(
        self, configuration: Mapping[str, float]
    ) -> Mapping[str, float] | None: ...

    def read_register(self, name: str) -> float: ...

    def write_register(self, name: str, value: float) -> SettingReadback | None: ...

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
            if not preflight.ophir_setup_readbacks:
                raise _ProcedureFailure(
                    FailureKind.SETUP,
                    "Ophir setup readbacks must be present.",
                )
            return SingleSensorLaserCalibrationResult(
                status=ProcedureStatus.PASSED,
                side=side,
                topology=preflight.topology,
                identities=(
                    preflight.console_identity,
                    preflight.selected_sensor_identity,
                ),
                ophir_identity=preflight.ophir_identity,
                ophir_setup_readbacks=preflight.ophir_setup_readbacks,
                events=tuple(events),
            )
        except _ProcedureFailure as failure:
            result = self._failed_result(events, side, preflight, failure)
            self._recorder.checkpoint(result)
            return result
        except Exception:
            failure = _ProcedureFailure(FailureKind.SETUP, "Bench preflight failed.")
            result = self._failed_result(events, side, preflight, failure)
            self._recorder.checkpoint(result)
            return result
        finally:
            self._bench.stop_trigger()

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

    def _failed_result(
        self,
        events: list[ProcedureEvent],
        side: SensorSide | None,
        preflight: PreflightSnapshot | None,
        failure: _ProcedureFailure,
    ) -> SingleSensorLaserCalibrationResult:
        self._record_event(events, "failure", failure.reason)
        return SingleSensorLaserCalibrationResult(
            status=ProcedureStatus.FAILED,
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
            ophir_setup_readbacks=(
                preflight.ophir_setup_readbacks if preflight else ()
            ),
            events=tuple(events),
        )
