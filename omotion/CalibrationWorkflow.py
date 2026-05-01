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
