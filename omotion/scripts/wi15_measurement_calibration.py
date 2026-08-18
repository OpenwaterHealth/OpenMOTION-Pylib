"""Operator entry point for WI-00015 Measurement Calibration (one sensor).

Thin bench runner around the SDK calibration engine (CalibrationWorkflow via
MotionInterface.start_calibration) - the same engine behind the app's
Calibrate button: laser-on collection scan, per-camera mean/contrast
computation, console EEPROM write, validation scan. One sensor module is
calibrated per run; run it once per shipping side. The full auditable
Measurement Calibration workflow (evidence contracts, HTML report) is
specified in docs/calibration/2026-08-12-wi15-measurement-calibration.md
and remains future work.

LASER SAFETY: the calibration scan fires the laser. The module must be on the
static phantom with the included weight (WI Figure H) - never run this with a
module in the 0 cm energy-meter fixture; with permissive thresholds the
engine would write a garbage calibration block over a good one. The phantom
attestation prompt (or --phantom-confirmed) is mandatory for every run.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import threading
import time
from typing import Callable, Sequence

from omotion import CalibrationRequest, CalibrationThresholds
from omotion.MotionInterface import MotionInterface
from omotion.ScanWorkflow import ConfigureRequest
from omotion.calibration.script_support import (
    OperatorCanceled as _OperatorCanceled,
    confirmed as _confirmed,
    emit_detail as _emit_detail,
    forward_library_logging,
    make_parser,
    required_value as _required_value,
    utc_run_id as _run_id,
)


interface_factory = MotionInterface

# Scan parameters follow the approved process addendum: 15-second
# calibration scan (also the bloodflow-app's shipped
# calibration_scan_duration_sec, not motion_connector's code fallback of 5)
# and 2-second validation scan (CalibrationRequest.validation_duration_sec).
# The scan_delay_sec leading skip applies to both sub-scans.
CAL_SCAN_DURATION_SEC = 15
VAL_SCAN_DURATION_SEC = 2
CAL_SCAN_DELAY_SEC = 1
CAL_MAX_DURATION_SEC = 600
READY_TIMEOUT_S = 20.0
CONFIGURE_TIMEOUT_S = 90.0

# Re-sent to the console before each sub-scan so the firmware-side
# fsync_counter resets and the dark schedule starts aligned (the app's flows
# do the same; see the trigger_config note on CalibrationRequest). This is
# the camera/FSIN trigger payload - the laser drive point itself comes from
# the tuned EPROM values via apply_laser_power.
STANDARD_TRIGGER_CONFIG = {
    "TriggerStatus": 2,
    "TriggerFrequencyHz": 40,
    "TriggerPulseWidthUsec": 500,
    "LaserPulseDelayUsec": 100,
    "LaserPulseWidthUsec": 500,
    "LaserPulseSkipInterval": 600,
    "LaserPulseSkipDelayUsec": 1800,
    "EnableSyncOut": True,
    "EnableTaTrigger": True,
}

# Per-camera acceptance thresholds: the FACTORY values, mirroring the
# bloodflow-app's live config (config/app_config.json ft_* keys):
# absolute-brightness minimums per camera (corner cameras 40, inner 80),
# contrast 0.25, SPEC-69 BFI/BVI, dark <= 3.0.
#
# The SPEC-69 BFI/BVI gates alone are nearly self-fulfilling right after
# calibration (i_max/c_max are normalized to the just-measured values, so the
# validation scan reads BVI ~5 / BFI ~0 by construction). The mean/contrast
# minimums are the only ABSOLUTE-brightness gates - without them, "passed"
# says nothing about signal level. BFI bounds MUST straddle zero: on a static
# phantom BFI legitimately reads slightly negative.
#
# On a dim dev bench use --bench-thresholds (disables the mean/contrast
# gates, loudly) or --thresholds-json for custom values.
FACTORY_THRESHOLDS = {
    "min_mean_per_camera": [40.0, 80.0, 80.0, 80.0, 80.0, 80.0, 80.0, 40.0],
    "min_contrast_per_camera": [0.25] * 8,
    "min_bfi_per_camera": [-0.5] * 8,   # SPEC-69 BFI Min
    "max_bfi_per_camera": [0.5] * 8,    # SPEC-69 BFI Max
    "min_bvi_per_camera": [4.5] * 8,    # SPEC-69 BVI Min
    "max_bvi_per_camera": [5.5] * 8,    # SPEC-69 BVI Max
    "max_dark_per_camera": [3.0] * 8,
}
BENCH_THRESHOLDS = {
    **FACTORY_THRESHOLDS,
    "min_mean_per_camera": [0.0] * 8,
    "min_contrast_per_camera": [0.0] * 8,
}


def _parser() -> argparse.ArgumentParser:
    def extra(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--side", choices=["left", "right"])
        parser.add_argument(
            "--phantom-confirmed", action="store_true",
            help="attest the module is on the static phantom with weight "
                 "(WI Figure H) and will not be touched")
        parser.add_argument(
            "--bench-thresholds", action="store_true",
            help="disable the absolute mean/contrast gates (dim dev bench). "
                 "PASSED then does NOT certify signal level.")
        parser.add_argument(
            "--thresholds-json", default=None,
            help="JSON file of CalibrationThresholds overrides; "
                 "default: factory values")

    return make_parser(__doc__, extra)


def _selected_side(
    value: str | None,
    input_func: Callable[[str], str],
    output_func: Callable[[str], None],
) -> str:
    candidate = value
    while True:
        if candidate is None:
            candidate = input_func("Which sensor do you want to calibrate? (left/right): ")
        side = candidate.strip().lower()
        if side in ("left", "right"):
            return side
        output_func("Please answer left or right.")
        candidate = None


# Plain lines for the calibration engine's progress stages; unknown stages
# surface as detail lines only. The engine names stay engineer-speak, so the
# raw token is always echoed as detail alongside the plain line.
_STAGE_LINES = {
    "flash_sensors": "Checking the camera programs ...",
    "calibration_scan": (
        f"Measuring for {CAL_SCAN_DURATION_SEC} seconds. "
        "Do not touch the setup."
    ),
    "compute_calibration": "Computing the calibration values ...",
    "gate": "Checking the values against the limits ...",
    "write_calibration": "Saving the calibration to the console ...",
    "validation_scan": "Running a short check scan ...",
    "evaluate": "Checking the final result ...",
}


def _build_thresholds(
    path: str | None, bench: bool
) -> tuple[CalibrationThresholds, str]:
    """Resolve thresholds. Returns (thresholds, source-label-for-the-record)."""
    if path:
        data = dict(FACTORY_THRESHOLDS)
        with open(path, "r", encoding="utf-8") as f:
            data.update(json.load(f))
        return CalibrationThresholds(**data), f"custom ({os.path.basename(path)})"
    if bench:
        return (CalibrationThresholds(**BENCH_THRESHOLDS),
                "BENCH: mean/contrast gates DISABLED - passed does not "
                "certify signal level")
    return (CalibrationThresholds(**FACTORY_THRESHOLDS),
            "factory (mean 40/80, contrast 0.25, SPEC-69 BFI/BVI, dark 3.0)")


def main(
    argv: Sequence[str] | None = None,
    *,
    input_func: Callable[[str], str] | None = None,
    output_func: Callable[[str], None] | None = None,
) -> int:
    """Confirm phantom placement, calibrate one side, and report the outcome."""
    input_func = input if input_func is None else input_func
    output_func = print if output_func is None else output_func
    forward_library_logging()
    args = _parser().parse_args(argv)

    def fail(problem: str) -> int:
        output_func(f"Problem: {problem}")
        output_func("Final result: FAIL")
        return 1

    try:
        operator = _required_value(args.operator, "Operator: ", input_func)
        side = _selected_side(args.side, input_func, output_func)
        if not args.phantom_confirmed and not _confirmed(
            f"Put the {side} sensor on the phantom with the weight "
            "(WI Figure H). Remove the covers. Do not touch the setup "
            "during the test. NEVER use the 0 cm energy-meter fixture "
            "for this. Ready? (yes/no): ",
            input_func,
        ):
            raise _OperatorCanceled
    except (EOFError, KeyboardInterrupt, _OperatorCanceled):
        output_func("Measurement Calibration canceled. Nothing was changed.")
        return 1

    thresholds, thresholds_label = _build_thresholds(
        args.thresholds_json, args.bench_thresholds)
    _emit_detail(output_func, f"limits: {thresholds_label}")
    if args.bench_thresholds:
        output_func("*** WARNING: bench mode - a PASS here does NOT "
                    "prove image brightness ***")

    run_id = _run_id()
    output_root = Path(args.output_dir) / f"measurement-calibration-{run_id}"
    output_root.mkdir(parents=True, exist_ok=True)

    output_func(f"Step 1 of 3: Checking the console and the {side} sensor ...")
    iface = interface_factory(
        data_dir=str(output_root / "scans"), operator_id=operator)
    iface.start()
    try:
        if not iface.wait_for_ready(console=True, sensors=0,
                                    timeout=READY_TIMEOUT_S):
            return fail("the console is not connected.")
        # Give the sensor side a moment, then require the chosen module.
        deadline = time.time() + READY_TIMEOUT_S
        while time.time() < deadline:
            _, l_ok, r_ok = iface.is_device_connected()
            if l_ok if side == "left" else r_ok:
                break
            time.sleep(1.0)
        _, l_ok, r_ok = iface.is_device_connected()
        if not (l_ok if side == "left" else r_ok):
            _emit_detail(output_func, f"connected: left={l_ok}, right={r_ok}")
            return fail(f"the {side} sensor is not connected.")

        # Cold-camera bring-up: scans only stream from powered AND configured
        # cameras, and a bare script must do that itself - the clinical app
        # does it on connect, the engineering app does not. Power the chosen
        # side, then configure exactly the cameras this run uses.
        sensor = iface.left if side == "left" else iface.right
        output_func("Step 2 of 3: Preparing the cameras. "
                    "This can take one minute ...")
        if not sensor.enable_camera_power(0xFF):
            return fail("could not turn on the cameras.")
        configured = threading.Event()
        configure_holder: dict = {}

        def on_configured(result) -> None:
            configure_holder["result"] = result
            configured.set()

        if not iface.start_configure_camera_sensors(
            ConfigureRequest(
                left_camera_mask=0xFF if side == "left" else 0x00,
                right_camera_mask=0xFF if side == "right" else 0x00,
                power_off_unused_cameras=False,
            ),
            on_complete_fn=on_configured,
        ):
            return fail("camera setup could not start.")
        if not configured.wait(CONFIGURE_TIMEOUT_S):
            return fail("camera setup did not finish.")
        configure_result = configure_holder["result"]
        if not getattr(configure_result, "ok", False):
            return fail("camera setup failed: "
                        f"{getattr(configure_result, 'error', '')}")

        # Cold-start prerequisite: after any power cycle the laser-driver
        # registers are cleared. This also applies the tuned EPROM overrides
        # (TA_CURRENT_DRV etc.) written by the laser-calibration flow.
        if not iface.apply_laser_power():
            return fail("could not set the laser power.")

        cfg = iface.console.read_config()
        cfg_data = (cfg.json_data or {}) if cfg else {}
        laser_point = {key: cfg_data.get(key)
                       for key in ("TA_PULSE_WIDTH", "TA_CURRENT_DRV")}
        _emit_detail(
            output_func,
            "console laser drive: "
            + ", ".join(f"{key}={value}" for key, value in laser_point.items()),
        )

        request = CalibrationRequest(
            operator_id=operator,
            output_dir=str(output_root),
            left_camera_mask=0xFF if side == "left" else 0x00,
            right_camera_mask=0xFF if side == "right" else 0x00,
            thresholds=thresholds,
            duration_sec=CAL_SCAN_DURATION_SEC,
            validation_duration_sec=VAL_SCAN_DURATION_SEC,
            scan_delay_sec=CAL_SCAN_DELAY_SEC,
            max_duration_sec=CAL_MAX_DURATION_SEC,
            trigger_config=dict(STANDARD_TRIGGER_CONFIG),
            notes=f"WI-00015 Measurement Calibration, side={side}, "
                  f"run {run_id}, thresholds: {thresholds_label}",
        )

        done = threading.Event()
        holder: dict = {}

        def on_complete(result) -> None:
            holder["result"] = result
            done.set()

        def on_progress(stage: str) -> None:
            line = _STAGE_LINES.get(stage)
            if line is not None:
                output_func(line)
            _emit_detail(
                output_func, f"[{dt.datetime.now():%H:%M:%S}] stage: {stage}"
            )

        def confirm_fn(rows) -> bool:
            """Pre-write gate: show what measured low, then always refuse.

            A below-threshold calibration is never written - there is no
            consent path. The rows are printed so the operator can see
            exactly which cameras failed the spec.
            """
            output_func("Camera values are below the limit. Measured values:")
            output_func(f"  {'side':<6} {'cam':>3} {'mean':>10} {'avg_contrast':>13}")
            for row in rows:
                # Cameras display 1-8, matching the engine's L#/R# labels.
                output_func(f"  {row.side:<6} {row.cam_id + 1:>3} "
                            f"{row.mean:>10.3f} {row.avg_contrast:>13.4f}")
            output_func("A result below the limit is never saved to "
                        "the console.")
            return False

        output_func(f"Step 3 of 3: Calibrating the {side} sensor. "
                    "The laser will turn ON.")
        output_func("*** Do not touch the setup while it runs. ***")
        if not iface.start_calibration(request, on_complete_fn=on_complete,
                                       on_progress_fn=on_progress,
                                       on_confirm_fn=confirm_fn):
            return fail("could not start (is another calibration running?)")
        if not done.wait(CAL_MAX_DURATION_SEC + 60):
            iface.cancel_calibration()
            return fail("calibration took too long and was stopped.")

        result = holder["result"]
        outcome = getattr(result.outcome, "value", str(result.outcome))
        passed = outcome == "passed"
        _emit_detail(output_func, f"outcome: {outcome}")
        if result.rows:
            _emit_detail(output_func,
                         f"{'side':<6} {'cam':>3} {'mean':>10} "
                         f"{'avg_contrast':>13} {'bfi':>8} {'bvi':>8}")
            for row in result.rows:
                # Cameras display 1-8, matching the engine's L#/R# labels.
                _emit_detail(
                    output_func,
                    f"{row.side:<6} {row.cam_id + 1:>3} {row.mean:>10.3f} "
                    f"{row.avg_contrast:>13.4f} {row.bfi:>8.3f} "
                    f"{row.bvi:>8.3f}")
        if passed:
            output_func("All cameras are within the limits.")
        elif result.error:
            output_func(f"Problem: {result.error}")
        if result.csv_path:
            output_func(f"Saved data (CSV): {result.csv_path}")
        if result.json_path:
            output_func(f"Saved data (JSON): {result.json_path}")

        if passed:
            verdict = "PASS"
        elif outcome == "canceled":
            verdict = "CANCELED"
        else:
            verdict = "FAIL"
        output_func(f"Final result: {verdict}")
        if passed:
            output_func("Note: for a two-sensor unit, also run this for "
                        "the other side.")
        return 0 if passed else 1
    except Exception as exc:
        output_func(f"Measurement Calibration stopped with an error: {exc}")
        output_func("Final result: FAIL")
        return 1
    finally:
        try:
            iface.stop()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
