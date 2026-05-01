"""Calibration procedure orchestrator.

Submits two short scans through ScanWorkflow, computes (2, 8)
calibration arrays from scan #1, writes them to the console (which
auto-refreshes the SDK cache), runs scan #2 with the freshly-written
calibration, writes a per-camera CSV with mean/contrast/BFI/BVI plus
pass/fail vs caller-supplied thresholds, and returns a
CalibrationResult.

The workflow does not talk to USB/UART directly. It calls into the
existing ScanWorkflow and processes the raw-histogram CSVs ScanWorkflow
produces.
"""
from __future__ import annotations

import csv
import datetime
import logging
import os
import threading
from dataclasses import dataclass
from typing import Callable, Optional, TYPE_CHECKING

import numpy as np

from omotion import _log_root
from omotion.Calibration import Calibration
from omotion.config import (
    CALIBRATION_DEFAULT_MAX_DURATION_SEC,
    CALIBRATION_DEFAULT_SCAN_DELAY_SEC,
    CALIBRATION_I_MAX_MULTIPLIER,
    CAMS_PER_MODULE,
    CAPTURE_HZ,
    MODULES,
)

if TYPE_CHECKING:
    from omotion.MotionInterface import MotionInterface

logger = logging.getLogger(
    f"{_log_root}.CalibrationWorkflow" if _log_root else "CalibrationWorkflow"
)


@dataclass
class CalibrationThresholds:
    """Per-camera lower bounds (length 8, indexed by cam_id 0..7).
    Applied symmetrically to left and right modules."""
    min_mean_per_camera: list[float]
    min_contrast_per_camera: list[float]
    min_bfi_per_camera: list[float]
    min_bvi_per_camera: list[float]


@dataclass
class CalibrationRequest:
    operator_id: str
    output_dir: str
    left_camera_mask: int
    right_camera_mask: int
    thresholds: CalibrationThresholds
    duration_sec: int  # required; caller supplies from config
    scan_delay_sec: int = CALIBRATION_DEFAULT_SCAN_DELAY_SEC
    max_duration_sec: int = CALIBRATION_DEFAULT_MAX_DURATION_SEC
    notes: str = ""


@dataclass
class CalibrationResultRow:
    camera_index: int
    side: str
    cam_id: int
    mean: float
    avg_contrast: float
    bfi: float
    bvi: float
    mean_test: str
    contrast_test: str
    bfi_test: str
    bvi_test: str
    security_id: str
    hwid: str


@dataclass
class CalibrationResult:
    ok: bool
    passed: bool
    canceled: bool
    error: str
    csv_path: str
    calibration: Optional[Calibration]
    rows: list[CalibrationResultRow]
    calibration_scan_left_path: str
    calibration_scan_right_path: str
    validation_scan_left_path: str
    validation_scan_right_path: str
    started_timestamp: str


# ---------------------------------------------------------------------------
# Pure compute helpers — no hardware, no UART. Tested in
# tests/test_calibration_workflow_compute.py.
# ---------------------------------------------------------------------------

from omotion.MotionProcessing import (
    Sample,
    create_science_pipeline,
    feed_pipeline_from_csv,
)


def _collect_samples_from_csvs(
    *,
    left_csv: Optional[str],
    right_csv: Optional[str],
    skip_leading_frames: int,
    left_camera_mask: int = 0xFF,
    right_camera_mask: int = 0xFF,
    calibration: Optional[Calibration] = None,
) -> list[Sample]:
    """Run the science pipeline against raw histogram CSVs and return all
    light-frame Samples whose absolute_frame_id is at or beyond
    ``skip_leading_frames``.

    Dark frames are skipped by construction: ``on_uncorrected_fn`` only
    fires for non-dark frames.

    Calibration defaults to ``Calibration.default()``. For the calibration
    phase, the BFI/BVI fields on the returned Samples are not used —
    only ``mean`` and ``contrast``, which are upstream of the calibration
    math. For the validation phase, callers pass the freshly-written
    console calibration so BFI/BVI reflect it.
    """
    cal = calibration or Calibration.default()
    samples: list[Sample] = []

    def _on_sample(s: Sample) -> None:
        if s.absolute_frame_id >= skip_leading_frames:
            samples.append(s)

    pipeline = create_science_pipeline(
        left_camera_mask=left_camera_mask,
        right_camera_mask=right_camera_mask,
        bfi_c_min=cal.c_min,
        bfi_c_max=cal.c_max,
        bfi_i_min=cal.i_min,
        bfi_i_max=cal.i_max,
        on_uncorrected_fn=_on_sample,
    )
    try:
        if left_csv:
            feed_pipeline_from_csv(left_csv, "left", pipeline)
        if right_csv:
            feed_pipeline_from_csv(right_csv, "right", pipeline)
    finally:
        # stop() drains the worker queue and joins the thread.
        pipeline.stop(timeout=30.0)
    samples.sort(key=lambda s: (s.side, s.cam_id, s.absolute_frame_id))
    return samples


class DegenerateCalibrationError(RuntimeError):
    """Raised when an active camera's calibration scan produces unusable
    data (zero / negative aggregates), making BFI/BVI math impossible."""


def _camera_active(mask: int, cam_id: int) -> bool:
    return bool(mask & (1 << cam_id))


def compute_calibration_from_csvs(
    *,
    left_csv: Optional[str],
    right_csv: Optional[str],
    left_camera_mask: int,
    right_camera_mask: int,
    skip_leading_frames: int,
) -> Calibration:
    """Compute (2, 8) C_max and I_max arrays from raw histogram CSVs.

    C_min and I_min are zero. ``C_max[m, c]`` is the average light-frame
    contrast for camera ``(m, c)``; ``I_max[m, c]`` is
    ``CALIBRATION_I_MAX_MULTIPLIER * average light-frame mean``.

    Inactive cameras (mask bit clear) get ``Calibration.default()`` values
    so monotonicity always holds. Active cameras with zero / negative
    aggregates raise :class:`DegenerateCalibrationError`.

    Returns ``Calibration(source="console")`` so callers can pass it
    straight to :meth:`omotion.MotionInterface.write_calibration`.
    """
    samples = _collect_samples_from_csvs(
        left_csv=left_csv, right_csv=right_csv,
        skip_leading_frames=skip_leading_frames,
        left_camera_mask=left_camera_mask,
        right_camera_mask=right_camera_mask,
    )

    defaults = Calibration.default()
    c_max = defaults.c_max.copy()
    i_max = defaults.i_max.copy()
    c_min = np.zeros_like(c_max)
    i_min = np.zeros_like(i_max)

    masks = (left_camera_mask, right_camera_mask)

    for module_idx, side in enumerate(("left", "right")):
        mask = masks[module_idx]
        for cam_id in range(CAMS_PER_MODULE):
            if not _camera_active(mask, cam_id):
                continue  # inactive — keep default value
            cam_samples = [
                s for s in samples
                if s.side == side and s.cam_id == cam_id
            ]
            if not cam_samples:
                raise DegenerateCalibrationError(
                    f"active camera ({side}, cam_id={cam_id}) produced "
                    f"no light-frame samples after skip_leading_frames="
                    f"{skip_leading_frames}; calibration aborted."
                )
            mean_avg = float(np.mean([s.mean for s in cam_samples]))
            contrast_avg = float(np.mean([s.contrast for s in cam_samples]))
            new_c_max = contrast_avg
            new_i_max = CALIBRATION_I_MAX_MULTIPLIER * mean_avg
            if new_c_max <= 0.0 or new_i_max <= 0.0:
                raise DegenerateCalibrationError(
                    f"active camera ({side}, cam_id={cam_id}) produced "
                    f"zero or negative aggregate (C_max={new_c_max:.4f}, "
                    f"I_max={new_i_max:.4f}); calibration aborted."
                )
            c_max[module_idx, cam_id] = new_c_max
            i_max[module_idx, cam_id] = new_i_max

    return Calibration(
        c_min=c_min, c_max=c_max,
        i_min=i_min, i_max=i_max,
        source="console",
    )


def _threshold_test(value: float, thresholds: list[float], cam_id: int) -> str:
    """PASS if the threshold list doesn't cover this cam_id, or value
    >= threshold."""
    if cam_id >= len(thresholds):
        return "PASS"
    t = thresholds[cam_id]
    if t is None or not isinstance(t, (int, float)):
        return "PASS"
    return "PASS" if value >= float(t) else "FAIL"


def build_result_rows(
    *,
    left_csv: Optional[str],
    right_csv: Optional[str],
    left_camera_mask: int,
    right_camera_mask: int,
    skip_leading_frames: int,
    thresholds: CalibrationThresholds,
    sensor_left,            # MotionSensor or None — for cached IDs
    sensor_right,           # MotionSensor or None
    calibration: Optional[Calibration],
) -> list[CalibrationResultRow]:
    """Aggregate per-camera mean/contrast/BFI/BVI from validation-scan
    CSVs and apply pass/fail thresholds."""
    samples = _collect_samples_from_csvs(
        left_csv=left_csv, right_csv=right_csv,
        skip_leading_frames=skip_leading_frames,
        left_camera_mask=left_camera_mask,
        right_camera_mask=right_camera_mask,
        calibration=calibration,
    )

    rows: list[CalibrationResultRow] = []
    masks = (left_camera_mask, right_camera_mask)
    sensors = (sensor_left, sensor_right)

    for module_idx, side in enumerate(("left", "right")):
        mask = masks[module_idx]
        sensor = sensors[module_idx]
        for cam_id in range(CAMS_PER_MODULE):
            if not _camera_active(mask, cam_id):
                continue
            cam_samples = [
                s for s in samples
                if s.side == side and s.cam_id == cam_id
            ]
            if not cam_samples:
                continue   # silently drop — no data for this active cam

            mean_val = float(np.mean([s.mean for s in cam_samples]))
            contrast_val = float(np.mean([s.contrast for s in cam_samples]))
            bfi_val = float(np.mean([s.bfi for s in cam_samples]))
            bvi_val = float(np.mean([s.bvi for s in cam_samples]))

            security_id = ""
            hwid = ""
            if sensor is not None and hasattr(sensor, "get_cached_camera_security_uid"):
                try:
                    security_id = str(sensor.get_cached_camera_security_uid(cam_id) or "")
                except Exception:
                    security_id = ""
                try:
                    hwid = str(sensor.get_cached_hardware_id() or "")
                except Exception:
                    hwid = ""

            rows.append(CalibrationResultRow(
                camera_index=len(rows),
                side=side,
                cam_id=cam_id,
                mean=mean_val,
                avg_contrast=contrast_val,
                bfi=bfi_val,
                bvi=bvi_val,
                mean_test=_threshold_test(mean_val, thresholds.min_mean_per_camera, cam_id),
                contrast_test=_threshold_test(contrast_val, thresholds.min_contrast_per_camera, cam_id),
                bfi_test=_threshold_test(bfi_val, thresholds.min_bfi_per_camera, cam_id),
                bvi_test=_threshold_test(bvi_val, thresholds.min_bvi_per_camera, cam_id),
                security_id=security_id,
                hwid=hwid,
            ))

    return rows


def evaluate_passed(rows: list[CalibrationResultRow]) -> bool:
    if not rows:
        return False
    return all(
        r.mean_test == "PASS"
        and r.contrast_test == "PASS"
        and r.bfi_test == "PASS"
        and r.bvi_test == "PASS"
        for r in rows
    )
