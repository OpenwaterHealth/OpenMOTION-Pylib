"""Operator entry point for WI-00015 single-sensor laser calibration."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

import omotion
from omotion.calibration.laser_hardware import (
    MotionLaserCalibrationBench,
    OphirEnergyMeter,
)
from omotion.calibration.reporting import HtmlRunReport, JsonRunRecorder
from omotion.calibration.script_support import (
    APPROVED_PROCEDURE_REVISION,
    PROCEDURE_ID,
    OperatorCanceled as _OperatorCanceled,
    OperatorRunReportRequest,
    apply_cleanup_failure,
    close_bench_capturing,
    close_best_effort as _close_best_effort,
    confirmed as _confirmed,
    finalize_run_artifacts,
    make_parser,
    required_value as _required_value,
    utc_run_id as _run_id,
)
from omotion.calibration.single_sensor_laser import (
    SingleSensorLaserCalibrationRequest,
    SingleSensorLaserCalibrationWorkflow,
)


recorder_factory = JsonRunRecorder
meter_factory = OphirEnergyMeter
bench_factory = MotionLaserCalibrationBench
workflow_factory = SingleSensorLaserCalibrationWorkflow
report_factory = HtmlRunReport


def _parser() -> argparse.ArgumentParser:
    return make_parser(
        __doc__,
        lambda parser: parser.add_argument("--fixture-calibration-status"),
    )


def _selected_side(
    input_func: Callable[[str], str], output_func: Callable[[str], None]
) -> str:
    while True:
        side = input_func("Installed sensor side (left/right): ").strip().lower()
        if side in ("left", "right"):
            output_func(f"Selected sensor side: {side}")
            return side
        output_func("Enter exactly left or right.")


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
        build_revision = args.build_revision or "unspecified"
        fixture_id = _required_value(args.fixture_id, "Fixture ID: ", input_func)
        fixture_calibration_status = args.fixture_calibration_status
        procedure_revision = _required_value(
            args.procedure_revision, "Procedure revision: ", input_func
        )
        side = _selected_side(input_func, output_func)
        if not _confirmed(
            f"Confirm selected sensor side is {side} (yes/no): ", input_func
        ):
            raise _OperatorCanceled
        if not _confirmed(
            "Confirm the sensor is placed in the Ophir 0 cm fixture (yes/no): ",
            input_func,
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
            sdk_version=getattr(omotion, "__version__", "unavailable"),
            started_at=datetime.now(timezone.utc),
        )
        result = workflow.run(request)
        cleanup_failure = close_bench_capturing(bench)
        bench = None
        meter = None
        result = apply_cleanup_failure(result, cleanup_failure, recorder)
        return finalize_run_artifacts(
            request=request,
            result=result,
            recorder=recorder,
            report_factory=report_factory,
            procedure_revision=procedure_revision,
            output_func=output_func,
        )
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
