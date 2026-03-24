import csv
import logging
import struct
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import queue
import threading
from omotion import _log_root
from omotion.config import TYPE_HISTO, TYPE_HISTO_CMP, CMP_UNCMP_CRC_SIZE
from omotion.utils import rle_decompress as _rle_decompress

try:
    # Accelerated CRC implementation if available.
    from omotion.utils import util_crc16 as _crc16
except ImportError:
    import binascii

    def _crc16(buf: memoryview) -> int:
        return binascii.crc_hqx(buf, 0xFFFF)


# Histogram payload constants
HISTO_SIZE_WORDS = 1024
HISTOGRAM_BYTES = HISTO_SIZE_WORDS * 4  # 4096
PACKET_HEADER_SIZE = 6
PACKET_FOOTER_SIZE = 3
HISTO_BLOCK_SIZE = 1 + 1 + HISTOGRAM_BYTES + 4 + 1  # SOH + cam + histo + temp + EOH
TIMESTAMP_SIZE = 4
MIN_PACKET_ENVELOPE_SIZE = PACKET_HEADER_SIZE + PACKET_FOOTER_SIZE
MIN_HISTO_PACKET_SIZE = PACKET_HEADER_SIZE + PACKET_FOOTER_SIZE + HISTO_BLOCK_SIZE
# TYPE_HISTO_CMP has: header + compressed_payload(>=1) + uncmp_crc16(2) + footer(3)
MIN_HISTO_CMP_PACKET_SIZE = PACKET_HEADER_SIZE + 1 + CMP_UNCMP_CRC_SIZE + PACKET_FOOTER_SIZE
MAX_PACKET_SIZE = 32837

SOF, SOH, EOH, EOF = 0xAA, 0xFF, 0xEE, 0xDD
HISTO_BINS = np.arange(HISTO_SIZE_WORDS, dtype=np.float64)
HISTO_BINS_SQ = HISTO_BINS * HISTO_BINS

# Frame ID rollover constants. The firmware packs frame_id into the high byte
# of the last histogram word, so it is an 8-bit counter (0–255).  We detect
# a forward wrap whenever the apparent backward delta would exceed this limit.
FRAME_ID_MODULUS = 256
FRAME_ROLLOVER_THRESHOLD = 128

# Expected sum of all histogram bins for a valid frame.
# When this is not None, any parsed histogram whose bin sum differs from this
# value is treated as corrupt and silently dropped from the sample list.
# Set to the integer value confirmed during calibration; leave as None to
# disable the check (e.g. during development before the expected value is
# known).
EXPECTED_HISTOGRAM_SUM: int | None = 2_457_606

logger = logging.getLogger(
    f"{_log_root}.MotionProcessing" if _log_root else "MotionProcessing"
)

# Struct formats
_U16 = struct.Struct("<H")
_U32 = struct.Struct("<I")
_F32 = struct.Struct("<f")
_HDR = struct.Struct("<BBI")
_BLK_HEAD = struct.Struct("<BB")


def _candidate_packet_size_ok(pkt_type_byte: int, candidate_size: int) -> bool:
    if pkt_type_byte == TYPE_HISTO:
        return MIN_HISTO_PACKET_SIZE <= candidate_size <= MAX_PACKET_SIZE
    if pkt_type_byte == TYPE_HISTO_CMP:
        return MIN_HISTO_CMP_PACKET_SIZE <= candidate_size <= MAX_PACKET_SIZE
    return False


# ---------------------------------------------------------------------------
# Frame ID unwrapping
# ---------------------------------------------------------------------------

class FrameIdUnwrapper:
    """
    Converts a raw u8 frame ID (0–255) into a monotonically increasing
    absolute frame number by detecting rollover events.

    The firmware frame counter wraps from 255 back to 0.  We detect the
    crossing by watching whether the new raw ID is numerically smaller than
    the previous one while the unsigned forward delta is still within the
    normal range (≤ FRAME_ROLLOVER_THRESHOLD).  A delta larger than the
    threshold indicates an anomalous backward jump (retransmit / corruption)
    rather than a genuine rollover, so we leave the epoch untouched.

    One unwrapper instance must be kept per (side, cam_id) pair so that
    independent per-camera counters do not interfere with one another.
    """

    def __init__(self) -> None:
        self._last_raw: int | None = None
        self._epoch: int = 0

    def unwrap(self, raw_frame_id: int) -> int:
        if self._last_raw is None:
            self._last_raw = raw_frame_id
            return raw_frame_id

        delta = (raw_frame_id - self._last_raw) & 0xFF

        if delta <= FRAME_ROLLOVER_THRESHOLD and raw_frame_id < self._last_raw:
            # Normal forward progress that crossed the 0/255 boundary.
            self._epoch += 1
        # delta > FRAME_ROLLOVER_THRESHOLD means apparent backward jump —
        # treat as anomaly and leave epoch unchanged.

        self._last_raw = raw_frame_id
        return self._epoch * FRAME_ID_MODULUS + raw_frame_id

    def reset(self) -> None:
        self._last_raw = None
        self._epoch = 0


# ---------------------------------------------------------------------------
# Wire-level data structures
# ---------------------------------------------------------------------------

@dataclass
class HistogramSample:
    cam_id: int
    frame_id: int          # raw u8 from the wire (0–255)
    timestamp_s: float
    histogram: np.ndarray
    temperature_c: float
    row_sum: int

    def to_csv_row(self, extra_cols: list | None = None) -> list:
        return [
            self.cam_id,
            self.frame_id,
            self.timestamp_s,
            *self.histogram.tolist(),
            self.temperature_c,
            self.row_sum,
            *(extra_cols or []),
        ]


@dataclass
class HistogramPacket:
    samples: list[HistogramSample]
    bytes_consumed: int
    timestamp_s: float | None


# ---------------------------------------------------------------------------
# Science-level data structures
# ---------------------------------------------------------------------------

@dataclass
class RealtimeSample:
    side: str
    cam_id: int
    frame_id: int           # raw u8 from the wire
    absolute_frame_id: int  # monotonic counter with rollover handled
    timestamp_s: float
    row_sum: int
    temperature_c: float
    mean: float
    std_dev: float
    contrast: float
    bfi: float
    bvi: float


@dataclass
class CorrectedSample:
    side: str
    cam_id: int
    frame_id: int           # raw u8 from the wire
    absolute_frame_id: int  # monotonic counter with rollover handled
    timestamp_s: float
    mean: float
    std_dev: float
    contrast: float
    bfi_corrected: float
    bvi_corrected: float


@dataclass
class ScienceFrame:
    """
    All science-computed data for a single aligned trigger frame across
    both sensor sides.

    ``absolute_frame`` is the monotonic frame index produced by
    FrameIdUnwrapper and is safe to use as a database primary key or plot
    x-axis without worrying about u8 rollover collisions.
    ``frame_id`` is the raw 0–255 value as it appears in the binary stream
    and on disk in CSV files.
    ``samples`` maps (side, cam_id) → CorrectedSample for every camera that
    reported during this trigger cycle.
    """

    absolute_frame: int
    frame_id: int
    timestamp_s: float
    samples: dict[tuple[str, int], CorrectedSample]


# ---------------------------------------------------------------------------
# Binary packet parsing
# ---------------------------------------------------------------------------

def bytes_to_integers(byte_array: bytes | bytearray) -> tuple[list[int], list[int]]:
    """
    Convert 4096 histogram bytes into packed integer bins and hidden figures.

    Input is expected as 1024 chunks of 4 bytes each:
    - first 3 bytes: little-endian 24-bit histogram bin value
    - last byte: hidden figure metadata (e.g. frame-id carrier)
    """
    if len(byte_array) != HISTOGRAM_BYTES:
        raise ValueError("Input byte array must be exactly 4096 bytes.")

    integers: list[int] = []
    hidden_figures: list[int] = []
    for i in range(0, len(byte_array), 4):
        chunk = byte_array[i : i + 4]
        hidden_figures.append(chunk[3])
        integers.append(int.from_bytes(chunk[0:3], byteorder="little"))
    return integers, hidden_figures


def parse_histogram_packet(
    pkt: memoryview,
) -> Tuple[Dict[int, np.ndarray], Dict[int, int], Dict[int, float], Optional[float], int]:
    """
    Backward-compatible packet parser returning legacy tuple maps.
    """
    packet = parse_histogram_packet_structured(pkt)
    hists: Dict[int, np.ndarray] = {}
    ids: Dict[int, int] = {}
    temps: Dict[int, float] = {}
    for sample in packet.samples:
        hists[sample.cam_id] = sample.histogram
        ids[sample.cam_id] = sample.frame_id
        temps[sample.cam_id] = sample.temperature_c
    return hists, ids, temps, packet.timestamp_s, packet.bytes_consumed


def parse_histogram_packet_structured(
    pkt: memoryview,
    expected_row_sum: int | None = None,
) -> HistogramPacket:
    """
    Parse a binary histogram packet into normalized packet/sample dataclasses.

    Parameters
    ----------
    pkt
        Raw bytes of a single histogram packet.
    expected_row_sum
        When not None, each parsed sample's bin sum is compared against this
        value.  Samples whose sum does not match are logged as warnings and
        excluded from the returned ``HistogramPacket.samples`` list — they are
        treated as if the frame never arrived (will not be written to CSV and
        will not be fed into the science pipeline).  Pass ``None`` (default) to
        disable the check.  The module-level ``EXPECTED_HISTOGRAM_SUM``
        constant is a convenient global override point.

    Returns
    -------
    HistogramPacket
        Packet with parsed samples and metadata.
    """
    if len(pkt) < MIN_PACKET_ENVELOPE_SIZE:
        raise ValueError("Packet too small")

    sof, pkt_type, pkt_len = _HDR.unpack_from(pkt, 0)
    if sof != SOF or pkt_type not in (TYPE_HISTO, TYPE_HISTO_CMP):
        raise ValueError("Bad header")

    if pkt_len > len(pkt):
        raise ValueError("Truncated packet")

    # If compressed, verify both CRCs, decompress, and rebuild as a standard packet.
    # Packet layout: [Header 6B][Compressed N B][UNCMP_CRC16 2B][PKT_CRC16 2B][EOF 1B]
    if pkt_type == TYPE_HISTO_CMP:
        if pkt_len < MIN_HISTO_CMP_PACKET_SIZE:
            raise ValueError("TYPE_HISTO_CMP packet too small")
        footer_off = pkt_len - PACKET_FOOTER_SIZE            # offset of PKT_CRC16
        uncmp_crc_off = footer_off - CMP_UNCMP_CRC_SIZE      # offset of UNCMP_CRC16

        # 1. Verify transport CRC
        pkt_crc_expected = struct.unpack_from("<H", pkt, footer_off)[0]
        pkt_crc_actual = _crc16(memoryview(pkt[: footer_off - 1]))
        if pkt_crc_actual != pkt_crc_expected:
            raise ValueError(
                f"TYPE_HISTO_CMP transport CRC mismatch "
                f"(got 0x{pkt_crc_actual:04X}, expected 0x{pkt_crc_expected:04X})"
            )

        # 2. Decompress
        uncmp_crc_expected = struct.unpack_from("<H", pkt, uncmp_crc_off)[0]
        compressed_payload = bytes(pkt[PACKET_HEADER_SIZE : uncmp_crc_off])
        decompressed = _rle_decompress(compressed_payload)

        # 3. Verify decompressed payload CRC
        uncmp_crc_actual = _crc16(memoryview(decompressed[:-1]))
        if uncmp_crc_actual != uncmp_crc_expected:
            raise ValueError(
                f"TYPE_HISTO_CMP decompressed CRC mismatch "
                f"(got 0x{uncmp_crc_actual:04X}, expected 0x{uncmp_crc_expected:04X}) "
                f"— decompressor produced wrong output"
            )

        # 4. Rebuild as a TYPE_HISTO packet and recurse
        new_total = PACKET_HEADER_SIZE + len(decompressed) + PACKET_FOOTER_SIZE
        new_header = struct.pack("<BBI", SOF, TYPE_HISTO, new_total)
        crc_data = new_header + decompressed
        crc = _crc16(memoryview(crc_data[: len(crc_data) - 1]))
        new_footer = struct.pack("<HB", crc, EOF)
        rebuilt = new_header + decompressed + new_footer
        packet = parse_histogram_packet_structured(
            memoryview(rebuilt), expected_row_sum=expected_row_sum
        )
        # Preserve original bytes_consumed for offset tracking
        return HistogramPacket(
            samples=packet.samples,
            timestamp_s=packet.timestamp_s,
            bytes_consumed=pkt_len,
        )

    payload_len = pkt_len - PACKET_HEADER_SIZE - PACKET_FOOTER_SIZE
    if payload_len < HISTO_BLOCK_SIZE:
        raise ValueError("Packet payload too small")

    has_timestamp = (payload_len % HISTO_BLOCK_SIZE) == TIMESTAMP_SIZE
    if not has_timestamp and (payload_len % HISTO_BLOCK_SIZE) != 0:
        raise ValueError("Packet length mismatch")

    payload_end = pkt_len - PACKET_FOOTER_SIZE
    off = PACKET_HEADER_SIZE

    samples: list[HistogramSample] = []
    timestamp_sec: Optional[float] = None

    if has_timestamp:
        timestamp_ms = _U32.unpack_from(pkt, off)[0]
        timestamp_sec = timestamp_ms / 1000.0
        off += TIMESTAMP_SIZE

    while off < payload_end:
        soh, cam_id = _BLK_HEAD.unpack_from(pkt, off)
        if soh != SOH:
            raise ValueError("Missing SOH")
        off += _BLK_HEAD.size

        hist = np.frombuffer(pkt, dtype=np.uint32, count=HISTO_SIZE_WORDS, offset=off)
        off += HISTOGRAM_BYTES

        temp = _F32.unpack_from(pkt, off)[0]
        off += 4

        if pkt[off] != EOH:
            raise ValueError("Missing EOH")
        off += 1

        # Strip frame-id from high byte of last word.
        last_word = hist[-1]
        frame_id = (last_word >> 24) & 0xFF
        hist = hist.copy()
        hist[-1] = last_word & 0x00_FF_FF_FF

        ts_val = timestamp_sec if timestamp_sec is not None else 0.0
        row_sum = int(hist.sum(dtype=np.uint64))

        # Sum validation — drop corrupt/doubled frames before they reach the
        # pipeline.  The expected value is the invariant photon-count total
        # that every valid frame must satisfy.
        _expected = expected_row_sum if expected_row_sum is not None else EXPECTED_HISTOGRAM_SUM
        if _expected is not None and row_sum != _expected:
            logger.warning(
                "Histogram sum mismatch for cam %d frame %d: "
                "got %d, expected %d — dropping sample",
                int(cam_id), int(frame_id), row_sum, _expected,
            )
            continue

        samples.append(
            HistogramSample(
                cam_id=int(cam_id),
                frame_id=int(frame_id),
                timestamp_s=float(ts_val),
                histogram=hist,
                temperature_c=float(temp),
                row_sum=row_sum,
            )
        )

    crc_expected = _U16.unpack_from(pkt, off)[0]
    off += 2
    if pkt[off] != EOF:
        raise ValueError("Missing EOF")

    if _crc16(pkt[: off - 3]) != crc_expected:
        raise ValueError("CRC mismatch")

    return HistogramPacket(
        samples=samples,
        bytes_consumed=pkt_len,
        timestamp_s=timestamp_sec,
    )


# ---------------------------------------------------------------------------
# File-oriented helpers (CSV)
# ---------------------------------------------------------------------------

def process_bin_file(
    src_bin: str, dst_csv: str, start_offset: int = 0, batch_rows: int = 4096
) -> None:
    """
    Convert raw histogram binary stream to CSV rows.
    """
    with open(src_bin, "rb") as f:
        data = memoryview(f.read())

    off = start_offset
    packet_ok = packet_fail = crc_failure = other_fail = bad_header_fail = 0
    out_buf: List[List] = []

    with open(dst_csv, "w", newline="") as fcsv:
        wr = csv.writer(fcsv)
        wr.writerow(
            [
                "cam_id",
                "frame_id",
                "timestamp_s",
                *range(HISTO_SIZE_WORDS),
                "temperature",
                "sum",
            ]
        )

        while off + MIN_PACKET_ENVELOPE_SIZE <= len(data):
            try:
                packet = parse_histogram_packet_structured(data[off:])
                off += packet.bytes_consumed
                packet_ok += 1

                for sample in packet.samples:
                    out_buf.append(sample.to_csv_row())

                if len(out_buf) >= batch_rows:
                    wr.writerows(out_buf)
                    out_buf.clear()
            except Exception as exc:
                if exc.args and exc.args[0] == "CRC mismatch":
                    crc_failure += 1
                elif exc.args and exc.args[0] == "Missing SOH":
                    packet_fail += 1
                elif exc.args and exc.args[0] == "Bad header":
                    bad_header_fail += 1
                else:
                    other_fail += 1

                # Resync: search for next valid packet header (SOF byte)
                old_off = off
                search_from = off + 1
                found_sync = False
                while search_from + PACKET_HEADER_SIZE <= len(data):
                    nxt = data.obj.find(b"\xAA", search_from)
                    if nxt == -1 or nxt + PACKET_HEADER_SIZE > len(data):
                        break
                    # Verify type byte is a known histogram type
                    pkt_type_byte = data[nxt + 1]
                    if pkt_type_byte not in (TYPE_HISTO, TYPE_HISTO_CMP):
                        search_from = nxt + 1
                        continue
                    candidate_size = _U32.unpack_from(data, nxt + 2)[0]
                    if _candidate_packet_size_ok(int(pkt_type_byte), int(candidate_size)):
                        off = nxt
                        found_sync = True
                        break
                    search_from = nxt + 1
                if found_sync:
                    continue
                break

        if out_buf:
            wr.writerows(out_buf)

    total_packets = packet_ok + packet_fail + crc_failure + other_fail + bad_header_fail
    logger.info("Parsed %d packets, %d OK", total_packets, packet_ok)


def parse_stream_to_csv(
    q: queue.Queue,
    stop_evt: threading.Event,
    csv_writer,
    buffer_accumulator: bytearray,
    extra_cols_fn: Callable[[], list] | None = None,
    on_row_fn: Callable[[int, int, float, np.ndarray, int, float], None] | None = None,
    expected_row_sum: int | None = None,
) -> int:
    """
    Parse streaming histogram binary data and write CSV rows.

    Parameters
    ----------
    expected_row_sum
        Forwarded to ``parse_histogram_packet_structured``.  When not None,
        samples whose histogram bin sum does not match are silently dropped
        from both CSV output and the ``on_row_fn`` callback.

    Returns
    -------
    int
        Number of rows written to ``csv_writer``.
    """
    rows_written = 0

    while not stop_evt.is_set() or not q.empty():
        try:
            data = q.get(timeout=0.300)
            if data:
                buffer_accumulator.extend(data)
            q.task_done()
        except queue.Empty:
            continue

        offset = 0
        while offset + MIN_PACKET_ENVELOPE_SIZE <= len(buffer_accumulator):
            try:
                pkt_view = memoryview(buffer_accumulator[offset:])
                packet = parse_histogram_packet_structured(
                    pkt_view, expected_row_sum=expected_row_sum
                )
                offset += packet.bytes_consumed

                for sample in packet.samples:
                    extra_cols = extra_cols_fn() if extra_cols_fn else []
                    row = sample.to_csv_row(extra_cols=extra_cols)
                    csv_writer.writerow(row)
                    rows_written += 1
                    if on_row_fn:
                        on_row_fn(
                            sample.cam_id,
                            sample.frame_id,
                            sample.timestamp_s,
                            sample.histogram,
                            sample.row_sum,
                            sample.temperature_c,
                        )

            except ValueError as e:
                old_off = offset
                search_from = offset + 1
                found_sync = False
                while search_from + PACKET_HEADER_SIZE <= len(buffer_accumulator):
                    nxt = buffer_accumulator.find(b"\xaa", search_from)
                    if nxt == -1 or nxt + PACKET_HEADER_SIZE > len(buffer_accumulator):
                        break
                    # Verify type byte is a known histogram type
                    pkt_type_byte = buffer_accumulator[nxt + 1]
                    if pkt_type_byte not in (TYPE_HISTO, TYPE_HISTO_CMP):
                        search_from = nxt + 1
                        continue
                    candidate_size = struct.unpack_from(
                        "<I", buffer_accumulator, nxt + 2
                    )[0]
                    if _candidate_packet_size_ok(int(pkt_type_byte), int(candidate_size)):
                        offset = nxt
                        found_sync = True
                        logger.warning(
                            "Parser error at offset %d, resynced to %d "
                            "(skipped %d bytes): %s",
                            old_off, nxt, nxt - old_off, e,
                        )
                        break
                    search_from = nxt + 1
                if found_sync:
                    continue
                break

        if offset > 0:
            del buffer_accumulator[:offset]

    # --- Final accumulator flush ------------------------------------------------
    # The main loop exits as soon as stop_evt is set and the queue is empty, but
    # bytes may still be sitting in buffer_accumulator from the last dequeue —
    # in particular the final frame, which the USB layer can deliver up to 250 ms
    # after the trigger stops.  Attempt one more full parse pass and log anything
    # that was recovered (or couldn't be parsed) so frame loss is visible in logs.
    if buffer_accumulator:
        logger.warning(
            "parse_stream_to_csv: %d bytes remain in accumulator after "
            "stream end — attempting final parse pass",
            len(buffer_accumulator),
        )
        rows_before_final_flush = rows_written
        offset = 0
        while offset + MIN_PACKET_ENVELOPE_SIZE <= len(buffer_accumulator):
            try:
                pkt_view = memoryview(buffer_accumulator[offset:])
                packet = parse_histogram_packet_structured(
                    pkt_view, expected_row_sum=expected_row_sum
                )
                offset += packet.bytes_consumed
                for sample in packet.samples:
                    extra_cols = extra_cols_fn() if extra_cols_fn else []
                    row = sample.to_csv_row(extra_cols=extra_cols)
                    csv_writer.writerow(row)
                    rows_written += 1
                    if on_row_fn:
                        on_row_fn(
                            sample.cam_id,
                            sample.frame_id,
                            sample.timestamp_s,
                            sample.histogram,
                            sample.row_sum,
                            sample.temperature_c,
                        )
            except ValueError as e:
                old_off = offset
                search_from = offset + 1
                found_sync = False
                while search_from + PACKET_HEADER_SIZE <= len(buffer_accumulator):
                    nxt = buffer_accumulator.find(b"\xaa", search_from)
                    if nxt == -1 or nxt + PACKET_HEADER_SIZE > len(buffer_accumulator):
                        break
                    pkt_type_byte = buffer_accumulator[nxt + 1]
                    if pkt_type_byte not in (TYPE_HISTO, TYPE_HISTO_CMP):
                        search_from = nxt + 1
                        continue
                    candidate_size = struct.unpack_from(
                        "<I", buffer_accumulator, nxt + 2
                    )[0]
                    if _candidate_packet_size_ok(int(pkt_type_byte), int(candidate_size)):
                        offset = nxt
                        found_sync = True
                        logger.warning(
                            "parse_stream_to_csv: final flush parser error at "
                            "offset %d, resynced to %d (skipped %d bytes): %s",
                            old_off, nxt, nxt - old_off, e,
                        )
                        break
                    search_from = nxt + 1
                if found_sync:
                    continue
                break
        if offset > 0:
            logger.info(
                "parse_stream_to_csv: final flush recovered %d additional row(s)",
                rows_written - rows_before_final_flush,
            )
            del buffer_accumulator[:offset]
        if buffer_accumulator:
            logger.warning(
                "parse_stream_to_csv: %d bytes could not be parsed and were "
                "discarded — likely an incomplete final packet",
                len(buffer_accumulator),
            )

    return rows_written


def stream_queue_to_csv_file(
    q: queue.Queue,
    stop_evt: threading.Event,
    filename: str,
    *,
    extra_headers: list[str] | None = None,
    extra_cols_fn: Callable[[], list] | None = None,
    on_row_fn: Callable[[int, int, float, np.ndarray, int, float], None] | None = None,
    on_complete_fn: Callable[[int], None] | None = None,
    on_error_fn: Callable[[Exception], None] | None = None,
    expected_row_sum: int | None = None,
) -> int:
    """
    High-level helper: parse stream queue data and write a CSV file end-to-end.

    This owns file open/header/write/close so applications only pass:
    - destination filename
    - queue + stop event
    - optional callbacks for extra columns and row handling.

    Parameters
    ----------
    expected_row_sum
        Forwarded to ``parse_stream_to_csv`` / ``parse_histogram_packet_structured``.
        Samples whose histogram bin sum does not match are dropped from both
        the CSV and the ``on_row_fn`` callback before being written.
    """
    rows_written = 0
    extra_headers = extra_headers or []

    try:
        with open(filename, "w", newline="", encoding="utf-8") as f:
            csv_writer = csv.writer(f)
            csv_writer.writerow(
                [
                    "cam_id",
                    "frame_id",
                    "timestamp_s",
                    *range(HISTO_SIZE_WORDS),
                    "temperature",
                    "sum",
                    *extra_headers,
                ]
            )

            buffer_accumulator = bytearray()
            rows_written = parse_stream_to_csv(
                q=q,
                stop_evt=stop_evt,
                csv_writer=csv_writer,
                buffer_accumulator=buffer_accumulator,
                extra_cols_fn=extra_cols_fn,
                on_row_fn=on_row_fn,
                expected_row_sum=expected_row_sum,
            )
    except Exception as e:
        if on_error_fn:
            on_error_fn(e)
        logger.error("Writer error (%s): %s", filename, e, exc_info=True)
        return rows_written

    if on_complete_fn:
        on_complete_fn(rows_written)
    return rows_written


# ---------------------------------------------------------------------------
# Pure science computation functions
# ---------------------------------------------------------------------------

def compute_realtime_metrics(
    *,
    side: str,
    cam_id: int,
    frame_id: int,
    absolute_frame_id: int,
    timestamp_s: float,
    hist: np.ndarray,
    row_sum: int,
    temperature_c: float,
    bfi_c_min,
    bfi_c_max,
    bfi_i_min,
    bfi_i_max,
) -> RealtimeSample:
    """
    Pure metric computation for one histogram row.
    """
    if row_sum > 0:
        mean_val = float(np.dot(hist, HISTO_BINS) / row_sum)
    else:
        mean_val = 0.0

    if row_sum > 0 and mean_val > 0:
        mean2 = float(np.dot(hist, HISTO_BINS_SQ) / row_sum)
        var = max(0.0, mean2 - (mean_val * mean_val))
        std = np.sqrt(var)
        contrast = float(std / mean_val) if mean_val > 0 else 0.0
    else:
        std = 0.0
        contrast = 0.0

    module_idx = 0 if side == "left" else 1
    cam_pos = int(cam_id) % 8

    if module_idx >= bfi_c_min.shape[0] or cam_pos >= bfi_c_min.shape[1]:
        bfi_val = contrast * 10.0
    else:
        cmin = float(bfi_c_min[module_idx, cam_pos])
        cmax = float(bfi_c_max[module_idx, cam_pos])
        cden = (cmax - cmin) or 1.0
        bfi_val = (1.0 - ((contrast - cmin) / cden)) * 10.0

    if module_idx >= bfi_i_min.shape[0] or cam_pos >= bfi_i_min.shape[1]:
        bvi_val = mean_val * 10.0
    else:
        imin = float(bfi_i_min[module_idx, cam_pos])
        imax = float(bfi_i_max[module_idx, cam_pos])
        iden = (imax - imin) or 1.0
        bvi_val = (1.0 - ((mean_val - imin) / iden)) * 10.0

    timestamp = float(timestamp_s) if timestamp_s else time.time()
    return RealtimeSample(
        side=side,
        cam_id=int(cam_id),
        frame_id=int(frame_id),
        absolute_frame_id=int(absolute_frame_id),
        timestamp_s=timestamp,
        row_sum=int(row_sum),
        temperature_c=float(temperature_c),
        mean=float(mean_val),
        std_dev=float(std),
        contrast=float(contrast),
        bfi=float(bfi_val),
        bvi=float(bvi_val),
    )


def compute_corrected_values(
    # TODO this is just a placeholder for the future corrected algorithm
    # TODO note that this function will need to operate on large numbers of histograms and will only
    # be able to happen once a dark frame has been captured, which may be every 15 seconds
    *,
    mean_val: float,
    bfi_val: float,
    bvi_val: float,
    last_bfi: float | None,
    last_bvi: float | None,
    mean_threshold: float,
) -> tuple[float, float]:
    """
    Pure correction computation from current values and prior state.
    """
    if mean_val < mean_threshold and last_bfi is not None:
        bfi_corr = float(last_bfi)
    else:
        bfi_corr = float(bfi_val)

    if mean_val < mean_threshold and last_bvi is not None:
        bvi_corr = float(last_bvi)
    else:
        bvi_corr = float(bvi_val)

    return bfi_corr, bvi_corr


# ---------------------------------------------------------------------------
# Unified science pipeline
# ---------------------------------------------------------------------------

@dataclass
class _FrameBuffer:
    """Internal accumulator for one trigger-cycle's worth of samples."""
    arrived_at: float          # monotonic wall time when the first sample arrived
    min_timestamp_s: float     # lowest sensor timestamp seen so far in this frame
    samples: dict[tuple[str, int], CorrectedSample] = field(default_factory=dict)


class SciencePipeline:
    """
    Unified single-threaded science computation pipeline for both sensor sides.

    All histogram samples — from left and right sensors alike — are fed in
    through a single ingress queue.  A single worker thread:

      1. Unwraps the raw u8 frame ID for each (side, cam_id) pair into a
         monotonically increasing ``absolute_frame_id`` so that frame-boundary
         crossing (255 → 0) never causes key collisions downstream.
      2. Computes BFI/BVI metrics via ``compute_realtime_metrics``.
      3. Applies dark-frame correction per (side, cam_id).
      4. Fires ``on_corrected_fn`` with the CorrectedSample immediately, so
         the GUI can update in real time without waiting for frame alignment.
      5. Groups all corrected samples by ``absolute_frame_id`` into a
         ``_FrameBuffer``.  Once all expected (side, cam_id) pairs have
         contributed a sample for a given absolute frame, a ``ScienceFrame``
         is assembled and ``on_science_frame_fn`` is called.  Partial frames
         older than ``frame_timeout_s`` are flushed automatically so that a
         single dropped packet on one side does not stall the pipeline.

    Running everything in one thread ensures that all derived quantities for
    a frame are available simultaneously and can be written to SQLite as a
    single atomic transaction.

    Parameters
    ----------
    left_camera_mask, right_camera_mask
        Bitmask of active cameras (bit N set → cam_id N is expected).
        Pass 0x00 for a side that is not connected.
    bfi_c_min/max, bfi_i_min/max
        Calibration arrays, shape (2, 8) — module index × camera position.
    on_corrected_fn
        Called immediately after correction is applied for each sample.
        Receives a ``CorrectedSample``.
    on_science_frame_fn
        Called once per complete aligned frame.  Receives a ``ScienceFrame``
        whose ``samples`` dict contains every expected (side, cam_id) key
        (or as many as arrived before the timeout).
    frame_timeout_s
        Seconds after the first sample in a frame arrives before the
        incomplete frame is flushed anyway.  Keeps the pipeline moving when
        a sensor drops a packet.
    correction_warmup_count
        Number of frames per camera to observe before applying correction.
    correction_mean_threshold
        Mean intensity below which a frame is treated as a dark frame and
        the previous corrected values are held.
    """

    def __init__(
        self,
        *,
        left_camera_mask: int = 0xFF,
        right_camera_mask: int = 0xFF,
        bfi_c_min,
        bfi_c_max,
        bfi_i_min,
        bfi_i_max,
        on_corrected_fn: Callable[[CorrectedSample], None] | None = None,
        on_science_frame_fn: Callable[[ScienceFrame], None] | None = None,
        frame_timeout_s: float = 0.5,
        correction_warmup_count: int = 10,
        correction_mean_threshold: float = 66.0,
        expected_row_sum: int | None = None,
    ):
        self._bfi_c_min = bfi_c_min
        self._bfi_c_max = bfi_c_max
        self._bfi_i_min = bfi_i_min
        self._bfi_i_max = bfi_i_max
        self._on_corrected_fn = on_corrected_fn
        self._on_science_frame_fn = on_science_frame_fn
        self._frame_timeout_s = frame_timeout_s
        self._correction_warmup_count = correction_warmup_count
        self._correction_mean_threshold = correction_mean_threshold
        self._expected_row_sum = expected_row_sum

        # Derive the set of (side, cam_id) keys that must be present for a
        # frame to be considered complete.
        self._expected_keys: frozenset[tuple[str, int]] = frozenset(
            (side, cam_id)
            for side, mask in (("left", left_camera_mask), ("right", right_camera_mask))
            for cam_id in range(8)
            if mask & (1 << cam_id)
        )

        # One FrameIdUnwrapper per (side, cam_id) — created lazily on first use.
        self._unwrappers: dict[tuple[str, int], FrameIdUnwrapper] = {}

        # Dark-frame correction state per (side, cam_id).
        self._correction_state: dict[tuple[str, int], dict] = {}

        # Pending frames waiting to collect all expected samples.
        self._frame_buffers: dict[int, _FrameBuffer] = {}

        # Tracks which (side, cam_id) pairs have received their first frame.
        # Used to detect stale frames from a previous scan (expected frame_id == 1
        # for the very first frame of a new scan).
        self._first_frame_seen: set[tuple[str, int]] = set()

        # Counts frame-ID desynchronization events between cameras in a frame.
        self._sync_error_count: int = 0

        self._ingress_queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._science_thread = threading.Thread(
            target=self._science_worker, daemon=True, name="SciencePipeline"
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._science_thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        self._science_thread.join(timeout=timeout)

    def enqueue(
        self,
        side: str,
        cam_id: int,
        frame_id: int,
        timestamp_s: float,
        hist: np.ndarray,
        row_sum: int,
        temperature_c: float,
    ) -> None:
        """Feed one histogram sample from the named side into the pipeline."""
        self._ingress_queue.put(
            (side, cam_id, frame_id, timestamp_s, hist, row_sum, temperature_c)
        )

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    def _science_worker(self) -> None:
        while not self._stop_event.is_set() or not self._ingress_queue.empty():
            try:
                item = self._ingress_queue.get(timeout=0.050)
            except queue.Empty:
                self._flush_stale_frames()
                continue

            side, cam_id, raw_frame_id, ts, hist, row_sum, temp = item
            key = (side, cam_id)

            # --- 0a. Sum validation (defense-in-depth) -------------------------
            # parse_histogram_packet_structured already filters these out when
            # expected_row_sum is set, but samples can also be enqueued directly
            # (e.g. in tests).  Re-check here so the pipeline is always clean.
            _expected_sum = (
                self._expected_row_sum
                if self._expected_row_sum is not None
                else EXPECTED_HISTOGRAM_SUM
            )
            if _expected_sum is not None and row_sum != _expected_sum:
                logger.warning(
                    "SciencePipeline: histogram sum mismatch for %s cam %d "
                    "frame %d: got %d, expected %d — dropping sample",
                    side, cam_id, raw_frame_id, row_sum, _expected_sum,
                )
                continue

            # --- 0b. First-frame staleness check --------------------------------
            # The very first histogram received for each (side, cam_id) after
            # pipeline start should have frame_id == 1.  Any other value means
            # we are receiving a leftover frame from the previous scan.
            if key not in self._first_frame_seen:
                self._first_frame_seen.add(key)
                if raw_frame_id != 1:
                    logger.warning(
                        "SciencePipeline: first frame for %s cam %d has "
                        "frame_id=%d (expected 1) — likely stale from previous "
                        "scan; dropping sample",
                        side, cam_id, raw_frame_id,
                    )
                    continue

            # --- 1. Unwrap frame ID -------------------------------------------
            if key not in self._unwrappers:
                self._unwrappers[key] = FrameIdUnwrapper()
            absolute_frame = self._unwrappers[key].unwrap(raw_frame_id)

            # --- 2. Compute BFI/BVI metrics ------------------------------------
            sample = compute_realtime_metrics(
                side=side,
                cam_id=cam_id,
                frame_id=raw_frame_id,
                absolute_frame_id=absolute_frame,
                timestamp_s=ts,
                hist=hist,
                row_sum=row_sum,
                temperature_c=temp,
                bfi_c_min=self._bfi_c_min,
                bfi_c_max=self._bfi_c_max,
                bfi_i_min=self._bfi_i_min,
                bfi_i_max=self._bfi_i_max,
            )

            # --- 3. Dark-frame correction --------------------------------------
            state = self._correction_state.get(key)
            if state is None:
                state = {"count": 0, "last_bfi": None, "last_bvi": None}
                self._correction_state[key] = state

            state["count"] += 1
            in_warmup = state["count"] <= self._correction_warmup_count

            if in_warmup:
                bfi_corr, bvi_corr = sample.bfi, sample.bvi
            else:
                bfi_corr, bvi_corr = compute_corrected_values(
                    mean_val=sample.mean,
                    bfi_val=sample.bfi,
                    bvi_val=sample.bvi,
                    last_bfi=state["last_bfi"],
                    last_bvi=state["last_bvi"],
                    mean_threshold=self._correction_mean_threshold,
                )

            # Only update the held values when the frame is bright enough to
            # be a valid (non-dark) measurement.
            if sample.mean >= self._correction_mean_threshold:
                state["last_bfi"] = bfi_corr
                state["last_bvi"] = bvi_corr

            corrected = CorrectedSample(
                side=side,
                cam_id=cam_id,
                frame_id=raw_frame_id,
                absolute_frame_id=absolute_frame,
                timestamp_s=ts,
                mean=sample.mean,
                std_dev=sample.std_dev,
                contrast=sample.contrast,
                bfi_corrected=bfi_corr,
                bvi_corrected=bvi_corr,
            )

            # --- 4. Per-sample callback (real-time GUI update) -----------------
            if self._on_corrected_fn:
                try:
                    self._on_corrected_fn(corrected)
                except Exception:
                    pass

            # --- 5. Accumulate into frame buffer ---------------------------------
            if key in self._expected_keys:
                buf = self._frame_buffers.get(absolute_frame)
                if buf is None:
                    buf = _FrameBuffer(
                        arrived_at=time.monotonic(),
                        min_timestamp_s=ts,
                    )
                    self._frame_buffers[absolute_frame] = buf
                buf.samples[key] = corrected
                buf.min_timestamp_s = min(buf.min_timestamp_s, ts)

                # Emit as soon as all expected cameras have reported.
                if self._expected_keys.issubset(buf.samples.keys()):
                    self._emit_science_frame(absolute_frame, buf)
                    del self._frame_buffers[absolute_frame]

            self._flush_stale_frames()

        # Drain any leftover partial frames on shutdown.
        for abs_frame in sorted(self._frame_buffers):
            self._emit_science_frame(abs_frame, self._frame_buffers[abs_frame])
        self._frame_buffers.clear()

    def _flush_stale_frames(self) -> None:
        """Emit and evict frames that have been waiting longer than the timeout."""
        if not self._frame_buffers:
            return
        now = time.monotonic()
        stale = [
            f for f, buf in self._frame_buffers.items()
            if now - buf.arrived_at >= self._frame_timeout_s
        ]
        for abs_frame in sorted(stale):
            buf = self._frame_buffers.pop(abs_frame)
            if buf.samples:
                logger.debug(
                    "Flushing incomplete frame %d (%d/%d cameras)",
                    abs_frame,
                    len(buf.samples),
                    len(self._expected_keys),
                )
                self._emit_science_frame(abs_frame, buf)

    def _emit_science_frame(self, absolute_frame: int, buf: _FrameBuffer) -> None:
        # --- Frame ID synchronization check ------------------------------------
        # All samples in a completed trigger frame should share the same raw
        # frame_id.  A mismatch means left and right (or multiple cameras on
        # one side) are out of step — typically caused by dropped packets on
        # one side that let that side advance its counter ahead of the other.
        if buf.samples:
            raw_ids_by_key = {key: s.frame_id for key, s in buf.samples.items()}
            unique_raw_ids = set(raw_ids_by_key.values())
            if len(unique_raw_ids) > 1:
                self._sync_error_count += 1
                detail = ", ".join(
                    f"{side}/cam{cam_id}={fid}"
                    for (side, cam_id), fid in sorted(raw_ids_by_key.items())
                )
                logger.error(
                    "SciencePipeline: frame ID sync error on absolute frame %d "
                    "(sync_errors=%d): %s",
                    absolute_frame, self._sync_error_count, detail,
                )

        if not self._on_science_frame_fn:
            return
        frame = ScienceFrame(
            absolute_frame=absolute_frame,
            frame_id=absolute_frame % FRAME_ID_MODULUS,
            timestamp_s=buf.min_timestamp_s,
            samples=dict(buf.samples),
        )
        try:
            self._on_science_frame_fn(frame)
        except Exception:
            pass


def create_science_pipeline(
    *,
    left_camera_mask: int = 0xFF,
    right_camera_mask: int = 0xFF,
    bfi_c_min,
    bfi_c_max,
    bfi_i_min,
    bfi_i_max,
    on_corrected_fn: Callable[[CorrectedSample], None] | None = None,
    on_science_frame_fn: Callable[[ScienceFrame], None] | None = None,
    frame_timeout_s: float = 0.5,
    correction_warmup_count: int = 10,
    correction_mean_threshold: float = 66.0,
    expected_row_sum: int | None = None,
) -> SciencePipeline:
    """
    Factory for a ready-to-run unified science pipeline.

    Parameters
    ----------
    expected_row_sum
        When not None, samples enqueued directly into the pipeline whose
        histogram bin sum does not match this value are discarded before metric
        computation.  Complements the sum check already performed during binary
        packet parsing.  Pass None (default) to disable.
    """
    pipeline = SciencePipeline(
        left_camera_mask=left_camera_mask,
        right_camera_mask=right_camera_mask,
        bfi_c_min=bfi_c_min,
        bfi_c_max=bfi_c_max,
        bfi_i_min=bfi_i_min,
        bfi_i_max=bfi_i_max,
        on_corrected_fn=on_corrected_fn,
        on_science_frame_fn=on_science_frame_fn,
        frame_timeout_s=frame_timeout_s,
        correction_warmup_count=correction_warmup_count,
        correction_mean_threshold=correction_mean_threshold,
        expected_row_sum=expected_row_sum,
    )
    pipeline.start()
    return pipeline


# ---------------------------------------------------------------------------
# Backward-compatibility shims
# ---------------------------------------------------------------------------
# The old RealtimeProcessingPipeline split work across two threads (metric
# worker + correction worker) and was constructed per-side.  New code should
# use SciencePipeline / create_science_pipeline instead.

class RealtimeProcessingPipeline(SciencePipeline):
    """Deprecated — use SciencePipeline instead."""

    def __init__(
        self,
        side: str,
        *,
        bfi_c_min,
        bfi_c_max,
        bfi_i_min,
        bfi_i_max,
        on_sample_fn: Callable[[RealtimeSample], None] | None = None,
        on_corrected_fn: Callable[[CorrectedSample], None] | None = None,
        correction_warmup_count: int = 10,
        correction_mean_threshold: float = 66.0,
    ):
        # Map old per-side construction to unified pipeline with a single side.
        mask = 0xFF
        left_mask = mask if side == "left" else 0x00
        right_mask = mask if side == "right" else 0x00
        super().__init__(
            left_camera_mask=left_mask,
            right_camera_mask=right_mask,
            bfi_c_min=bfi_c_min,
            bfi_c_max=bfi_c_max,
            bfi_i_min=bfi_i_min,
            bfi_i_max=bfi_i_max,
            on_corrected_fn=on_corrected_fn,
            correction_warmup_count=correction_warmup_count,
            correction_mean_threshold=correction_mean_threshold,
        )
        self.side = side
        self._on_sample_fn = on_sample_fn  # kept for compat; not called in new design

    def enqueue(  # type: ignore[override]
        self,
        cam_id: int,
        frame_id: int,
        timestamp_s: float,
        hist: np.ndarray,
        row_sum: int,
        temperature_c: float,
    ) -> None:
        """Old signature without side argument — uses the side set at construction."""
        super().enqueue(self.side, cam_id, frame_id, timestamp_s, hist, row_sum, temperature_c)


def create_realtime_processing_pipeline(
    side: str,
    *,
    bfi_c_min,
    bfi_c_max,
    bfi_i_min,
    bfi_i_max,
    on_sample_fn: Callable[[RealtimeSample], None] | None = None,
    on_corrected_fn: Callable[[CorrectedSample], None] | None = None,
    correction_warmup_count: int = 10,
    correction_mean_threshold: float = 66.0,
) -> RealtimeProcessingPipeline:
    """Deprecated — use create_science_pipeline instead."""
    pipeline = RealtimeProcessingPipeline(
        side=side,
        bfi_c_min=bfi_c_min,
        bfi_c_max=bfi_c_max,
        bfi_i_min=bfi_i_min,
        bfi_i_max=bfi_i_max,
        on_sample_fn=on_sample_fn,
        on_corrected_fn=on_corrected_fn,
        correction_warmup_count=correction_warmup_count,
        correction_mean_threshold=correction_mean_threshold,
    )
    pipeline.start()
    return pipeline
