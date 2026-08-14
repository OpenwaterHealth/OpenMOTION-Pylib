"""Operator entry point for WI-00015 Measurement Calibration (one sensor).

Thin bench runner around the SDK calibration engine (CalibrationWorkflow via
MotionInterface.start_calibration) - the same engine behind the app's
Calibrate button: laser-on collection scan, per-camera mean/contrast
computation, console EEPROM write, validation scan. One sensor module is
calibrated per run; run it once per shipping side. The full auditable
Measurement Calibration workflow (evidence contracts, HTML report) is
specified in docs/superpowers/specs/2026-08-12-wi15-measurement-calibration.md
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
    make_parser,
    required_value as _required_value,
    utc_run_id as _run_id,
)


interface_factory = MotionInterface

# Scan parameters mirror the bloodflow-app's defaults (motion_connector cfg
# keys calibration_scan_duration_sec / calibration_scan_delay_sec /
# max_calibration_time_sec).
CAL_SCAN_DURATION_SEC = 5
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
            candidate = input_func("Sensor side to calibrate (left/right): ")
        side = candidate.strip().lower()
        if side in ("left", "right"):
            output_func(f"Calibrating sensor side: {side}")
            return side
        output_func("Enter exactly left or right.")
        candidate = None


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
    args = _parser().parse_args(argv)
    try:
        operator = _required_value(args.operator, "Operator: ", input_func)
        side = _selected_side(args.side, input_func, output_func)
        if not args.phantom_confirmed and not _confirmed(
            f"Confirm the {side} module is on the static phantom with the "
            "included weight (WI Figure H), covers removed, and the setup "
            "will not be touched while calibration runs. NEVER calibrate a "
            "module seated in the 0 cm energy-meter fixture. (yes/no): ",
            input_func,
        ):
            raise _OperatorCanceled
    except (EOFError, KeyboardInterrupt, _OperatorCanceled):
        output_func("Measurement Calibration canceled before hardware construction.")
        return 1

    thresholds, thresholds_label = _build_thresholds(
        args.thresholds_json, args.bench_thresholds)
    output_func(f"thresholds: {thresholds_label}")
    if args.bench_thresholds:
        output_func("*** WARNING: bench mode - a PASSED result does NOT "
                    "certify image brightness ***")

    run_id = _run_id()
    output_root = Path(args.output_dir) / f"measurement-calibration-{run_id}"
    output_root.mkdir(parents=True, exist_ok=True)

    iface = interface_factory(
        data_dir=str(output_root / "scans"), operator_id=operator)
    iface.start()
    try:
        if not iface.wait_for_ready(console=True, sensors=0,
                                    timeout=READY_TIMEOUT_S):
            output_func("FAIL: console not ready")
            return 1
        # Give the sensor side a moment, then require the chosen module.
        deadline = time.time() + READY_TIMEOUT_S
        while time.time() < deadline:
            _, l_ok, r_ok = iface.is_device_connected()
            if l_ok if side == "left" else r_ok:
                break
            time.sleep(1.0)
        _, l_ok, r_ok = iface.is_device_connected()
        if not (l_ok if side == "left" else r_ok):
            output_func(f"FAIL: {side} sensor module not connected "
                        f"(left={l_ok}, right={r_ok})")
            return 1

        # Cold-camera bring-up: scans only stream from powered AND configured
        # cameras, and a bare script must do that itself - the clinical app
        # does it on connect, the engineering app does not. Power the chosen
        # side, then configure exactly the cameras this run uses.
        sensor = iface.left if side == "left" else iface.right
        if not sensor.enable_camera_power(0xFF):
            output_func("FAIL: camera power enable failed")
            return 1
        configured = threading.Event()
        configure_holder: dict = {}

        def on_configured(result) -> None:
            configure_holder["result"] = result
            configured.set()

        output_func("configuring cameras (this can take a minute) ...")
        if not iface.start_configure_camera_sensors(
            ConfigureRequest(
                left_camera_mask=0xFF if side == "left" else 0x00,
                right_camera_mask=0xFF if side == "right" else 0x00,
                power_off_unused_cameras=False,
            ),
            on_complete_fn=on_configured,
        ):
            output_func("FAIL: camera configuration refused to start")
            return 1
        if not configured.wait(CONFIGURE_TIMEOUT_S):
            output_func("FAIL: camera configuration did not complete")
            return 1
        configure_result = configure_holder["result"]
        if not getattr(configure_result, "ok", False):
            output_func("FAIL: camera configuration failed: "
                        f"{getattr(configure_result, 'error', '')}")
            return 1

        # Cold-start prerequisite: after any power cycle the laser-driver
        # registers are cleared. This also applies the tuned EPROM overrides
        # (TA_CURRENT_DRV etc.) written by the laser-calibration flow.
        if not iface.apply_laser_power():
            output_func("FAIL: apply_laser_power failed")
            return 1

        cfg = iface.console.read_config()
        cfg_data = (cfg.json_data or {}) if cfg else {}
        laser_point = {key: cfg_data.get(key)
                       for key in ("TA_PULSE_WIDTH", "TA_CURRENT_DRV")}
        output_func(f"tuned laser point from EPROM: {laser_point}")

        request = CalibrationRequest(
            operator_id=operator,
            output_dir=str(output_root),
            left_camera_mask=0xFF if side == "left" else 0x00,
            right_camera_mask=0xFF if side == "right" else 0x00,
            thresholds=thresholds,
            duration_sec=CAL_SCAN_DURATION_SEC,
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
            output_func(f"  [{dt.datetime.now():%H:%M:%S}] {stage}")

        def confirm_fn(rows) -> bool:
            """Pre-write gate: show what measured low, then always refuse.

            A below-threshold calibration is never written - there is no
            consent path. The rows are printed so the operator can see
            exactly which cameras failed the spec.
            """
            output_func("Below-threshold gate fired. Measured rows:")
            output_func(f"  {'side':<6} {'cam':>3} {'mean':>10} {'avg_contrast':>13}")
            for row in rows:
                # Cameras display 1-8, matching the engine's L#/R# labels.
                output_func(f"  {row.side:<6} {row.cam_id + 1:>3} "
                            f"{row.mean:>10.3f} {row.avg_contrast:>13.4f}")
            output_func("A below-threshold calibration is never written to "
                        "the console.")
            return False

        output_func(f"*** CALIBRATION STARTING (side={side}, laser will "
                    "fire; do not touch the setup) ***")
        if not iface.start_calibration(request, on_complete_fn=on_complete,
                                       on_progress_fn=on_progress,
                                       on_confirm_fn=confirm_fn):
            output_func("FAIL: start_calibration refused (already running?)")
            return 1
        if not done.wait(CAL_MAX_DURATION_SEC + 60):
            output_func("FAIL: calibration did not complete within the "
                        "watchdog window")
            iface.cancel_calibration()
            return 1

        result = holder["result"]
        outcome = getattr(result.outcome, "value", str(result.outcome))
        output_func(f"outcome: {outcome}"
                    + (f"  error: {result.error}" if result.error else ""))
        if result.rows:
            output_func(f"  {'side':<6} {'cam':>3} {'mean':>10} "
                        f"{'avg_contrast':>13} {'bfi':>8} {'bvi':>8}")
            for row in result.rows:
                # Cameras display 1-8, matching the engine's L#/R# labels.
                output_func(
                    f"  {row.side:<6} {row.cam_id + 1:>3} {row.mean:>10.3f} "
                    f"{row.avg_contrast:>13.4f} {row.bfi:>8.3f} "
                    f"{row.bvi:>8.3f}")
        if result.csv_path:
            output_func(f"csv evidence: {result.csv_path}")
        if result.json_path:
            output_func(f"json evidence: {result.json_path}")

        passed = outcome == "passed"
        output_func(f"Terminal status: {'passed' if passed else outcome}")
        if passed:
            output_func("Reminder: a shipping dual unit needs the other side "
                        "calibrated in its own run.")
        return 0 if passed else 1
    except Exception as exc:
        output_func(f"Measurement Calibration failed: {exc}")
        return 1
    finally:
        try:
            iface.stop()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
