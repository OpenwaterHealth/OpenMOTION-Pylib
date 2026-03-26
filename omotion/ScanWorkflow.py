import datetime
import csv
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, TYPE_CHECKING

from omotion import _log_root
from omotion.MotionProcessing import (
    CorrectedBatch,
    HISTO_SIZE_WORDS,
    create_science_pipeline,
    parse_stream_to_csv,
    stream_queue_to_csv_file,
)

if TYPE_CHECKING:
    from omotion.Interface import MOTIONInterface

logger = logging.getLogger(f"{_log_root}.ScanWorkflow" if _log_root else "ScanWorkflow")

# ---------------------------------------------------------------------------
# Null CSV writer — used when a raw-stream writer thread must keep running
# (to feed the science pipeline) but file I/O has been disabled.
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# ConsoleTelemetry CSV helpers
# ---------------------------------------------------------------------------

_TELEMETRY_HEADERS: list[str] = [
    "timestamp",
    "tcm", "tcl", "pdc",
    "tec_v_raw", "tec_set_raw", "tec_curr_raw", "tec_volt_raw", "tec_good",
    *[f"pdu_raw_{i}" for i in range(16)],
    *[f"pdu_volt_{i}" for i in range(16)],
    "safety_se", "safety_so", "safety_ok",
    "read_ok", "error",
]


def _snap_to_row(snap) -> list:
    """Convert a ConsoleTelemetry snapshot to a flat CSV row."""
    row: list = [
        snap.timestamp,
        snap.tcm, snap.tcl, snap.pdc,
        snap.tec_v_raw, snap.tec_set_raw, snap.tec_curr_raw, snap.tec_volt_raw,
        int(snap.tec_good),
    ]
    pdu_raws = snap.pdu_raws or []
    pdu_volts = snap.pdu_volts or []
    for i in range(16):
        row.append(pdu_raws[i] if i < len(pdu_raws) else "")
    for i in range(16):
        row.append(pdu_volts[i] if i < len(pdu_volts) else "")
    row.extend([
        snap.safety_se, snap.safety_so, int(snap.safety_ok),
        int(snap.read_ok), snap.error or "",
    ])
    return row


@dataclass
class ScanRequest:
    subject_id: str
    duration_sec: int
    left_camera_mask: int
    right_camera_mask: int
    data_dir: str
    disable_laser: bool
    expected_size: int = 32837
    # CSV output flags — all enabled by default.  Flip to False once the
    # corresponding downstream consumer no longer needs the file, so the
    # pipeline avoids unnecessary disk I/O.
    write_raw_csv: bool = True
    write_corrected_csv: bool = True
    write_telemetry_csv: bool = True
    # Maximum number of seconds for which raw histogram CSVs are written.
    # None (default) means write for the full scan duration.
    # Has no effect when write_raw_csv is False.
    raw_csv_duration_sec: float | None = None


@dataclass
class ScanResult:
    ok: bool
    error: str
    left_path: str
    right_path: str
    canceled: bool
    scan_timestamp: str
    corrected_path: str = ""
    telemetry_path: str = ""


@dataclass
class ConfigureRequest:
    left_camera_mask: int
    right_camera_mask: int
    power_off_unused_cameras: bool = False


@dataclass
class ConfigureResult:
    ok: bool
    error: str


class ScanWorkflow:
    def __init__(self, interface: "MOTIONInterface"):
        self._interface = interface
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._running = False
        self._lock = threading.Lock()
        self._config_thread: threading.Thread | None = None
        self._config_stop_evt = threading.Event()
        self._config_running = False

        self._bfi_c_min = None
        self._bfi_c_max = None
        self._bfi_i_min = None
        self._bfi_i_max = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def config_running(self) -> bool:
        with self._lock:
            return self._config_running

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
        on_uncorrected_fn: Callable[[object], None] | None = None,
        on_corrected_batch_fn: Callable[[object], None] | None = None,
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
            corrected_path = ""
            telemetry_path = ""
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            active_sides = []
            writer_threads: dict[str, threading.Thread] = {}
            writer_stops: dict[str, threading.Event] = {}
            writer_row_counts: dict[str, int] = {}
            writer_queues: dict[str, queue.Queue] = {}
            science_pipeline = None
            corrected_lock = threading.Lock()
            # Keyed by absolute_frame_id (monotonic, rollover-safe) rather than
            # the raw u8 frame_id so that frame 0 on pass 2 does not overwrite
            # frame 0 on pass 1 for scans longer than 256 trigger cycles.
            corrected_by_frame: dict[int, dict] = {}
            corrected_path = os.path.join(
                request.data_dir, f"{ts}_{request.subject_id}_corrected.csv"
            )
            telemetry_path = os.path.join(
                request.data_dir, f"{ts}_{request.subject_id}_telemetry.csv"
            )
            # Telemetry CSV state (populated in try block if console is available)
            _telem_poller = None
            _telem_listener = None
            _telem_fh = None
            _telem_lock = threading.Lock()
            _telem_stop = threading.Event()

            try:
                os.makedirs(request.data_dir, exist_ok=True)

                # Open the telemetry CSV and register a listener on the poller.
                # The guard handles headless configs where there is no console module.
                _telem_poller = getattr(
                    getattr(self._interface, "console_module", None), "telemetry", None
                )
                if _telem_poller is not None and request.write_telemetry_csv:
                    try:
                        _telem_fh = open(  # noqa: WPS515
                            telemetry_path, "w", newline="", encoding="utf-8"
                        )
                        _telem_csv = csv.writer(_telem_fh)
                        _telem_csv.writerow(_TELEMETRY_HEADERS)
                        _telem_fh.flush()

                        def _on_telemetry(snap):
                            if _telem_stop.is_set():
                                return
                            with _telem_lock:
                                if _telem_stop.is_set():
                                    return
                                try:
                                    _telem_csv.writerow(_snap_to_row(snap))
                                    _telem_fh.flush()
                                except Exception as _te:
                                    logger.debug("Telemetry CSV write error: %s", _te)

                        _telem_listener = _on_telemetry
                        _telem_poller.add_listener(_telem_listener)
                    except Exception as _telem_err:
                        _emit_log(f"Failed to open telemetry CSV: {_telem_err}")
                        telemetry_path = ""
                else:
                    telemetry_path = ""

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

                _emit_log("Setting up streaming...")

                # Build one unified SciencePipeline that handles both sides
                # before starting any per-side writer threads.
                if (
                    self._bfi_c_min is not None
                    and self._bfi_c_max is not None
                    and self._bfi_i_min is not None
                    and self._bfi_i_max is not None
                ):
                    left_mask_active = next(
                        (m for s, m, _ in active_sides if s == "left"), 0x00
                    )
                    right_mask_active = next(
                        (m for s, m, _ in active_sides if s == "right"), 0x00
                    )

                    def _on_uncorrected_sample(sample):
                        # Per-sample real-time callback (fires immediately for
                        # GUI with uncorrected BFI/BVI).
                        if on_uncorrected_fn:
                            on_uncorrected_fn(sample)

                    def _on_corrected_batch(batch: CorrectedBatch):
                        # Fires once per dark-frame interval with properly
                        # corrected BFI/BVI for the entire interval.
                        try:
                            with corrected_lock:
                                for sample in batch.samples:
                                    frame_key = int(sample.absolute_frame_id)
                                    col_suffix = f"{sample.side[0]}{int(sample.cam_id) + 1}"
                                    frame_entry = corrected_by_frame.get(frame_key)
                                    if frame_entry is None:
                                        frame_entry = {
                                            "timestamp_s": float(sample.timestamp_s),
                                            "values": {},
                                        }
                                        corrected_by_frame[frame_key] = frame_entry
                                    else:
                                        frame_entry["timestamp_s"] = min(
                                            float(frame_entry["timestamp_s"]),
                                            float(sample.timestamp_s),
                                        )
                                    frame_entry["values"][f"bfi_{col_suffix}"] = float(
                                        sample.bfi_corrected
                                    )
                                    frame_entry["values"][f"bvi_{col_suffix}"] = float(
                                        sample.bvi_corrected
                                    )
                                    frame_entry["values"][f"mean_{col_suffix}"] = float(
                                        sample.mean
                                    )
                                    frame_entry["values"][f"std_{col_suffix}"] = float(
                                        sample.std_dev
                                    )
                                    frame_entry["values"][f"contrast_{col_suffix}"] = float(
                                        sample.contrast
                                    )
                        except Exception as agg_err:
                            _emit_log(f"Corrected batch aggregation error: {agg_err}")
                        if on_corrected_batch_fn:
                            on_corrected_batch_fn(batch)

                    science_pipeline = create_science_pipeline(
                        left_camera_mask=left_mask_active,
                        right_camera_mask=right_mask_active,
                        bfi_c_min=self._bfi_c_min,
                        bfi_c_max=self._bfi_c_max,
                        bfi_i_min=self._bfi_i_min,
                        bfi_i_max=self._bfi_i_max,
                        on_uncorrected_fn=_on_uncorrected_sample,
                        on_corrected_batch_fn=_on_corrected_batch,
                        on_science_frame_fn=on_sample_fn,  # repurposed for aligned-frame callback
                    )

                def _make_row_handler(current_side: str, p):
                    """Close over side so each writer thread feeds the right key."""
                    def _on_row(cam_id, frame_id, ts_val, hist, row_sum, temp):
                        if p is not None:
                            p.enqueue(
                                current_side,
                                cam_id,
                                frame_id,
                                ts_val,
                                hist,
                                row_sum,
                                temp,
                            )
                    return _on_row

                _RAW_CSV_EXTRA_HEADERS = ["tcm", "tcl", "pdc"]

                for side, mask, sensor in active_sides:
                    q = queue.Queue()
                    writer_queues[side] = q
                    stop_evt = threading.Event()

                    # Drain any USB data left over from the previous scan before
                    # arming the new writer thread.  This runs while the MCU
                    # trigger is still off, so only stale prior-scan frames can
                    # be in the endpoint buffer — no real data is discarded.
                    flushed = sensor.uart.histo.flush_stale_data(
                        expected_size=request.expected_size
                    )
                    if flushed:
                        _emit_log(
                            f"Flushed {flushed} stale bytes from {side} USB endpoint "
                            f"({flushed // request.expected_size} frame(s)) before scan start."
                        )

                    sensor.uart.histo.start_streaming(q, expected_size=request.expected_size)

                    _row_handler = _make_row_handler(side, science_pipeline)

                    if request.write_raw_csv and request.raw_csv_duration_sec is None:
                        # Full-duration histogram CSV.
                        filename = f"{ts}_{request.subject_id}_{side}_mask{mask:02X}.csv"
                        filepath = os.path.join(request.data_dir, filename)
                        t = threading.Thread(
                            target=stream_queue_to_csv_file,
                            kwargs={
                                "q": q,
                                "stop_evt": stop_evt,
                                "filename": filepath,
                                "extra_headers": _RAW_CSV_EXTRA_HEADERS,
                                "extra_cols_fn": extra_cols_fn,
                                "on_row_fn": _row_handler,
                                "on_error_fn": lambda e, fn=filename: _emit_log(
                                    f"Writer error ({fn}): {e}"
                                ),
                                "on_complete_fn": lambda n, s=side: writer_row_counts.__setitem__(s, n),
                            },
                            daemon=True,
                        )

                    elif request.write_raw_csv and request.raw_csv_duration_sec is not None:
                        # Time-limited histogram CSV: write for raw_csv_duration_sec
                        # seconds, then close the file and discard further rows while
                        # continuing to feed the science pipeline.
                        filename = f"{ts}_{request.subject_id}_{side}_mask{mask:02X}.csv"
                        filepath = os.path.join(request.data_dir, filename)
                        _dur = float(request.raw_csv_duration_sec)

                        def _timed_write(
                            q=q,
                            stop_evt=stop_evt,
                            on_row_fn=_row_handler,
                            fp=filepath,
                            ecfn=extra_cols_fn,
                            s=side,
                            dur=_dur,
                        ):
                            rows_written = 0
                            fh = None
                            try:
                                fh = open(fp, "w", newline="", encoding="utf-8")  # noqa: WPS515
                                real_writer = csv.writer(fh)
                                real_writer.writerow([
                                    "cam_id", "frame_id", "timestamp_s",
                                    *range(HISTO_SIZE_WORDS),
                                    "temperature", "sum",
                                    *_RAW_CSV_EXTRA_HEADERS,
                                ])
                                deadline = time.time() + dur
                                trunc = _TruncatingCsvWriter(
                                    real_writer, fh, deadline,
                                    on_truncate_fn=lambda: _emit_log(
                                        f"{s.capitalize()} histogram CSV closed after "
                                        f"{dur:.0f}s limit"
                                    ),
                                )
                                rows_written = parse_stream_to_csv(
                                    q=q,
                                    stop_evt=stop_evt,
                                    csv_writer=trunc,
                                    buffer_accumulator=bytearray(),
                                    extra_cols_fn=ecfn,
                                    on_row_fn=on_row_fn,
                                )
                            except Exception as e:
                                _emit_log(f"Writer error ({os.path.basename(fp)}): {e}")
                            finally:
                                if fh is not None:
                                    try:
                                        fh.close()
                                    except Exception:
                                        pass
                                writer_row_counts[s] = rows_written

                        t = threading.Thread(target=_timed_write, daemon=True)

                    else:
                        # No file output — run a bare drain thread so the science
                        # pipeline still receives data via on_row_fn.
                        filepath = ""

                        def _drain(q=q, stop_evt=stop_evt, on_row_fn=_row_handler, s=side):
                            n = parse_stream_to_csv(
                                q=q,
                                stop_evt=stop_evt,
                                csv_writer=_NullCsvWriter(),
                                buffer_accumulator=bytearray(),
                                extra_cols_fn=None,
                                on_row_fn=on_row_fn,
                            )
                            writer_row_counts[s] = n
                        t = threading.Thread(target=_drain, daemon=True)

                    t.start()
                    writer_threads[side] = t
                    writer_stops[side] = stop_evt

                    if side == "left":
                        left_path = filepath
                    elif side == "right":
                        right_path = filepath
                    if filepath:
                        _emit_log(f"{side.capitalize()} raw CSV: {os.path.basename(filepath)}")
                    if on_side_stream_fn:
                        on_side_stream_fn(side, filepath)

                # Arm host-side streaming before enabling cameras so the first
                # frame packet is not missed at scan start.
                _emit_log("Enabling cameras...")
                for side, mask, _ in active_sides:
                    res = self._interface.run_on_sensors("enable_camera", mask, target=side)
                    if not self._ok_from_result(res, side):
                        raise RuntimeError(
                            f"Failed to enable camera on {side} (mask 0x{mask:02X})."
                        )

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

                time.sleep(0.5)

                try:
                    for side, mask, _ in active_sides:
                        try:
                            self._interface.run_on_sensors("disable_camera", mask, target=side)
                        except Exception:
                            pass
                except Exception:
                    pass

                # After disabling cameras the MCU still needs up to ~250 ms to
                # flush its DMA buffer and complete the final USB bulk transfer.
                # Waiting here while _stream_loop is still running ensures that
                # transfer is received and queued BEFORE stop_streaming() signals
                # the loop to exit.
                time.sleep(0.35)

                for side, _, sensor in active_sides:
                    try:
                        sensor.uart.histo.stop_streaming()
                    except Exception:
                        pass
                    # Post-stop drain: _stream_loop exits when it gets a timeout
                    # while stop_event is set.  If the MCU's final USB transfer
                    # arrives after that timeout window (which can happen >350 ms
                    # after trigger-off), the frame lands in the host endpoint
                    # buffer with no reader.  drain_final() recovers it here,
                    # before the writer thread is told to stop.
                    q = writer_queues.get(side)
                    if q is not None:
                        try:
                            final_chunks = sensor.uart.histo.drain_final(
                                expected_size=request.expected_size
                            )
                            for chunk in final_chunks:
                                q.put(chunk)
                            if final_chunks:
                                _emit_log(
                                    f"{side.capitalize()}: post-stop drain recovered "
                                    f"{len(final_chunks)} late USB transfer(s) "
                                    f"({sum(len(c) for c in final_chunks)} bytes)"
                                )
                        except Exception as _drain_err:
                            logger.warning("%s: post-stop drain error: %s", side, _drain_err)

                for stop_evt in writer_stops.values():
                    stop_evt.set()
                for t in writer_threads.values():
                    t.join(timeout=5.0)

                # Per-side summary: USB read chunks received vs rows written to CSV.
                # Compare against the MCU's own frame-sent printout to locate
                # exactly where any frame loss is occurring.
                for side, _, sensor in active_sides:
                    usb_pkts = sensor.uart.histo.packets_received
                    rows = writer_row_counts.get(side, 0)
                    side_path = left_path if side == "left" else right_path
                    _emit_log(
                        f"{side.capitalize()} — USB read chunks received: {usb_pkts} | "
                        f"CSV rows written: {rows}"
                        + (f" | {os.path.basename(side_path)}" if side_path else "")
                    )

                if science_pipeline is not None:
                    science_pipeline.stop()

                # Telemetry CSV teardown — signal the listener to stop writing,
                # wait for any in-flight write to drain, then close the file.
                _telem_stop.set()
                with _telem_lock:
                    pass  # acquire+release: ensures any in-flight write has exited
                if _telem_poller is not None and _telem_listener is not None:
                    try:
                        _telem_poller.remove_listener(_telem_listener)
                    except Exception:
                        pass
                if _telem_fh is not None:
                    try:
                        _telem_fh.close()
                    except Exception:
                        pass
                if telemetry_path:
                    _emit_log(f"Telemetry CSV created: {os.path.basename(telemetry_path)}")

                # Build one merged corrected CSV, aligned by frame_id, with normalized timestamp.
                if not request.write_corrected_csv:
                    corrected_path = ""

                if corrected_path:
                    corrected_columns = (
                        [f"bfi_l{i}" for i in range(1, 9)]
                        + [f"bfi_r{i}" for i in range(1, 9)]
                        + [f"bvi_l{i}" for i in range(1, 9)]
                        + [f"bvi_r{i}" for i in range(1, 9)]
                        + [f"mean_l{i}" for i in range(1, 9)]
                        + [f"mean_r{i}" for i in range(1, 9)]
                        + [f"std_l{i}" for i in range(1, 9)]
                        + [f"std_r{i}" for i in range(1, 9)]
                        + [f"contrast_l{i}" for i in range(1, 9)]
                        + [f"contrast_r{i}" for i in range(1, 9)]
                    )
                    try:
                        with corrected_lock:
                            frame_ids = sorted(corrected_by_frame.keys())
                            if frame_ids:
                                base_ts = min(
                                    float(corrected_by_frame[fid]["timestamp_s"])
                                    for fid in frame_ids
                                )
                            else:
                                base_ts = 0.0

                            with open(
                                corrected_path, "w", newline="", encoding="utf-8"
                            ) as cfh:
                                cw = csv.writer(cfh)
                                cw.writerow(["frame_id", "timestamp_s", *corrected_columns])
                                for fid in frame_ids:
                                    frame_entry = corrected_by_frame[fid]
                                    rel_ts = float(frame_entry["timestamp_s"]) - base_ts
                                    values = frame_entry["values"]
                                    row = [fid, rel_ts]
                                    row.extend(
                                        values.get(col, "") for col in corrected_columns
                                    )
                                    cw.writerow(row)
                        _emit_log(
                            f"Merged corrected CSV created: {os.path.basename(corrected_path)}"
                        )
                    except Exception as corrected_err:
                        _emit_log(f"Failed to create merged corrected CSV: {corrected_err}")
                        corrected_path = ""

                result = ScanResult(
                    ok=ok,
                    error=err,
                    left_path=left_path,
                    right_path=right_path,
                    corrected_path=corrected_path,
                    telemetry_path=telemetry_path,
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

    def start_configure_camera_sensors(
        self,
        request: ConfigureRequest,
        *,
        on_progress_fn: Callable[[int], None] | None = None,
        on_log_fn: Callable[[str], None] | None = None,
        on_complete_fn: Callable[[ConfigureResult], None] | None = None,
    ) -> bool:
        with self._lock:
            if self._config_running or (
                self._config_thread and self._config_thread.is_alive()
            ):
                return False
            self._config_running = True

        self._config_stop_evt = threading.Event()

        def _emit_progress(pct: int) -> None:
            if on_progress_fn:
                on_progress_fn(int(pct))

        def _emit_log(msg: str) -> None:
            logger.info(msg)
            if on_log_fn:
                on_log_fn(msg)

        def _worker():
            ok = False
            err = ""
            try:
                active = self._resolve_active_sides(
                    request.left_camera_mask, request.right_camera_mask
                )
                if not active:
                    raise RuntimeError("No active sensors to configure.")

                if request.power_off_unused_cameras:
                    _emit_log("Powering on cameras before programming FPGAs...")
                    for side, mask, sensor in active:
                        try:
                            power_status = sensor.get_camera_power_status()
                            if not power_status or len(power_status) != 8:
                                _emit_log(f"{side}: could not get camera power status")
                                continue
                            off_mask = sum(
                                1 << i
                                for i in range(8)
                                if power_status[i] and not (mask & (1 << i))
                            )
                            on_mask = mask & 0xFF
                            if off_mask:
                                if sensor.disable_camera_power(off_mask):
                                    _emit_log(
                                        f"{side}: powered off cameras not in mask (0x{off_mask:02X})"
                                    )
                                time.sleep(0.05)
                            if on_mask:
                                if sensor.enable_camera_power(on_mask):
                                    _emit_log(
                                        f"{side}: powered on cameras (mask 0x{on_mask:02X})"
                                    )
                                else:
                                    raise RuntimeError(
                                        f"Failed to power on cameras on {side} (mask 0x{on_mask:02X})."
                                    )
                                time.sleep(0.5)
                        except Exception as e:
                            raise RuntimeError(
                                f"Error setting camera power for {side}: {e}"
                            ) from e

                tasks: list[tuple[str, int]] = []
                for side, mask, _ in active:
                    positions = [i for i in range(8) if (mask & (1 << i))]
                    tasks.extend((side, pos) for pos in positions)

                if not tasks:
                    raise RuntimeError("Empty camera masks (left & right)")

                total = len(tasks) * 2
                done = 0
                _emit_progress(1)

                for side, pos in tasks:
                    if self._config_stop_evt.is_set():
                        raise RuntimeError("Canceled")

                    sensor = self._interface.sensors.get(side)
                    if not sensor or not sensor.is_connected():
                        raise RuntimeError(f"{side} sensor not connected during configure.")

                    cam_mask_single = 1 << pos
                    pos1 = pos + 1

                    status_map = sensor.get_camera_status(cam_mask_single)
                    if not status_map or pos not in status_map:
                        raise RuntimeError(
                            f"Failed to read camera status for {side} camera {pos1}."
                        )
                    status = status_map[pos]
                    if not status & (1 << 0):
                        raise RuntimeError(
                            f"{side} camera {pos1} not READY for FPGA/config."
                        )

                    msg = (
                        f"Programming {side} camera FPGA at position {pos1} "
                        f"(mask 0x{cam_mask_single:02X})..."
                    )
                    _emit_log(msg)
                    results = self._interface.run_on_sensors(
                        "program_fpga",
                        camera_position=cam_mask_single,
                        manual_process=False,
                        target=side,
                    )
                    if not self._ok_from_result(results, side):
                        raise RuntimeError(
                            f"Failed to program FPGA on {side} sensor (pos {pos1})."
                        )
                    done += 1
                    _emit_progress(int((done / total) * 100))

                    if self._config_stop_evt.is_set():
                        raise RuntimeError("Canceled")

                    time.sleep(0.1)
                    msg = (
                        f"Configuring {side} camera sensor registers "
                        f"at position {pos1}..."
                    )
                    _emit_log(msg)
                    cfg_results = self._interface.run_on_sensors(
                        "camera_configure_registers",
                        camera_position=cam_mask_single,
                        target=side,
                    )
                    if not self._ok_from_result(cfg_results, side):
                        raise RuntimeError(
                            f"camera_configure_registers failed on {side} at position {pos1}: {cfg_results!r}"
                        )
                    done += 1
                    _emit_progress(int((done / total) * 100))

                ok = True
                _emit_log("FPGAs programmed & registers configured")
            except Exception as e:
                err = str(e)
                logger.error("Camera configure workflow error: %s", err)
            finally:
                if on_complete_fn:
                    on_complete_fn(ConfigureResult(ok=ok, error=err))
                with self._lock:
                    self._config_running = False
                    self._config_thread = None

        self._config_thread = threading.Thread(target=_worker, daemon=True)
        self._config_thread.start()
        return True

    def cancel_configure_camera_sensors(self, *, join_timeout: float = 5.0) -> None:
        self._config_stop_evt.set()
        if self._config_thread and self._config_thread.is_alive():
            self._config_thread.join(timeout=join_timeout)

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
