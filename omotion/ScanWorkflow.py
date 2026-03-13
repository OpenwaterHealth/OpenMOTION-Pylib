import datetime
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, TYPE_CHECKING

from omotion import _log_root
from omotion.MotionProcessing import create_realtime_processing_pipeline, stream_queue_to_csv_file

if TYPE_CHECKING:
    from omotion.Interface import MOTIONInterface

logger = logging.getLogger(f"{_log_root}.ScanWorkflow" if _log_root else "ScanWorkflow")


@dataclass
class ScanRequest:
    subject_id: str
    duration_sec: int
    left_camera_mask: int
    right_camera_mask: int
    data_dir: str
    disable_laser: bool
    expected_size: int = 32837


@dataclass
class ScanResult:
    ok: bool
    error: str
    left_path: str
    right_path: str
    canceled: bool
    scan_timestamp: str


class ScanWorkflow:
    def __init__(self, interface: "MOTIONInterface"):
        self._interface = interface
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._running = False
        self._lock = threading.Lock()

        self._bfi_c_min = None
        self._bfi_c_max = None
        self._bfi_i_min = None
        self._bfi_i_max = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def set_realtime_calibration(
        self,
        bfi_c_min,
        bfi_c_max,
        bfi_i_min,
        bfi_i_max,
    ) -> None:
        self._bfi_c_min = bfi_c_min
        self._bfi_c_max = bfi_c_max
        self._bfi_i_min = bfi_i_min
        self._bfi_i_max = bfi_i_max

    def get_single_histogram(
        self,
        side: str,
        camera_id: int,
        test_pattern_id: int = 4,
        auto_upload: bool = True,
    ):
        side_key = (side or "").strip().lower()
        if side_key not in ("left", "right"):
            logger.error("Invalid side for get_single_histogram: %s", side)
            return None
        sensor = self._interface.sensors.get(side_key) if self._interface.sensors else None
        if not sensor or not sensor.is_connected():
            logger.error("%s sensor not connected", side_key.capitalize())
            return None
        return sensor.get_camera_histogram(
            camera_id=int(camera_id),
            test_pattern_id=int(test_pattern_id),
            auto_upload=bool(auto_upload),
        )

    def start_scan(
        self,
        request: ScanRequest,
        *,
        extra_cols_fn: Callable[[], list] | None = None,
        on_log_fn: Callable[[str], None] | None = None,
        on_progress_fn: Callable[[int], None] | None = None,
        on_trigger_state_fn: Callable[[str], None] | None = None,
        on_sample_fn: Callable[[object], None] | None = None,
        on_corrected_fn: Callable[[object], None] | None = None,
        on_error_fn: Callable[[Exception], None] | None = None,
        on_side_stream_fn: Callable[[str, str], None] | None = None,
        on_complete_fn: Callable[[ScanResult], None] | None = None,
    ) -> bool:
        with self._lock:
            if self._running or (self._thread and self._thread.is_alive()):
                return False
            self._running = True

        self._stop_evt = threading.Event()

        def _emit_log(msg: str) -> None:
            logger.info(msg)
            if on_log_fn:
                on_log_fn(msg)

        def _worker():
            ok = False
            err = ""
            left_path = ""
            right_path = ""
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            active_sides = []
            writer_threads: dict[str, threading.Thread] = {}
            writer_stops: dict[str, threading.Event] = {}
            processing_pipelines = {}

            try:
                os.makedirs(request.data_dir, exist_ok=True)
                active_sides = self._resolve_active_sides(
                    request.left_camera_mask, request.right_camera_mask
                )
                if not active_sides:
                    raise RuntimeError(
                        "No active sensors to capture (both masks 0x00 or disconnected)."
                    )

                _emit_log("Preparing capture...")

                if not request.disable_laser:
                    _emit_log("Enabling external frame sync...")
                    for side, _, _ in active_sides:
                        res = self._interface.run_on_sensors(
                            "enable_camera_fsin_ext", target=side
                        )
                        if not self._ok_from_result(res, side):
                            raise RuntimeError(
                                f"Failed to enable external frame sync on {side}."
                            )

                time.sleep(0.1)

                _emit_log("Enabling cameras...")
                for side, mask, _ in active_sides:
                    res = self._interface.run_on_sensors("enable_camera", mask, target=side)
                    if not self._ok_from_result(res, side):
                        raise RuntimeError(
                            f"Failed to enable camera on {side} (mask 0x{mask:02X})."
                        )

                _emit_log("Setting up streaming...")
                for side, mask, sensor in active_sides:
                    q = queue.Queue()
                    stop_evt = threading.Event()
                    sensor.uart.histo.start_streaming(q, expected_size=request.expected_size)

                    filename = f"scan_{request.subject_id}_{ts}_{side}_mask{mask:02X}.csv"
                    filepath = os.path.join(request.data_dir, filename)

                    pipeline = None
                    if (
                        self._bfi_c_min is not None
                        and self._bfi_c_max is not None
                        and self._bfi_i_min is not None
                        and self._bfi_i_max is not None
                    ):
                        pipeline = create_realtime_processing_pipeline(
                            side=side,
                            bfi_c_min=self._bfi_c_min,
                            bfi_c_max=self._bfi_c_max,
                            bfi_i_min=self._bfi_i_min,
                            bfi_i_max=self._bfi_i_max,
                            on_sample_fn=on_sample_fn,
                            on_corrected_fn=on_corrected_fn,
                        )
                        processing_pipelines[side] = pipeline

                    def _make_row_handler(p):
                        def _on_row(cam_id, frame_id, ts_val, hist, row_sum, temp):
                            if p is not None:
                                p.enqueue(cam_id, frame_id, ts_val, hist, row_sum, temp)

                        return _on_row

                    t = threading.Thread(
                        target=stream_queue_to_csv_file,
                        kwargs={
                            "q": q,
                            "stop_evt": stop_evt,
                            "filename": filepath,
                            "extra_headers": ["tcm", "tcl", "pdc"],
                            "extra_cols_fn": extra_cols_fn,
                            "on_row_fn": _make_row_handler(pipeline),
                            "on_error_fn": lambda e, fn=filename: _emit_log(
                                f"Writer error ({fn}): {e}"
                            ),
                        },
                        daemon=True,
                    )
                    t.start()
                    writer_threads[side] = t
                    writer_stops[side] = stop_evt

                    if side == "left":
                        left_path = filepath
                    elif side == "right":
                        right_path = filepath
                    if on_side_stream_fn:
                        on_side_stream_fn(side, filepath)

                _emit_log("Starting trigger...")
                if not self._interface.console_module.start_trigger():
                    raise RuntimeError("Failed to start trigger.")
                if on_trigger_state_fn:
                    on_trigger_state_fn("ON")

                start_t = time.time()
                last_emit = -1
                while not self._stop_evt.is_set():
                    elapsed = time.time() - start_t
                    pct = int(min(100, max(0, (elapsed / max(1, request.duration_sec)) * 100)))
                    if pct != last_emit:
                        if on_progress_fn:
                            on_progress_fn(pct if pct >= 1 else 1)
                        last_emit = pct
                    if elapsed >= request.duration_sec:
                        break
                    time.sleep(0.2)

                ok = not self._stop_evt.is_set()
                if not ok:
                    err = "Capture canceled"
            except Exception as e:
                ok = False
                err = str(e)
                if on_error_fn:
                    on_error_fn(e)
            finally:
                try:
                    self._interface.console_module.stop_trigger()
                except Exception:
                    pass
                if on_trigger_state_fn:
                    on_trigger_state_fn("OFF")

                try:
                    for side, mask, _ in active_sides:
                        try:
                            self._interface.run_on_sensors("disable_camera", mask, target=side)
                        except Exception:
                            pass
                except Exception:
                    pass

                for side, _, sensor in active_sides:
                    try:
                        sensor.uart.histo.stop_streaming()
                    except Exception:
                        pass

                for stop_evt in writer_stops.values():
                    stop_evt.set()
                for t in writer_threads.values():
                    t.join(timeout=5.0)
                for pipeline in processing_pipelines.values():
                    pipeline.stop()

                result = ScanResult(
                    ok=ok,
                    error=err,
                    left_path=left_path,
                    right_path=right_path,
                    canceled=self._stop_evt.is_set(),
                    scan_timestamp=ts,
                )
                if on_complete_fn:
                    on_complete_fn(result)
                with self._lock:
                    self._running = False
                    self._thread = None

        self._thread = threading.Thread(target=_worker, daemon=True)
        self._thread.start()
        return True

    def cancel_scan(self, *, join_timeout: float = 5.0) -> None:
        self._stop_evt.set()
        try:
            if self._interface and self._interface.console_module:
                self._interface.console_module.stop_trigger()
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=join_timeout)

    def _resolve_active_sides(self, left_mask: int, right_mask: int):
        sides_info = [
            ("left", left_mask, self._interface.sensors.get("left")),
            ("right", right_mask, self._interface.sensors.get("right")),
        ]
        active = []
        for side, mask, sensor in sides_info:
            if int(mask) == 0x00:
                continue
            if not (sensor and sensor.is_connected()):
                continue
            active.append((side, int(mask), sensor))
        return active

    @staticmethod
    def _ok_from_result(result, side: str) -> bool:
        if isinstance(result, dict):
            return bool(result.get(side))
        return bool(result)
