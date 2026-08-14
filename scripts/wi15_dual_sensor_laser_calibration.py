"""Operator entry point for WI-00015 dual-sensor laser calibration."""

from __future__ import annotations

import argparse
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
from omotion.calibration.laser_hardware import (
    MotionLaserCalibrationBench,
    OphirEnergyMeter,
)
from omotion.calibration.reporting import JsonRunRecorder
from omotion.calibration.script_support import (
    APPROVED_PROCEDURE_REVISION,
    PROCEDURE_ID,
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


recorder_factory = JsonRunRecorder
meter_factory = OphirEnergyMeter
bench_factory = MotionLaserCalibrationBench
workflow_factory = DualSensorLaserCalibrationWorkflow
report_factory = DualSensorHtmlRunReport


def _parser() -> argparse.ArgumentParser:
    return make_parser(
        __doc__,
        lambda parser: parser.add_argument("--fixture-calibration-status"),
    )


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
