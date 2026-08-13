"""Operator entry point for WI-00015 dual-sensor laser calibration."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

import omotion
from omotion.calibration.dual_sensor_laser import (
    DualSensorLaserCalibrationRequest,
    DualSensorLaserCalibrationWorkflow,
    PlacementChangeRequest,
)
from omotion.calibration.dual_sensor_laser_report import DualSensorHtmlRunReport
from omotion.calibration.laser import FailureKind, ProcedureStatus
from omotion.calibration.laser_hardware import (
    MotionLaserCalibrationBench,
    OphirEnergyMeter,
)
from omotion.calibration.reporting import JsonRunRecorder
from omotion.calibration.single_sensor_laser import (
    ReportArtifactEvidence,
    ReportArtifactStatus,
)


PROCEDURE_ID = "WI-00015"
APPROVED_PROCEDURE_REVISION = (
    "WI-00015 automated process addendum approved 2026-08-12"
)

recorder_factory = JsonRunRecorder
meter_factory = OphirEnergyMeter
bench_factory = MotionLaserCalibrationBench
workflow_factory = DualSensorLaserCalibrationWorkflow
report_factory = DualSensorHtmlRunReport


@dataclass(frozen=True)
class OperatorRunReportRequest:
    """Report metadata, including the approved procedure revision."""

    request: DualSensorLaserCalibrationRequest
    procedure_revision: str


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="./wi15_out")
    parser.add_argument("--operator")
    parser.add_argument("--build-revision")
    parser.add_argument("--fixture-id")
    parser.add_argument("--fixture-calibration-status")
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
    """Collect metadata, run the dual workflow, and finalize both artifacts."""
    input_func = input if input_func is None else input_func
    output_func = print if output_func is None else output_func
    args = _parser().parse_args(argv)
    try:
        operator = _required_value(args.operator, "Operator: ", input_func)
        build_revision = _required_value(
            args.build_revision, "Build revision: ", input_func
        )
        fixture_id = _required_value(args.fixture_id, "Fixture ID: ", input_func)
        fixture_calibration_status = _required_value(
            args.fixture_calibration_status,
            "Fixture calibration status: ",
            input_func,
        )
        procedure_revision = _required_value(
            args.procedure_revision, "Procedure revision: ", input_func
        )
    except (EOFError, KeyboardInterrupt):
        output_func("Calibration canceled before hardware construction.")
        return 1

    recorder = None
    meter = None
    bench = None
    try:
        run_id = _run_id()
        recorder = recorder_factory(args.output_dir, PROCEDURE_ID, run_id)
        meter = meter_factory()
        bench = bench_factory(meter)

        def acknowledge_placement(change: PlacementChangeRequest) -> bool:
            output_func(change.label)
            prompt = (
                f"Please place the {change.to_side} sensor module "
                f"(serial {change.sensor_serial}) in the Ophir 0 cm fixture. "
                "Confirm when it is securely seated [y/N]: "
            )
            return _confirmed(prompt, input_func)

        workflow = workflow_factory(bench, recorder, acknowledge_placement)
        request = DualSensorLaserCalibrationRequest(
            operator=operator,
            build_id=build_revision,
            fixture_id=fixture_id,
            fixture_calibration_status=fixture_calibration_status,
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
            meter = None
        if cleanup_failure is not None:
            result = replace(
                result,
                status=(
                    ProcedureStatus.FAILED
                    if result.status is ProcedureStatus.PASSED
                    else result.status
                ),
                failure_kind=(
                    FailureKind.MEASUREMENT
                    if result.status is ProcedureStatus.PASSED
                    else result.failure_kind
                ),
                failure_reason=(
                    "Hardware resource cleanup failed."
                    if result.status is ProcedureStatus.PASSED
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
            failed_result = replace(
                incomplete_result,
                status=(
                    ProcedureStatus.FAILED
                    if result.status is ProcedureStatus.PASSED
                    else result.status
                ),
                failure_kind=(
                    FailureKind.REPORT
                    if result.status is ProcedureStatus.PASSED
                    else result.failure_kind
                ),
                failure_reason=(
                    "HTML report generation failed."
                    if result.status is ProcedureStatus.PASSED
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
        output_func(f"Calibration failed before a terminal report: {exc}")
        return 1
    finally:
        if bench is not None:
            _close_best_effort(bench)
        else:
            _close_best_effort(meter)


if __name__ == "__main__":
    raise SystemExit(main())
