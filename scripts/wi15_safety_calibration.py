"""Operator entry point for WI-00015 Safety Calibration."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import math
from pathlib import Path
import time
from typing import Callable, Sequence

import omotion
from omotion.WI15LaserCalibration import FailureKind, ProcedureStatus
from omotion.WI15LaserCalibrationReport import JsonRunRecorder
from omotion.WI15SafetyCalibration import PowerCycleEvidence, ShippingTopology
from omotion.WI15SafetyCalibrationHardware import MotionSafetyCalibrationBench
from omotion.WI15SafetyCalibrationReport import SafetyCalibrationHtmlRunReport
from omotion.WI15SafetyCalibrationWorkflow import (
    SafetyCalibrationRequest,
    SafetyCalibrationWorkflow,
)
from omotion.WI15SingleSensorLaserCalibration import (
    ReportArtifactEvidence,
    ReportArtifactStatus,
)


PROCEDURE_ID = "WI-00015"
APPROVED_PROCEDURE_REVISION = (
    "WI-00015 automated process addendum approved 2026-08-12"
)

recorder_factory = JsonRunRecorder
bench_factory = MotionSafetyCalibrationBench
workflow_factory = SafetyCalibrationWorkflow
report_factory = SafetyCalibrationHtmlRunReport


@dataclass(frozen=True)
class OperatorRunReportRequest:
    """Report metadata, including the approved procedure revision."""

    request: SafetyCalibrationRequest
    procedure_revision: str


class _OperatorCanceled(Exception):
    pass


class ManualPowerCycleCoordinator:
    """Guide and independently observe the required manual console restart."""

    def __init__(
        self,
        *,
        input_func: Callable[[str], str],
        output_func: Callable[[str], None],
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval_s: float = 0.1,
        disconnect_timeout_s: float = 60.0,
        reconnect_timeout_s: float = 120.0,
    ):
        timing = (poll_interval_s, disconnect_timeout_s, reconnect_timeout_s)
        if any(
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(float(value))
            or value <= 0
            for value in timing
        ):
            raise ValueError("manual power-cycle timing values must be finite and positive")
        self._input = input_func
        self._output = output_func
        self._clock = clock
        self._wall_clock = wall_clock
        self._sleep = sleep
        self._poll_interval_s = float(poll_interval_s)
        self._disconnect_timeout_s = float(disconnect_timeout_s)
        self._reconnect_timeout_s = float(reconnect_timeout_s)

    def _utc_now(self) -> datetime:
        return datetime.fromtimestamp(self._wall_clock(), timezone.utc)

    def _wait_for_state(
        self,
        predicate: Callable[[], bool],
        expected: bool,
        timeout_s: float,
    ) -> bool:
        deadline = self._clock() + timeout_s
        while self._clock() < deadline:
            try:
                if bool(predicate()) is expected:
                    return True
            except Exception:
                pass
            remaining = deadline - self._clock()
            self._sleep(min(self._poll_interval_s, remaining))
        try:
            return bool(predicate()) is expected
        except Exception:
            return False

    def perform(
        self,
        *,
        minimum_off_s: float,
        expected_console_serial: str,
        is_console_connected: Callable[[], bool],
        read_console_serial: Callable[[], str | None],
    ) -> PowerCycleEvidence:
        minimum_off_s = float(minimum_off_s)
        if not math.isfinite(minimum_off_s) or minimum_off_s < 15.0:
            raise ValueError("Safety Calibration requires at least 15 seconds off")

        off_requested_at = self._utc_now()
        self._output(
            "6A. Console power-off: laser and scan activity are stopped."
        )
        if not _confirmed(
            "Ready for the observed power-off step? After confirming, immediately "
            "switch console main power OFF [y/N]: ",
            self._input,
        ):
            return PowerCycleEvidence(
                off_requested_at,
                None,
                None,
                None,
                None,
                None,
                False,
                False,
                False,
                None,
                expected_console_serial,
                None,
            )

        self._output(
            f"Now switch console main power OFF. Waiting up to "
            f"{self._disconnect_timeout_s:g} seconds for Motion to observe console "
            "disconnection."
        )
        if not self._wait_for_state(
            is_console_connected, False, self._disconnect_timeout_s
        ):
            self._output(
                f"Console remained connected through the "
                f"{self._disconnect_timeout_s:g}-second timeout; power restoration "
                "was not requested."
            )
            return PowerCycleEvidence(
                off_requested_at,
                None,
                None,
                None,
                None,
                None,
                False,
                False,
                False,
                None,
                expected_console_serial,
                None,
            )

        disconnected_at_clock = self._clock()
        disconnect_observed_at = self._utc_now()
        self._output(
            "6B. Console disconnection observed. Measuring the required 15-second "
            "minimum power-off interval."
        )
        while self._clock() - disconnected_at_clock < minimum_off_s:
            remaining = minimum_off_s - (self._clock() - disconnected_at_clock)
            self._sleep(min(self._poll_interval_s, remaining))

        on_allowed_at = self._utc_now()
        on_requested_at = self._utc_now()
        off_duration_s = self._clock() - disconnected_at_clock
        if not _confirmed(
            "The measured off interval is complete. Ready to restore power? After "
            "confirming, immediately switch console main power ON [y/N]: ",
            self._input,
        ):
            return PowerCycleEvidence(
                off_requested_at,
                disconnect_observed_at,
                on_allowed_at,
                on_requested_at,
                None,
                off_duration_s,
                True,
                False,
                False,
                None,
                expected_console_serial,
                None,
            )

        self._output(
            f"Now switch console main power ON. Waiting up to "
            f"{self._reconnect_timeout_s:g} seconds for Motion to observe console "
            "reconnection."
        )
        if not self._wait_for_state(
            is_console_connected, True, self._reconnect_timeout_s
        ):
            self._output("Console reconnection was not observed before timeout.")
            return PowerCycleEvidence(
                off_requested_at,
                disconnect_observed_at,
                on_allowed_at,
                on_requested_at,
                None,
                off_duration_s,
                True,
                False,
                False,
                None,
                expected_console_serial,
                None,
            )

        reconnect_observed_at = self._utc_now()
        try:
            console_serial_after = read_console_serial()
        except Exception:
            console_serial_after = None
        self._output("6C. Console reconnection observed; verifying persisted settings.")
        return PowerCycleEvidence(
            off_requested_at,
            disconnect_observed_at,
            on_allowed_at,
            on_requested_at,
            reconnect_observed_at,
            off_duration_s,
            True,
            True,
            True,
            "Observed disconnect and reconnect on the same long-lived Motion console handle.",
            expected_console_serial,
            console_serial_after,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="./wi15_out")
    parser.add_argument("--operator")
    parser.add_argument("--build-revision")
    parser.add_argument("--fixture-id")
    parser.add_argument(
        "--shipping-topology",
        choices=tuple(item.value for item in ShippingTopology),
    )
    parser.add_argument(
        "--procedure-revision", default=APPROVED_PROCEDURE_REVISION
    )
    return parser


def _required_value(
    value: str | None, prompt: str, input_func: Callable[[str], str]
) -> str:
    while True:
        candidate = value if value is not None else input_func(prompt)
        value = None
        candidate = candidate.strip()
        if candidate:
            return candidate


def _confirmed(prompt: str, input_func: Callable[[str], str]) -> bool:
    return input_func(prompt).strip().lower() in ("yes", "y")


def _shipping_topology(
    value: str | None,
    input_func: Callable[[str], str],
    output_func: Callable[[str], None],
) -> ShippingTopology:
    candidate = value
    while True:
        if candidate is None:
            candidate = input_func(
                "Declared shipping topology (single-left/single-right/dual): "
            )
        try:
            topology = ShippingTopology(candidate.strip().lower())
        except (AttributeError, ValueError):
            output_func("Enter exactly single-left, single-right, or dual.")
            candidate = None
            continue
        output_func(f"Declared shipping topology: {topology.value}.")
        return topology


def _topology_instruction(topology: ShippingTopology) -> str:
    descriptions = {
        ShippingTopology.SINGLE_LEFT: "the left sensor module only",
        ShippingTopology.SINGLE_RIGHT: "the right sensor module only",
        ShippingTopology.DUAL: "both the left and right sensor modules",
    }
    return descriptions[topology]


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _close_best_effort(resource) -> None:
    if resource is None:
        return
    try:
        resource.close()
    except Exception:
        pass


def main(
    argv: Sequence[str] | None = None,
    *,
    input_func: Callable[[str], str] | None = None,
    output_func: Callable[[str], None] | None = None,
) -> int:
    """Collect audit metadata, execute the workflow, and finalize artifacts."""
    input_func = input if input_func is None else input_func
    output_func = print if output_func is None else output_func
    args = _parser().parse_args(argv)

    try:
        operator = _required_value(args.operator, "Operator: ", input_func)
        build_revision = _required_value(
            args.build_revision, "Build revision: ", input_func
        )
        fixture_id = _required_value(args.fixture_id, "Bench or fixture ID: ", input_func)
        topology = _shipping_topology(
            args.shipping_topology, input_func, output_func
        )
        procedure_revision = _required_value(
            args.procedure_revision, "Procedure revision: ", input_func
        )
        instruction = _topology_instruction(topology)
        if not _confirmed(
            f"Please connect exact {topology.value} shipping topology: "
            f"{instruction}. The modules may remain connected during the "
            "console-only ADC portion. Confirm when ready [y/N]: ",
            input_func,
        ):
            raise _OperatorCanceled
    except (EOFError, KeyboardInterrupt, _OperatorCanceled):
        output_func("Safety Calibration canceled before hardware construction.")
        return 1

    recorder = None
    bench = None
    try:
        run_id = _run_id()
        recorder = recorder_factory(args.output_dir, PROCEDURE_ID, run_id)
        coordinator = ManualPowerCycleCoordinator(
            input_func=input_func,
            output_func=output_func,
        )
        bench = bench_factory(power_cycle_coordinator=coordinator)
        workflow = workflow_factory(bench, recorder)
        request = SafetyCalibrationRequest(
            shipping_topology=topology,
            operator=operator,
            build_id=build_revision,
            fixture_id=fixture_id,
            procedure_id=PROCEDURE_ID,
            output_root=Path(args.output_dir),
            run_id=run_id,
            sdk_version=getattr(omotion, "__version__", "unavailable"),
            started_at=datetime.now(timezone.utc),
        )
        result = workflow.run(request)

        try:
            bench.close()
        except Exception as exc:
            cleanup_failure = str(exc) or exc.__class__.__name__
        else:
            cleanup_failure = None
        finally:
            bench = None
        if cleanup_failure is not None:
            was_passing = result.status is ProcedureStatus.PASSED
            result = replace(
                result,
                status=ProcedureStatus.FAILED if was_passing else result.status,
                failure_kind=(
                    FailureKind.MEASUREMENT if was_passing else result.failure_kind
                ),
                failure_reason=(
                    "Hardware resource cleanup failed."
                    if was_passing
                    else result.failure_reason
                ),
                resource_cleanup_failure=cleanup_failure,
            )
            recorder.checkpoint(result)

        report_path = Path(recorder.run_directory) / "report.html"
        incomplete_result = replace(
            result,
            report_paths=(Path(recorder.json_path),),
            report_artifact=ReportArtifactEvidence(
                report_path, ReportArtifactStatus.INCOMPLETE
            ),
        )
        recorder.checkpoint(incomplete_result)
        try:
            report = report_factory(recorder.run_directory)
            report_path = Path(report.report_path)
            finalized_result = replace(
                incomplete_result,
                report_paths=(Path(recorder.json_path), report_path),
                report_artifact=ReportArtifactEvidence(
                    report_path, ReportArtifactStatus.FINALIZED
                ),
            )
            written_report = report.write(
                OperatorRunReportRequest(request, procedure_revision),
                finalized_result,
                recorder.json_path,
            )
            if (
                Path(written_report).resolve() != report_path.resolve()
                or not report_path.is_file()
            ):
                raise RuntimeError(
                    "HTML report writer did not create the expected file"
                )
        except Exception as exc:
            report_failure = str(exc) or exc.__class__.__name__
            was_passing = result.status is ProcedureStatus.PASSED
            failed_result = replace(
                incomplete_result,
                status=ProcedureStatus.FAILED if was_passing else result.status,
                failure_kind=(
                    FailureKind.REPORT if was_passing else result.failure_kind
                ),
                failure_reason=(
                    "HTML report generation failed."
                    if was_passing
                    else result.failure_reason
                ),
                report_paths=(Path(recorder.json_path),),
                report_artifact=ReportArtifactEvidence(
                    report_path,
                    ReportArtifactStatus.FAILED,
                    report_failure,
                ),
            )
            recorder.checkpoint(failed_result)
            output_func(f"HTML report generation failed: {report_failure}")
            return 1

        recorder.checkpoint(finalized_result)
        output_func(f"Terminal status: {finalized_result.status.value}")
        if finalized_result.failure_kind is not None:
            output_func(f"Failure category: {finalized_result.failure_kind.value}")
        if finalized_result.failure_reason is not None:
            output_func(f"Failure reason: {finalized_result.failure_reason}")
        output_func(f"JSON evidence: {Path(recorder.json_path).resolve()}")
        output_func(f"HTML report: {Path(report_path).resolve()}")
        return 0 if finalized_result.status is ProcedureStatus.PASSED else 1
    except Exception as exc:
        output_func(f"Safety Calibration failed before a terminal report: {exc}")
        return 1
    finally:
        if bench is not None:
            _close_best_effort(bench)


if __name__ == "__main__":
    raise SystemExit(main())
