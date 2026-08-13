"""Operator entry point for WI-00015 single-sensor laser calibration."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from omotion.WI15LaserCalibration import ProcedureStatus
from omotion.WI15LaserCalibrationHardware import (
    MotionLaserCalibrationBench,
    OphirEnergyMeter,
)
from omotion.WI15LaserCalibrationReport import HtmlRunReport, JsonRunRecorder
from omotion.WI15SingleSensorLaserCalibration import (
    SingleSensorLaserCalibrationRequest,
    SingleSensorLaserCalibrationWorkflow,
)


PROCEDURE_ID = "WI-00015"
APPROVED_PROCEDURE_REVISION = "WI-00015 automated process addendum approved 2026-08-12"

recorder_factory = JsonRunRecorder
meter_factory = OphirEnergyMeter
bench_factory = MotionLaserCalibrationBench
workflow_factory = SingleSensorLaserCalibrationWorkflow
report_factory = HtmlRunReport


@dataclass(frozen=True)
class OperatorRunReportRequest:
    """Report metadata, including the approved procedure revision."""

    request: SingleSensorLaserCalibrationRequest
    procedure_revision: str


class _OperatorCanceled(Exception):
    pass


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
    response = input_func(prompt).strip().lower()
    if response in ("yes", "y"):
        return True
    if response in ("no", "n", "cancel", "c"):
        return False
    return False


def _selected_side(input_func: Callable[[str], str], output_func: Callable[[str], None]) -> str:
    while True:
        side = input_func("Installed sensor side (left/right): ").strip().lower()
        if side in ("left", "right"):
            output_func(f"Selected sensor side: {side}")
            return side
        output_func("Enter exactly left or right.")


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
    """Collect operator evidence, run the workflow, and write terminal artifacts."""
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
        side = _selected_side(input_func, output_func)
        if not _confirmed(
            f"Confirm selected sensor side is {side} (yes/no): ", input_func
        ):
            raise _OperatorCanceled
        if not _confirmed(
            "Confirm fixture placement and beam containment (yes/no): ", input_func
        ):
            raise _OperatorCanceled
    except (EOFError, KeyboardInterrupt, _OperatorCanceled):
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
        workflow = workflow_factory(bench, recorder)
        request = SingleSensorLaserCalibrationRequest(
            side=side,
            side_confirmed=True,
            fixture_confirmed=True,
            operator=operator,
            build_id=build_revision,
            fixture_id=fixture_id,
            fixture_calibration_status=fixture_calibration_status,
            procedure_id=PROCEDURE_ID,
            output_root=Path(args.output_dir),
            run_id=run_id,
        )
        result = workflow.run(request)
        report = report_factory(recorder.run_directory)
        report_path = report.report_path
        terminal_result = replace(
            result,
            report_paths=(Path(recorder.json_path), Path(report_path)),
        )
        recorder.checkpoint(terminal_result)
        report.write(
            OperatorRunReportRequest(request, procedure_revision),
            terminal_result,
            recorder.json_path,
        )
        recorder.checkpoint(terminal_result)
        output_func(f"Terminal status: {terminal_result.status.value}")
        output_func(f"JSON evidence: {Path(recorder.json_path).resolve()}")
        output_func(f"HTML report: {Path(report_path).resolve()}")
        return 0 if terminal_result.status is ProcedureStatus.PASSED else 1
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
