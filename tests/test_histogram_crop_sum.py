"""Histogram sum-validation accepts cropped frames (issue #150).

The sensor-fw debug crop (DEBUG_FLAG_CAMERA_CROP, sensor-fw #86) streams
1720x1280 frames whose bins sum to 2,201,606 instead of the full-frame
2,457,606. The parser must accept both geometries while still dropping
genuinely corrupt (partial/doubled) frames.
"""
import struct

import numpy as np
import pytest

from omotion.config import TYPE_HISTO
from omotion.MotionProcessing import (
    EOF,
    EOH,
    EXPECTED_HISTOGRAM_SUM,
    EXPECTED_HISTOGRAM_SUMS,
    HISTO_SIZE_WORDS,
    SOF,
    SOH,
    _crc16,
    _histogram_sum_ok,
    _resolve_valid_sums,
    parse_histogram_packet_structured,
)

FULL_SUM = 1920 * 1280 + 6   # 2,457,606
CROP_SUM = 1720 * 1280 + 6   # 2,201,606


def _build_histo_packet(total: int, cam_id: int = 0, frame_id: int = 7,
                        temp: float = 25.0) -> bytes:
    """Build a single-camera TYPE_HISTO wire packet whose bins sum to `total`.

    Mirrors the firmware framing the parser expects: 6-byte header, one
    [SOH, cam_id, 1024xu32 LE, f32 temp, EOH] block, then [crc16, EOF]. The
    frame id lives in the high byte of the last histogram word.
    """
    hist = np.zeros(HISTO_SIZE_WORDS, dtype=np.uint32)
    hist[0] = total                       # whole count in bin 0
    hist[-1] = (frame_id & 0xFF) << 24    # frame id in high byte, 0 count

    block = (bytes([SOH, cam_id]) + hist.tobytes()
             + struct.pack("<f", temp) + bytes([EOH]))
    pkt_len = 6 + len(block) + 3          # header + body + [crc16, EOF]
    header = struct.pack("<BBI", SOF, TYPE_HISTO, pkt_len)
    # Parser checks _crc16 over pkt[:pkt_len-4] == header + body without its
    # trailing EOH byte.
    crc = _crc16(memoryview(header + block[:-1]))
    return header + block + struct.pack("<H", crc) + bytes([EOF])


def _parse(total: int, **kw):
    pkt = _build_histo_packet(total)
    return parse_histogram_packet_structured(memoryview(pkt), **kw)


def test_full_frame_roundtrip_sanity():
    """A full-frame packet parses into exactly one sample (self-check of the
    test's own framing/CRC before we trust the crop cases)."""
    result = _parse(FULL_SUM)
    assert len(result.samples) == 1
    s = result.samples[0]
    assert s.row_sum == FULL_SUM
    assert s.frame_id == 7


def test_cropped_frame_is_accepted():
    """The 1720x1280 cropped total must NOT be dropped (the bug)."""
    result = _parse(CROP_SUM)
    assert len(result.samples) == 1
    assert result.samples[0].row_sum == CROP_SUM


@pytest.mark.parametrize("bad", [
    FULL_SUM * 2,      # doubled/concatenated frame
    CROP_SUM * 2,
    FULL_SUM - 1000,   # partial frame
    12345,             # garbage
])
def test_corrupt_sums_are_dropped(bad):
    """Sums matching no known geometry are still rejected."""
    result = _parse(bad)
    assert result.samples == []


def test_explicit_single_int_still_exact_matches():
    """Passing an int keeps legacy exact-match behavior."""
    assert _parse(CROP_SUM, expected_row_sum=FULL_SUM).samples == []
    assert len(_parse(FULL_SUM, expected_row_sum=FULL_SUM).samples) == 1


def test_empty_iterable_disables_check():
    """An empty accepted-set accepts any sum."""
    assert len(_parse(999_999, expected_row_sum=()).samples) == 1


def test_expected_sums_set_contents():
    assert EXPECTED_HISTOGRAM_SUMS == frozenset({FULL_SUM, CROP_SUM})
    assert EXPECTED_HISTOGRAM_SUM == FULL_SUM  # backward-compat alias


def test_resolve_and_predicate_helpers():
    assert _resolve_valid_sums(None) == EXPECTED_HISTOGRAM_SUMS
    assert _resolve_valid_sums(FULL_SUM) == frozenset({FULL_SUM})
    assert _resolve_valid_sums([1, 2, 3]) == frozenset({1, 2, 3})
    assert _resolve_valid_sums(()) is None          # empty -> disabled
    assert _histogram_sum_ok(CROP_SUM, None) is True
    assert _histogram_sum_ok(999, None) is False
    assert _histogram_sum_ok(999, ()) is True       # disabled -> accept
