"""Operator entry point for WI-00015 Safety Calibration."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import logging
import math
from pathlib import Path
import time
from typing import Callable, Sequence

import omotion
from omotion.calibration.reporting import JsonRunRecorder
from omotion.calibration.safety import PowerCycleEvidence, ShippingTopology
from omotion.calibration.safety_hardware import MotionSafetyCalibrationBench
from omotion.calibration.safety_report import SafetyCalibrationHtmlRunReport
from omotion.calibration.safety_workflow import (
    SafetyCalibrationRequest,
    SafetyCalibrationWorkflow,
)
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


# The pane terminal is the operator surface: the SDK's connection-retry
# warnings during the observed power cycle are expected churn, not operator
# information, so keep the child's SDK logging to errors only.
logging.getLogger("openmotion").setLevel(logging.ERROR)

recorder_factory = JsonRunRecorder
bench_factory = MotionSafetyCalibrationBench
workflow_factory = SafetyCalibrationWorkflow
report_factory = SafetyCalibrationHtmlRunReport


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
        if not math.isfinite(minimum_off_s) or minimum_off_s <= 0:
            raise ValueError("minimum_off_s must be finite and positive")

        off_requested_at = self._utc_now()
        self._output(
            "6A. Power the console OFF and back ON now. The procedure observes "
            f"the cycle itself; keep power off at least {minimum_off_s:g} "
            "second(s) or the run fails."
        )
        if not self._wait_for_state(
            is_console_connected, False, self._disconnect_timeout_s
        ):
            self._output(
                f"Console disconnection was not observed within "
                f"{self._disconnect_timeout_s:g} seconds."
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
            "6B. Console disconnection observed; waiting for reconnection."
        )
        if not self._wait_for_state(
            is_console_connected, True, self._reconnect_timeout_s
        ):
            self._output("Console reconnection was not observed before timeout.")
            return PowerCycleEvidence(
                off_requested_at,
                disconnect_observed_at,
                disconnect_observed_at + timedelta(seconds=minimum_off_s),
                None,
                None,
                self._clock() - disconnected_at_clock,
                True,
                False,
                False,
                None,
                expected_console_serial,
                None,
            )

        reconnect_observed_at = self._utc_now()
        off_duration_s = self._clock() - disconnected_at_clock
        # The earliest instant power-on was permitted; a reconnect before it
        # means the operator cycled too quickly, and the workflow's dwell
        # validation fails the run on this evidence.
        on_allowed_at = disconnect_observed_at + timedelta(seconds=minimum_off_s)
        try:
            console_serial_after = read_console_serial()
        except Exception:
            console_serial_after = None
        self._output("6C. Console reconnection observed; verifying persisted settings.")
        return PowerCycleEvidence(
            off_requested_at,
            disconnect_observed_at,
            on_allowed_at,
            reconnect_observed_at,
            reconnect_observed_at,
            off_duration_s,
            True,
            True,
            True,
            "Observed disconnect and reconnect on the same long-lived Motion "
            "console handle without operator confirmation gates.",
            expected_console_serial,
            console_serial_after,
        )


def _parser() -> argparse.ArgumentParser:
    return make_parser(
        __doc__,
        lambda parser: parser.add_argument(
            "--shipping-topology",
            choices=tuple(item.value for item in ShippingTopology),
        ),
    )


def _shipping_topology(
    value: str | None,
    input_func: Callable[[str], str],
    output_func: Callable[[str], None],
) -> ShippingTopology:
    candidate = value
    while True:
        if candidate is None:
            candidate = input_func(
                "Declared shipping topology "
                "(single-left/single-right/dual/console-only): "
            )
        try:
            topology = ShippingTopology(candidate.strip().lower())
        except (AttributeError, ValueError):
            output_func(
                "Enter exactly single-left, single-right, dual, or console-only."
            )
            candidate = None
            continue
        output_func(f"Declared shipping topology: {topology.value}.")
        return topology


def _topology_instruction(topology: ShippingTopology) -> str:
    descriptions = {
        ShippingTopology.SINGLE_LEFT: "the left sensor module only",
        ShippingTopology.SINGLE_RIGHT: "the right sensor module only",
        ShippingTopology.DUAL: "both the left and right sensor modules",
        ShippingTopology.CONSOLE_ONLY: "no sensor modules (console only)",
    }
    return descriptions[topology]


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
        build_revision = args.build_revision or "unspecified"
        fixture_id = _required_value(args.fixture_id, "Bench or fixture ID: ", input_func)
        topology = _shipping_topology(
            args.shipping_topology, input_func, output_func
        )
        procedure_revision = _required_value(
            args.procedure_revision, "Procedure revision: ", input_func
        )
        instruction = _topology_instruction(topology)
        if not _confirmed(
            f"Please connect the {topology.value} topology: {instruction}. "
            "Extra modules may remain connected during the console-only ADC "
            "portion. Confirm when ready [y/N]: ",
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
        cleanup_failure = close_bench_capturing(bench)
        bench = None
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
        output_func(f"Safety Calibration failed before a terminal report: {exc}")
        return 1
    finally:
        if bench is not None:
            _close_best_effort(bench)


if __name__ == "__main__":
    raise SystemExit(main())
