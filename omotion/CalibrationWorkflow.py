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
    CorrectedBatch,
    Sample,
    create_science_pipeline,
    feed_pipeline_from_csv,
)


def _collect_samples_from_csvs(
    *,
    left_csv: Optional[str],
    right_csv: Optional[str],
    skip_leading_frames: int,
    frame_window_count: Optional[int] = None,
    left_camera_mask: int = 0xFF,
    right_camera_mask: int = 0xFF,
    calibration: Optional[Calibration] = None,
) -> list[Sample]:
    """Run the science pipeline against raw histogram CSVs and return
    *dark-frame-corrected* Samples whose ``absolute_frame_id`` falls in
    the half-open window
    ``[skip_leading_frames, skip_leading_frames + frame_window_count)``.

    Each returned Sample has:
        - ``mean``     = ``u1 − dark_u1`` (laser-on mean minus the linearly
          interpolated dark baseline, no pedestal artifact)
        - ``std_dev``  = sqrt(corrected variance with shot-noise removed)
        - ``contrast`` = ``std_dev / mean`` (physically bounded speckle
          contrast)
        - ``is_corrected = True``

    The dark baseline at every light frame is interpolated linearly
    between the two bounding dark frames the firmware emits at the
    start and end of every scan: frame 10 (the first scheduled dark)
    and the terminal dark (last frame). For short calibration scans
    this gives a clean, scan-local dark reference without waiting for
    the next 600-frame interval.

    When ``frame_window_count`` is ``None`` the trailing edge is
    unbounded.

    Calibration defaults to ``Calibration.default()``. The BFI/BVI
    fields on the returned Samples reflect that calibration; for the
    validation phase callers pass the freshly-written console
    calibration so the values match what scans will produce in real
    use.
    """
    cal = calibration or Calibration.default()
    samples: list[Sample] = []

    if frame_window_count is None:
        upper_bound = None
    else:
        upper_bound = skip_leading_frames + int(frame_window_count)

    def _on_corrected(batch: CorrectedBatch) -> None:
        for s in batch.samples:
            if s.absolute_frame_id < skip_leading_frames:
                continue
            if upper_bound is not None and s.absolute_frame_id >= upper_bound:
                continue
            samples.append(s)

    pipeline = create_science_pipeline(
        left_camera_mask=left_camera_mask,
        right_camera_mask=right_camera_mask,
        bfi_c_min=cal.c_min,
        bfi_c_max=cal.c_max,
        bfi_i_min=cal.i_min,
        bfi_i_max=cal.i_max,
        on_corrected_batch_fn=_on_corrected,
    )
    try:
        if left_csv:
            feed_pipeline_from_csv(left_csv, "left", pipeline)
        if right_csv:
            feed_pipeline_from_csv(right_csv, "right", pipeline)
    finally:
        # stop() drains the worker queue and triggers _flush_terminal_dark,
        # which promotes the firmware's terminal dark frame to the dark
        # history and emits the corrected batch even for short scans.
        pipeline.stop(timeout=30.0)
    samples.sort(key=lambda s: (s.side, s.cam_id, s.absolute_frame_id))
    return samples


class DegenerateCalibrationError(RuntimeError):
    """Raised when an active camera's calibration scan produces unusable
    data (zero / negative aggregates), making BFI/BVI math impossible."""


def _camera_active(mask: int, cam_id: int) -> bool:
    return bool(mask & (1 << cam_id))


def _compute_calibration_from_samples(
    samples: list[Sample],
    *,
    left_camera_mask: int,
    right_camera_mask: int,
) -> Calibration:
    """Core calibration math: aggregate dark-corrected Samples into a
    ``(MODULES, CAMS_PER_MODULE)`` Calibration.

    Pure function — no I/O. Caller pre-filters ``samples`` to the
    averaging window. Each input Sample should be from the science
    pipeline's corrected stream (``is_corrected=True``): ``mean``
    is dark-baseline-subtracted, ``std_dev`` has shot-noise removed,
    ``contrast = std_dev / mean`` is physical speckle contrast.
    """
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
                    f"active camera ({side}, cam={cam_id + 1}) produced "
                    f"no corrected samples; calibration aborted."
                )
            # Ratio-of-means contrast, not mean-of-ratios.
            # mean(std/mean) is statistically biased upward whenever
            # per-frame mean varies; mean(std)/mean(mean) matches the
            # live UI's "speckle contrast" and stays bounded by 1 for a
            # well-conditioned signal.
            mean_avg = float(np.mean([s.mean for s in cam_samples]))
            std_avg = float(np.mean([s.std_dev for s in cam_samples]))
            new_c_max = (std_avg / mean_avg) if mean_avg > 0.0 else 0.0
            new_i_max = CALIBRATION_I_MAX_MULTIPLIER * mean_avg
            # The biased per-frame average is logged for diagnosis: a
            # large divergence between the two estimators is the smoking
            # gun for a flaky / partially-occluded camera.
            per_frame_contrast_avg = float(
                np.mean([s.contrast for s in cam_samples])
            )
            logger.info(
                "  cam (%s, cam=%d): n=%d  mean=%.2f  std=%.2f  "
                "C_max(ratio-of-means)=%.4f  C_max(mean-of-ratios)=%.4f  "
                "I_max=%.2f",
                side, cam_id + 1, len(cam_samples),
                mean_avg, std_avg, new_c_max, per_frame_contrast_avg,
                new_i_max,
            )
            if new_c_max <= 0.0 or new_i_max <= 0.0:
                raise DegenerateCalibrationError(
                    f"active camera ({side}, cam={cam_id + 1}) produced "
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


def compute_calibration_from_csvs(
    *,
    left_csv: Optional[str],
    right_csv: Optional[str],
    left_camera_mask: int,
    right_camera_mask: int,
    skip_leading_frames: int,
    frame_window_count: Optional[int] = None,
) -> Calibration:
    """CSV-driven entry point — for tests and offline analysis only.

    Production calibration (run from the bloodflow app) goes through
    :class:`CalibrationWorkflow`, which subscribes to the science
    pipeline's corrected stream as the scan runs and never re-parses
    the data from disk.
    """
    logger.info(
        "compute_calibration_from_csvs: left_csv=%s right_csv=%s "
        "masks=(0x%02X, 0x%02X) skip_leading_frames=%d "
        "frame_window_count=%s",
        os.path.basename(left_csv) if left_csv else None,
        os.path.basename(right_csv) if right_csv else None,
        left_camera_mask, right_camera_mask, skip_leading_frames,
        frame_window_count,
    )
    samples = _collect_samples_from_csvs(
        left_csv=left_csv, right_csv=right_csv,
        skip_leading_frames=skip_leading_frames,
        frame_window_count=frame_window_count,
        left_camera_mask=left_camera_mask,
        right_camera_mask=right_camera_mask,
    )
    logger.info(
        "compute_calibration_from_csvs: collected %d dark-corrected samples.",
        len(samples),
    )
    return _compute_calibration_from_samples(
        samples,
        left_camera_mask=left_camera_mask,
        right_camera_mask=right_camera_mask,
    )


def _format_calibration(cal: Calibration) -> str:
    """Return a multi-line human-readable dump of a Calibration's arrays.
    Cameras are labeled 1..8 (not 0..7)."""
    header = "  " + " " * 21 + "  ".join(f"{cam:>8d}" for cam in range(1, CAMS_PER_MODULE + 1))

    def _row(label: str, arr: np.ndarray) -> str:
        rows = []
        for module_idx, side in enumerate(("left ", "right")):
            vals = "  ".join(f"{v:>8.4f}" for v in arr[module_idx])
            rows.append(f"  {label} {side} (m={module_idx}): {vals}")
        return "\n".join(rows)

    return (
        f"Calibration(source={cal.source!r}):\n"
        f"{header}    (cam #)\n"
        f"{_row('C_min', cal.c_min)}\n"
        f"{_row('C_max', cal.c_max)}\n"
        f"{_row('I_min', cal.i_min)}\n"
        f"{_row('I_max', cal.i_max)}"
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
    frame_window_count: Optional[int] = None,
) -> list[CalibrationResultRow]:
    """CSV-driven entry point — for tests and offline analysis only.

    Production validation (run from the bloodflow app) goes through
    :class:`CalibrationWorkflow`, which subscribes to the corrected
    stream as the scan runs.

    See :func:`_collect_samples_from_csvs` for ``frame_window_count``
    semantics.
    """
    samples = _collect_samples_from_csvs(
        left_csv=left_csv, right_csv=right_csv,
        skip_leading_frames=skip_leading_frames,
        frame_window_count=frame_window_count,
        left_camera_mask=left_camera_mask,
        right_camera_mask=right_camera_mask,
        calibration=calibration,
    )
    return _build_result_rows_from_samples(
        samples,
        left_camera_mask=left_camera_mask,
        right_camera_mask=right_camera_mask,
        thresholds=thresholds,
        sensor_left=sensor_left,
        sensor_right=sensor_right,
    )


def _build_result_rows_from_samples(
    samples: list[Sample],
    *,
    left_camera_mask: int,
    right_camera_mask: int,
    thresholds: CalibrationThresholds,
    sensor_left,
    sensor_right,
) -> list[CalibrationResultRow]:
    """Core row aggregation: per-camera mean/contrast/BFI/BVI averages
    and threshold pass/fail. Pure function — caller pre-filters.
    """
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


_CSV_FIELDS = [
    "camera_index", "side", "cam",
    "mean", "avg_contrast", "bfi", "bvi",
    "mean_test", "contrast_test", "bfi_test", "bvi_test",
    "security_id", "hwid",
]


def write_result_csv(path: str, rows: list[CalibrationResultRow]) -> None:
    """Write CalibrationResultRow list to ``path`` in the canonical
    column order. Creates parent directories if needed.

    The ``cam`` column is 1-indexed (1..8), matching how cameras are
    physically labeled. Internally ``CalibrationResultRow.cam_id`` is
    still 0-indexed (so it can be used to lookup into the per-camera
    threshold arrays).
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_CSV_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({
                "camera_index": r.camera_index,
                "side": r.side,
                "cam": r.cam_id + 1,
                "mean": f"{r.mean:.4f}",
                "avg_contrast": f"{r.avg_contrast:.6f}",
                "bfi": f"{r.bfi:.4f}",
                "bvi": f"{r.bvi:.4f}",
                "mean_test": r.mean_test,
                "contrast_test": r.contrast_test,
                "bfi_test": r.bfi_test,
                "bvi_test": r.bvi_test,
                "security_id": r.security_id,
                "hwid": r.hwid,
            })


# ---------------------------------------------------------------------------
# Orchestration class
# ---------------------------------------------------------------------------

from omotion.ScanWorkflow import ScanRequest, ScanResult


def _run_subscan_capture(
    interface,
    request: CalibrationRequest,
    *,
    subject_id: str,
    duration_sec: int,
    skip_leading_frames: int,
    frame_window_count: int,
    stop_evt: threading.Event,
) -> tuple[str, str, list[Sample]]:
    """Submit a ScanRequest and capture corrected samples in-memory as
    the science pipeline emits them.

    The scan still writes its raw histogram CSV to disk (`write_raw_csv=True`)
    so operators retain the artifact for later verification, but we
    don't re-parse it — corrected samples are captured live via
    ``on_corrected_batch_fn``. This avoids running the science pipeline
    twice on the same data.

    Returns ``(left_path, right_path, captured_samples)``. Raises
    ``RuntimeError`` on scan failure. Honors ``stop_evt`` by calling
    ``cancel_scan`` and returning empty paths + empty list.
    """
    scan_req = ScanRequest(
        subject_id=subject_id,
        duration_sec=duration_sec,
        left_camera_mask=request.left_camera_mask,
        right_camera_mask=request.right_camera_mask,
        data_dir=request.output_dir,
        disable_laser=False,
        write_raw_csv=True,         # keep the raw artifact for audit
        write_corrected_csv=False,
        write_telemetry_csv=False,
        reduced_mode=False,
    )

    upper_bound = skip_leading_frames + int(frame_window_count)
    captured: list[Sample] = []

    def _on_corrected_batch(batch: CorrectedBatch) -> None:
        for s in batch.samples:
            if s.absolute_frame_id < skip_leading_frames:
                continue
            if s.absolute_frame_id >= upper_bound:
                continue
            captured.append(s)

    evt = threading.Event()
    holder: dict[str, ScanResult] = {}

    def _on_complete(r: ScanResult) -> None:
        holder["r"] = r
        evt.set()

    started = interface.scan_workflow.start_scan(
        scan_req,
        on_corrected_batch_fn=_on_corrected_batch,
        on_complete_fn=_on_complete,
    )
    if not started:
        raise RuntimeError("ScanWorkflow refused start_scan.")

    while not evt.wait(timeout=0.1):
        if stop_evt.is_set():
            try:
                interface.scan_workflow.cancel_scan()
            except Exception:
                pass
            evt.wait(timeout=5.0)
            return "", "", []
    res = holder.get("r")
    if res is None or not res.ok:
        raise RuntimeError(
            f"sub-scan failed: {(res.error if res else 'no result')}"
        )
    if res.canceled:
        return "", "", []
    captured.sort(key=lambda s: (s.side, s.cam_id, s.absolute_frame_id))
    return res.left_path or "", res.right_path or "", captured


class CalibrationWorkflow:
    def __init__(self, interface: "MotionInterface"):
        self._interface = interface
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()
        self._running = False

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def start_calibration(
        self,
        request: CalibrationRequest,
        *,
        on_log_fn: Optional[Callable[[str], None]] = None,
        on_progress_fn: Optional[Callable[[str], None]] = None,
        on_complete_fn: Optional[Callable[[CalibrationResult], None]] = None,
    ) -> bool:
        with self._lock:
            if self._running:
                logger.warning("start_calibration refused: already running.")
                return False
            self._running = True
        self._stop_evt = threading.Event()

        def _emit_log(msg: str) -> None:
            logger.info(msg)
            if on_log_fn:
                on_log_fn(msg)

        def _emit_progress(stage: str) -> None:
            if on_progress_fn:
                on_progress_fn(stage)

        def _worker() -> None:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            cal_left = cal_right = ""
            val_left = val_right = ""
            cal_obj: Optional[Calibration] = None
            csv_path = ""
            rows: list[CalibrationResultRow] = []
            ok = False
            passed = False
            error = ""
            canceled = False

            logger.info(
                "Calibration: starting procedure (operator=%s, output_dir=%s, "
                "masks=(0x%02X, 0x%02X), duration_sec=%d, scan_delay_sec=%d, "
                "max_duration_sec=%d, ts=%s)",
                request.operator_id, request.output_dir,
                request.left_camera_mask, request.right_camera_mask,
                request.duration_sec, request.scan_delay_sec,
                request.max_duration_sec, ts,
            )

            def _watchdog() -> None:
                self._stop_evt.set()
                logger.warning(
                    "Calibration watchdog fired after %d sec; aborting.",
                    request.max_duration_sec,
                )
                try:
                    self._interface.scan_workflow.cancel_scan()
                except Exception:
                    pass
            wd = threading.Timer(request.max_duration_sec, _watchdog)
            wd.daemon = True
            wd.start()

            skip_frames = int(round(request.scan_delay_sec * CAPTURE_HZ))
            # Bound the trailing edge to keep the firmware's terminal
            # dark frame (and any laser ramp-down) out of the average.
            window_frames = int(round(request.duration_sec * CAPTURE_HZ))
            try:
                _emit_progress("calibration_scan")
                _emit_log("Calibration: starting calibration scan…")
                logger.info(
                    "Calibration phase 1: calibration scan, "
                    "duration=%d sec (= %d duration + %d delay)",
                    request.duration_sec + request.scan_delay_sec,
                    request.duration_sec, request.scan_delay_sec,
                )
                cal_left, cal_right, cal_samples = _run_subscan_capture(
                    self._interface, request,
                    subject_id=f"calib1_{request.operator_id}",
                    duration_sec=request.duration_sec + request.scan_delay_sec,
                    skip_leading_frames=skip_frames,
                    frame_window_count=window_frames,
                    stop_evt=self._stop_evt,
                )
                logger.info(
                    "Calibration phase 1 done: %d corrected samples captured "
                    "live; raw CSVs: left=%s  right=%s",
                    len(cal_samples),
                    cal_left or "(none)", cal_right or "(none)",
                )
                if self._stop_evt.is_set():
                    canceled = True
                    error = "canceled during calibration scan"
                    return

                _emit_progress("compute_calibration")
                _emit_log("Calibration: computing arrays…")
                logger.info("Calibration phase 2: computing (2, 8) arrays.")
                try:
                    cal_obj = _compute_calibration_from_samples(
                        cal_samples,
                        left_camera_mask=request.left_camera_mask,
                        right_camera_mask=request.right_camera_mask,
                    )
                except DegenerateCalibrationError as e:
                    error = str(e)
                    return
                logger.info(
                    "Calibration phase 2 done — proposed calibration:\n%s",
                    _format_calibration(cal_obj),
                )

                _emit_progress("write_calibration")
                _emit_log("Calibration: writing to console…")
                logger.info("Calibration phase 3: writing to console EEPROM.")
                cal_obj = self._interface.write_calibration(
                    cal_obj.c_min, cal_obj.c_max,
                    cal_obj.i_min, cal_obj.i_max,
                )
                logger.info(
                    "Calibration phase 3 done — calibration written and "
                    "cached (source=%s).", cal_obj.source,
                )

                if self._stop_evt.is_set():
                    canceled = True
                    error = "canceled after calibration write"
                    return

                _emit_progress("validation_scan")
                _emit_log("Calibration: starting validation scan…")
                logger.info(
                    "Calibration phase 4: validation scan, "
                    "duration=%d sec (= %d duration + %d delay)",
                    request.duration_sec + request.scan_delay_sec,
                    request.duration_sec, request.scan_delay_sec,
                )
                val_left, val_right, val_samples = _run_subscan_capture(
                    self._interface, request,
                    subject_id=f"calib2_{request.operator_id}",
                    duration_sec=request.duration_sec + request.scan_delay_sec,
                    skip_leading_frames=skip_frames,
                    frame_window_count=window_frames,
                    stop_evt=self._stop_evt,
                )
                logger.info(
                    "Calibration phase 4 done: %d corrected samples captured "
                    "live; raw CSVs: left=%s  right=%s",
                    len(val_samples),
                    val_left or "(none)", val_right or "(none)",
                )
                if self._stop_evt.is_set():
                    canceled = True
                    error = "canceled during validation scan"
                    return

                _emit_progress("evaluate")
                _emit_log("Calibration: evaluating…")
                logger.info("Calibration phase 5: aggregating per-camera rows + thresholds.")
                rows = _build_result_rows_from_samples(
                    val_samples,
                    left_camera_mask=request.left_camera_mask,
                    right_camera_mask=request.right_camera_mask,
                    thresholds=request.thresholds,
                    sensor_left=getattr(self._interface, "left", None),
                    sensor_right=getattr(self._interface, "right", None),
                )
                csv_path = os.path.join(
                    request.output_dir, f"calibration-{ts}.csv"
                )
                write_result_csv(csv_path, rows)
                passed = evaluate_passed(rows)
                pass_count = sum(
                    1 for r in rows
                    if r.mean_test == "PASS" and r.contrast_test == "PASS"
                    and r.bfi_test == "PASS" and r.bvi_test == "PASS"
                )
                logger.info(
                    "Calibration phase 5 done: %d/%d cameras PASS, "
                    "overall=%s. CSV: %s",
                    pass_count, len(rows), "PASS" if passed else "FAIL",
                    csv_path,
                )
                ok = True
            except Exception as e:
                logger.exception("Calibration worker failed.")
                if not error:
                    error = f"{type(e).__name__}: {e}"
            finally:
                wd.cancel()
                if self._stop_evt.is_set() and not canceled:
                    canceled = True
                    if not error:
                        error = (
                            f"calibration exceeded max_duration_sec="
                            f"{request.max_duration_sec}"
                        )

                if cal_obj is not None:
                    logger.info(
                        "Calibration: final calibration on console:\n%s",
                        _format_calibration(cal_obj),
                    )
                logger.info(
                    "Calibration: procedure complete (ok=%s, passed=%s, "
                    "canceled=%s, error=%r)",
                    ok, passed, canceled, error,
                )

                result = CalibrationResult(
                    ok=ok, passed=passed, canceled=canceled, error=error,
                    csv_path=csv_path, calibration=cal_obj, rows=rows,
                    calibration_scan_left_path=cal_left,
                    calibration_scan_right_path=cal_right,
                    validation_scan_left_path=val_left,
                    validation_scan_right_path=val_right,
                    started_timestamp=ts,
                )
                with self._lock:
                    self._running = False
                if on_complete_fn:
                    try:
                        on_complete_fn(result)
                    except Exception:
                        logger.exception("on_complete_fn raised.")

        self._thread = threading.Thread(
            target=_worker, name="CalibrationWorker", daemon=True,
        )
        self._thread.start()
        return True

    def cancel_calibration(self, *, join_timeout: float = 10.0) -> None:
        if not self.running:
            return
        self._stop_evt.set()
        try:
            self._interface.scan_workflow.cancel_scan()
        except Exception:
            logger.warning("cancel_calibration: cancel_scan raised; ignoring.")
        if self._thread is not None:
            self._thread.join(timeout=join_timeout)
