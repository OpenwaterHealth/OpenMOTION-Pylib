import csv
import logging
import struct
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import queue
import threading
from omotion import _log_root

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
MIN_PACKET_SIZE = PACKET_HEADER_SIZE + PACKET_FOOTER_SIZE + HISTO_BLOCK_SIZE

SOF, SOH, EOH, EOF = 0xAA, 0xFF, 0xEE, 0xDD
HISTO_BINS = np.arange(HISTO_SIZE_WORDS, dtype=np.float64)

logger = logging.getLogger(
    f"{_log_root}.MotionProcessing" if _log_root else "MotionProcessing"
)

# Struct formats
_U16 = struct.Struct("<H")
_U32 = struct.Struct("<I")
_F32 = struct.Struct("<f")
_HDR = struct.Struct("<BBI")
_BLK_HEAD = struct.Struct("<BB")


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
    Parse a binary histogram packet.

    Returns:
        hists: {camera_id: np.ndarray[uint32] (1024 bins)}
        ids: {camera_id: frame_id}
        temps: {camera_id: temperature_c}
        timestamp_sec: optional packet timestamp in seconds
        bytes_consumed: packet length in bytes
    """
    if len(pkt) < MIN_PACKET_SIZE:
        raise ValueError("Packet too small")

    sof, pkt_type, pkt_len = _HDR.unpack_from(pkt, 0)
    if sof != SOF or pkt_type != 0x00:
        raise ValueError("Bad header")

    if pkt_len > len(pkt):
        raise ValueError("Truncated packet")

    payload_len = pkt_len - PACKET_HEADER_SIZE - PACKET_FOOTER_SIZE
    if payload_len < HISTO_BLOCK_SIZE:
        raise ValueError("Packet payload too small")

    has_timestamp = (payload_len % HISTO_BLOCK_SIZE) == TIMESTAMP_SIZE
    if not has_timestamp and (payload_len % HISTO_BLOCK_SIZE) != 0:
        raise ValueError("Packet length mismatch")

    payload_end = pkt_len - PACKET_FOOTER_SIZE
    off = PACKET_HEADER_SIZE

    hists: Dict[int, np.ndarray] = {}
    ids: Dict[int, int] = {}
    temps: Dict[int, float] = {}
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

        hists[cam_id] = hist
        ids[cam_id] = frame_id
        temps[cam_id] = temp

    crc_expected = _U16.unpack_from(pkt, off)[0]
    off += 2
    if pkt[off] != EOF:
        raise ValueError("Missing EOF")

    if _crc16(pkt[: off - 3]) != crc_expected:
        raise ValueError("CRC mismatch")

    return hists, ids, temps, timestamp_sec, pkt_len


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

        while off + MIN_PACKET_SIZE <= len(data):
            try:
                hists, ids, temps, timestamp_sec, consumed = parse_histogram_packet(
                    data[off:]
                )
                off += consumed
                packet_ok += 1

                ts_val = timestamp_sec if timestamp_sec is not None else 0.0
                for cam_id, hist in hists.items():
                    row_sum = int(hist.sum(dtype=np.uint64))
                    out_buf.append(
                        [cam_id, ids[cam_id], ts_val, *hist.tolist(), temps[cam_id], row_sum]
                    )

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

                # Resync to the next likely packet boundary marker.
                pat = b"\xaa\x00\x41"
                off = off + 1
                nxt = data.obj.find(pat, off)
                if nxt != -1:
                    off = nxt
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
) -> int:
    """
    Parse streaming histogram binary data and write CSV rows.
    Returns number of rows written.
    """
    rows_written = 0

    while not stop_evt.is_set() or not q.empty():
        try:
            data = q.get(timeout=0.100)
            if data:
                buffer_accumulator.extend(data)
            q.task_done()
        except queue.Empty:
            continue

        offset = 0
        while offset + MIN_PACKET_SIZE <= len(buffer_accumulator):
            try:
                pkt_view = memoryview(buffer_accumulator[offset:])
                hists, ids, temps, timestamp_sec, consumed = parse_histogram_packet(pkt_view)
                offset += consumed

                ts_val = timestamp_sec if timestamp_sec is not None else 0.0
                for cam_id, hist in hists.items():
                    row_sum = int(hist.sum(dtype=np.uint64))
                    extra_cols = extra_cols_fn() if extra_cols_fn else []
                    row = [
                        cam_id,
                        ids[cam_id],
                        ts_val,
                        *hist.tolist(),
                        temps[cam_id],
                        row_sum,
                        *extra_cols,
                    ]
                    csv_writer.writerow(row)
                    rows_written += 1
                    if on_row_fn:
                        on_row_fn(cam_id, ids[cam_id], ts_val, hist, row_sum, temps[cam_id])

            except ValueError as e:
                pat = b"\xaa\x00\x41"
                offset += 1
                nxt = buffer_accumulator.find(pat, offset)
                if nxt != -1:
                    offset = nxt
                    logger.warning("Parser error, resyncing: %s", e)
                    continue
                break

        if offset > 0:
            del buffer_accumulator[:offset]

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
) -> int:
    """
    High-level helper: parse stream queue data and write a CSV file end-to-end.

    This owns file open/header/write/close so applications only pass:
    - destination filename
    - queue + stop event
    - optional callbacks for extra columns and row handling.
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
            )
    except Exception as e:
        if on_error_fn:
            on_error_fn(e)
        logger.error("Writer error (%s): %s", filename, e, exc_info=True)
        return rows_written

    if on_complete_fn:
        on_complete_fn(rows_written)
    return rows_written
