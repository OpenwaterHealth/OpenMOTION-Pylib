import csv
import io
import os
import struct
from typing import Dict, Tuple, List

import numpy as np

try:
    # Your accelerated C implementation
    from omotion.utils import util_crc16 as _crc16
except ImportError:
    # Fall back to binascii if util_crc16 isn't available
    import binascii

    def _crc16(buf: memoryview) -> int:
        return binascii.crc_hqx(buf, 0xFFFF)


# ─── Constants ──────────────────────────────────────────────────────────────
HISTO_SIZE_WORDS = 1024
HISTO_BYTES = HISTO_SIZE_WORDS * 4  # 4 096
PACKET_HEADER_SIZE = 6
PACKET_FOOTER_SIZE = 3
HISTO_BLOCK_SIZE = 1 + 1 + HISTO_BYTES + 4 + 1  # SOH + cam + histo + temp + EOH
MIN_PACKET_SIZE = PACKET_HEADER_SIZE + PACKET_FOOTER_SIZE + HISTO_BLOCK_SIZE

SOF, SOH, EOH, EOF = 0xAA, 0xFF, 0xEE, 0xDD

# ─── Pre‑compiled struct formats ────────────────────────────────────────────
_U32  = struct.Struct("<I")
_U16  = struct.Struct("<H")
_F32  = struct.Struct("<f")
_HDR  = struct.Struct("<BBI")          # SOF, type, length
_BLK_HEAD = struct.Struct("<BB")       # SOH, cam_id

# ─── Fast helpers ───────────────────────────────────────────────────────────
def _get_u32(buf: memoryview, offset: int) -> int:
    return _U32.unpack_from(buf, offset)[0]

def _crc_matches(pkt: memoryview, crc_expected: int) -> bool:
    return _crc16(pkt) == crc_expected


# ─── Packet parser (no Python loops inside the histogram) ───────────────────
def parse_histogram_packet(pkt: memoryview) -> Tuple[
        Dict[int, np.ndarray], Dict[int, int], Dict[int, float], int]:
    """
    Returns histograms, frame‑ids, temperatures, bytes_consumed
    Raises ValueError on format errors (CRC mismatch, etc.)
    """

    data = pkt[0:10].hex()
    if len(pkt) < MIN_PACKET_SIZE:
        raise ValueError("Packet too small")

    sof, pkt_type, pkt_len = _HDR.unpack_from(pkt, 0)
    if sof != SOF or pkt_type != 0x00:
        raise ValueError("Bad header")
    pkt_len_2 = len(pkt)
    if pkt_len > len(pkt):
        raise ValueError("Truncated packet")

    payload_end = pkt_len - PACKET_FOOTER_SIZE
    off = PACKET_HEADER_SIZE           # start of payload

    hists: Dict[int, np.ndarray] = {}
    ids:   Dict[int, int] = {}
    temps: Dict[int, float] = {}

    mv = pkt  # shorthand

    while off < payload_end:
        soh, cam_id = _BLK_HEAD.unpack_from(mv, off)
        if soh != SOH:
            raise ValueError("Missing SOH")
        off += _BLK_HEAD.size

        # Histogram as a view – no copy!
        hist = np.frombuffer(mv, dtype=np.uint32,
                             count=HISTO_SIZE_WORDS,
                             offset=off)
        off += HISTO_BYTES

        temp = _F32.unpack_from(mv, off)[0]
        off += 4

        if mv[off] != EOH:
            raise ValueError("Missing EOH")
        off += 1

        # strip packet‑id (high byte of last word)
        last_word = hist[-1]
        frame_id = (last_word >> 24) & 0xFF
        hist = hist.copy()             # we will edit a copy
        hist[-1] = last_word & 0x00_FFFF_FF

        hists[cam_id] = hist
        ids[cam_id] = frame_id
        temps[cam_id] = temp

    # Footer
    crc_expected = _U16.unpack_from(mv, off)[0]
    off += 2
    if mv[off] != EOF:
        raise ValueError("Missing EOF")

    if not _crc_matches(mv[:off-3], crc_expected):
        raise ValueError("CRC mismatch")

    return hists, ids, temps, pkt_len


# ─── Main driver ────────────────────────────────────────────────────────────
def process_bin_file(src_bin: str, dst_csv: str,
                     start_offset: int = 0,
                     batch_rows: int = 4096) -> None:
    """Convert binary → CSV quickly."""
    with open(src_bin, "rb") as f:
        data = memoryview(f.read())  # zero‑copy view

    off = start_offset
    packet_ok = packet_fail = crc_failure = other_fail = 0
    out_buf: List[List] = []

    with open(dst_csv, "w", newline="") as fcsv:
        wr = csv.writer(fcsv)
        wr.writerow(
            ["cam_id", "frame_id", *range(HISTO_SIZE_WORDS),
             "temperature", "sum"]
        )

        while off + MIN_PACKET_SIZE <= len(data):
            try:
                hists, ids, temps, consumed = parse_histogram_packet(data[off:])
                off += consumed
                packet_ok += 1
                # assemble CSV rows
                for cam, hist in hists.items():
                    row_sum = int(hist.sum(dtype=np.uint64))
                    out_buf.append(
                        [cam, ids[cam], *hist.tolist(),
                         temps[cam], row_sum]
                    )
                # flush if buffer big
                if len(out_buf) >= batch_rows:
                    wr.writerows(out_buf)
                    out_buf.clear()
            except Exception as exc:
                if(exc.args[0] == "CRC mismatch"):
                    crc_failure += 1
                elif(exc.args[0] == "Missing SOH"):
                    packet_fail += 1
                else:
                    other_fail += 1
                    print("other")

                # ---------- fast resync ----------
                pat = b"\xDD\xAA"        # EOF of bad packet + SOF of next
                mv_slice = data[off:]

                if hasattr(mv_slice, "find"):            # Py ≥ 3.10
                    nxt = mv_slice.find(pat)
                    if nxt != -1:
                        off += nxt + 1                   # keep leading 0xAA
                        continue
                else:                                    # Py ≤ 3.9
                    nxt = data.obj.find(pat, off)        # no extra copy
                    if nxt != -1:
                        off = nxt + 1
                        continue
                # --------------------------------------

                break    # pattern not found → reached end of file

        # write any remaining rows
        if out_buf:
            wr.writerows(out_buf)

    print(f"✅ Done – {packet_ok} packets OK, {packet_fail} failed, {crc_failure} CRC failed, {other_fail} other fail")


# ─── CLI ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    process_bin_file("histogram.bin", "histogram.csv",
                     start_offset=0)
