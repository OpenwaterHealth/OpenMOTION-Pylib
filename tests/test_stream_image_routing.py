"""StreamInterface TYPE_IMAGE routing — software-only (no USB device).

StreamInterface.__init__ (via USBInterfaceBase.__init__) only stores its
arguments, so a StreamInterface(None, 1, "test") is safe to construct and
lets us drive _route_chunk directly.
"""

import queue

import pytest

pytestmark = pytest.mark.unit

from omotion.StreamInterface import StreamInterface, extract_stream_packets


def _histo_pkt(payload=b"\x01\x02\x03\x04"):
    """Minimal TYPE_HISTO envelope: routing checks framing only, not CRC."""
    total = 6 + len(payload) + 3
    return (bytes([0xAA, 0x00]) + total.to_bytes(4, "little")
            + payload + bytes([0x00, 0x00, 0xDD]))


def _image_pkt(fill=0x55):
    """Minimal well-framed TYPE_IMAGE envelope (2424 B). Routing does not
    parse the payload, so a constant fill body is sufficient here."""
    body = bytes([fill]) * (4 + 1 + 1 + 2408 + 1)   # timestamp..EOH region, framing only
    total = 6 + len(body) + 3
    pkt = bytearray(bytes([0xAA, 0x03]) + total.to_bytes(4, "little")
                    + body + bytes([0x00, 0x00, 0xDD]))
    pkt[10] = 0xFF                                # SOH
    pkt[len(pkt) - 4] = 0xEE                     # EOH
    return bytes(pkt)


def test_extract_splits_and_classifies():
    """One histo + one image packet concatenated -> each lands in its list
    and the buffer is fully consumed."""
    buf = bytearray(_histo_pkt() + _image_pkt())
    histo, image = extract_stream_packets(buf)
    assert [p[1] for p in histo] == [0x00]
    assert [p[1] for p in image] == [0x03]
    assert len(buf) == 0


def test_extract_holds_partial_packet():
    """A packet split across USB chunks stays buffered until complete."""
    ip = _image_pkt()
    buf = bytearray(ip[:1000])
    histo, image = extract_stream_packets(buf)
    assert histo == [] and image == []
    assert len(buf) == 1000
    buf += ip[1000:]
    histo, image = extract_stream_packets(buf)
    assert len(image) == 1 and image[0] == ip and len(buf) == 0


def test_extract_resyncs_past_garbage():
    """Garbage before SOF and a corrupt EOF are skipped without losing the
    following good packet."""
    good = _histo_pkt()
    bad = bytearray(_histo_pkt())
    bad[-1] = 0x00   # break EOF -> forces the 1-byte resync path
    buf = bytearray(b"\x00\x12" + bytes(bad) + good)
    histo, image = extract_stream_packets(buf)
    assert good in histo


def test_route_chunk_separates_queues():
    """End-to-end through the instance method: image packets only ever appear
    in image_queue, histogram packets only in data_queue — the guarantee that
    histogram consumers never see image traffic."""
    st = StreamInterface(None, 1, desc="test")
    hq: queue.Queue = queue.Queue()
    iq: queue.Queue = queue.Queue()
    st.data_queue = hq
    st.image_queue = iq
    st._route_buf = bytearray()

    ip, hp = _image_pkt(), _histo_pkt()
    blob = hp + ip + hp
    # feed in awkward chunk sizes to cross packet boundaries
    for i in range(0, len(blob), 700):
        st._route_chunk(blob[i:i + 700], hq)

    got_h = [hq.get_nowait() for _ in range(hq.qsize())]
    got_i = [iq.get_nowait() for _ in range(iq.qsize())]
    assert got_h == [hp, hp]
    assert got_i == [ip]
