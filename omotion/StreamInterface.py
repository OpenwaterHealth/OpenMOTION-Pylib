import logging
import struct
import usb.core
import usb.util
import threading
from omotion.USBInterfaceBase import USBInterfaceBase
from omotion.config import TYPE_HISTO, TYPE_HISTO_CMP
from omotion import _log_root

logger = logging.getLogger(
    f"{_log_root}.StreamInterface" if _log_root else "StreamInterface"
)


def _rle_decompress(data: bytes) -> bytes:
    """Decompress PackBits-style byte-level RLE data."""
    result = bytearray()
    i = 0
    n = len(data)
    while i < n:
        ctrl = data[i]
        i += 1
        if ctrl < 0x80:
            # Literal run: (ctrl + 1) bytes follow
            count = ctrl + 1
            result.extend(data[i : i + count])
            i += count
        else:
            # Repeat run: next byte repeated (ctrl - 0x80 + 3) times
            count = ctrl - 0x80 + 3
            result.extend(bytes([data[i]]) * count)
            i += 1
    return bytes(result)


_HEADER_SIZE = 6      # SOF(1) + type(1) + size(4)
_FOOTER_SIZE = 3      # CRC(2) + EOF(1)
# TYPE_HISTO_CMP packets have an extra 2-byte CRC-16 of the *uncompressed*
# payload inserted immediately before the normal footer.
_UNCMP_CRC_SIZE = 2


try:
    from omotion.utils import util_crc16 as _util_crc16
except ImportError:
    import binascii

    def _util_crc16(buf):
        return binascii.crc_hqx(buf, 0xFFFF)


def _decompress_histo_cmp(raw: bytes) -> bytes:
    """
    Given a raw TYPE_HISTO_CMP packet, decompress the payload and return
    a reconstructed TYPE_HISTO packet (so downstream consumers are unaffected).

    Packet layout (TYPE_HISTO_CMP):
      [Header 6B][Compressed payload N B][UNCMP_CRC16 2B][PKT_CRC16 2B][EOF 1B]

    Two CRCs are checked:
      1. PKT_CRC16  – covers header + compressed payload + UNCMP_CRC16 (transport integrity)
      2. UNCMP_CRC16 – covers the *decompressed* payload (decompressor correctness)

    Raises ValueError on any integrity failure.
    """
    if len(raw) < _HEADER_SIZE + _UNCMP_CRC_SIZE + _FOOTER_SIZE:
        raise ValueError("Compressed packet too small")

    # ── 1. Verify transport CRC (covers everything before PKT_CRC) ──
    footer_off = len(raw) - _FOOTER_SIZE        # offset of PKT_CRC16
    pkt_crc_expected = struct.unpack_from("<H", raw, footer_off)[0]
    if raw[footer_off + 2] != 0xDD:
        raise ValueError("Compressed packet missing EOF marker")
    pkt_crc_actual = _util_crc16(raw[: footer_off - 1])   # matches firmware range
    if pkt_crc_actual != pkt_crc_expected:
        raise ValueError(
            f"Compressed packet CRC mismatch "
            f"(got 0x{pkt_crc_actual:04X}, expected 0x{pkt_crc_expected:04X})"
        )

    # ── 2. Extract UNCMP_CRC16 (sits just before the footer) ──
    uncmp_crc_off = footer_off - _UNCMP_CRC_SIZE
    uncmp_crc_expected = struct.unpack_from("<H", raw, uncmp_crc_off)[0]

    # ── 3. Decompress (compressed payload is between header and UNCMP_CRC) ──
    compressed_payload = raw[_HEADER_SIZE : uncmp_crc_off]
    decompressed = _rle_decompress(compressed_payload)

    # ── 4. Verify decompressed payload CRC ──
    uncmp_crc_actual = _util_crc16(decompressed[:-1])   # same off-by-one as firmware
    if uncmp_crc_actual != uncmp_crc_expected:
        raise ValueError(
            f"Decompressed payload CRC mismatch "
            f"(got 0x{uncmp_crc_actual:04X}, expected 0x{uncmp_crc_expected:04X}) "
            f"— decompressor produced wrong output"
        )

    # ── 5. Rebuild as a TYPE_HISTO packet ──
    new_total = _HEADER_SIZE + len(decompressed) + _FOOTER_SIZE
    header = struct.pack("<BBI", raw[0], TYPE_HISTO, new_total)

    # Recompute CRC for the reconstructed packet (excluding last byte, matching firmware)
    crc_data = header + decompressed
    crc = _util_crc16(crc_data[: len(crc_data) - 1])
    footer = struct.pack("<HB", crc, 0xDD)

    return header + decompressed + footer


# =========================================
# Stream Interface (IN only + thread + queue)
# =========================================
class StreamInterface(USBInterfaceBase):
    def __init__(self, dev, interface_index, desc="Stream"):
        super().__init__(dev, interface_index, desc)
        self.thread = None
        self.stop_event = threading.Event()
        self.data_queue = None
        self.expected_size = None
        self.isStreaming = False

    def start_streaming(self, queue_obj, expected_size):
        if self.thread and self.thread.is_alive():
            logger.info(f"{self.desc}: Stream already running")
            return
        self.data_queue = queue_obj
        self.expected_size = expected_size
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._stream_loop, daemon=True)
        self.thread.start()
        self.isStreaming = True
        logger.info(f"{self.desc}: Streaming started")

    def stop_streaming(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join()
        self.isStreaming = False
        self.data_queue = None
        self.expected_size = None
        logger.info(f"{self.desc}: Streaming stopped")

    def _process_packet(self, raw, pkt_count, cmp_count, cmp_errors):
        """Process a single framed packet: decompress if needed, queue it.
        Returns (cmp_count, cmp_errors)."""
        pkt_type = raw[1] if len(raw) > 1 else -1

        if pkt_type == TYPE_HISTO_CMP:
            cmp_count += 1
            compressed_size = len(raw)
            try:
                raw = _decompress_histo_cmp(raw)
                decompressed_size = len(raw)
                if cmp_count <= 3 or cmp_count % 100 == 0:
                    logger.info(
                        f"{self.desc}: [CMP] pkt#{pkt_count} decompressed "
                        f"{compressed_size} -> {decompressed_size} bytes "
                        f"({cmp_count} compressed so far)"
                    )
            except Exception as exc:
                cmp_errors += 1
                logger.error(
                    f"{self.desc}: [CMP] decompression FAILED pkt#{pkt_count}, "
                    f"compressed_size={compressed_size}, "
                    f"error={exc} (errors: {cmp_errors}/{cmp_count})"
                )
                return cmp_count, cmp_errors  # drop corrupted packet
        elif pkt_type not in (TYPE_HISTO, TYPE_HISTO_CMP) and pkt_count <= 5:
            logger.warning(
                f"{self.desc}: unexpected pkt_type=0x{pkt_type:02X}, "
                f"len={len(raw)}, pkt#{pkt_count}"
            )

        if self.data_queue:
            self.data_queue.put(raw)
        return cmp_count, cmp_errors

    def _stream_loop(self):
        """
        USB bulk transfers are NOT guaranteed to be packet-aligned: the host
        stack may concatenate multiple transfers into one read (when compressed
        packets are much smaller than expected_size) or return partial reads
        (just one 512-byte USB bulk fragment).

        We solve this with a framing layer: accumulate USB reads into a buffer,
        then extract complete packets using the 4-byte size field in each
        packet header (bytes 2-5).
        """
        pkt_count = 0
        cmp_count = 0
        cmp_errors = 0
        usb_errors = 0
        framing_resyncs = 0
        rx_buf = bytearray()

        while not self.stop_event.is_set():
            try:
                data = self.dev.read(
                    self.ep_in.bEndpointAddress, self.expected_size, timeout=100
                )
                if data:
                    rx_buf.extend(data)
            except usb.core.USBError as e:
                if e.errno not in (110, 10060):
                    usb_errors += 1
                    logger.error(
                        f"{self.desc} stream USB error #{usb_errors}: {e} "
                        f"(after {pkt_count} pkts, {cmp_count} compressed)"
                    )
                # On timeout (110/10060), fall through to process any buffered data

            # ── Extract complete packets from the receive buffer ──
            while len(rx_buf) >= _HEADER_SIZE:
                # Validate SOF marker
                if rx_buf[0] != 0xAA:
                    # Lost sync — scan forward for next SOF byte
                    sof_pos = rx_buf.find(0xAA, 1)
                    if sof_pos == -1:
                        skipped = len(rx_buf)
                        rx_buf.clear()
                    else:
                        skipped = sof_pos
                        del rx_buf[:sof_pos]
                    framing_resyncs += 1
                    if framing_resyncs <= 20:
                        logger.warning(
                            f"{self.desc}: [FRAME] lost sync, skipped {skipped} bytes "
                            f"(resync #{framing_resyncs})"
                        )
                    continue

                # Read packet size from header (bytes 2-5, little-endian uint32)
                pkt_len = struct.unpack_from("<I", rx_buf, 2)[0]

                # Sanity check the size field
                if pkt_len < _HEADER_SIZE + _FOOTER_SIZE or pkt_len > self.expected_size:
                    # Bad size — likely not a real packet header; skip this SOF byte
                    del rx_buf[:1]
                    framing_resyncs += 1
                    if framing_resyncs <= 20:
                        logger.warning(
                            f"{self.desc}: [FRAME] bad pkt_len={pkt_len}, "
                            f"skipping SOF (resync #{framing_resyncs})"
                        )
                    continue

                # Wait for the complete packet to arrive
                if len(rx_buf) < pkt_len:
                    break  # need more data

                # Extract the complete packet
                raw = bytes(rx_buf[:pkt_len])
                del rx_buf[:pkt_len]
                pkt_count += 1

                cmp_count, cmp_errors = self._process_packet(
                    raw, pkt_count, cmp_count, cmp_errors
                )

        # Drain any remaining complete packets in the buffer
        while len(rx_buf) >= _HEADER_SIZE:
            if rx_buf[0] != 0xAA:
                break
            pkt_len = struct.unpack_from("<I", rx_buf, 2)[0]
            if pkt_len < _HEADER_SIZE + _FOOTER_SIZE or pkt_len > self.expected_size:
                break
            if len(rx_buf) < pkt_len:
                break
            raw = bytes(rx_buf[:pkt_len])
            del rx_buf[:pkt_len]
            pkt_count += 1
            cmp_count, cmp_errors = self._process_packet(
                raw, pkt_count, cmp_count, cmp_errors
            )

        # Log summary when stream loop exits
        logger.info(
            f"{self.desc}: stream loop exited. "
            f"Total pkts={pkt_count}, compressed={cmp_count}, "
            f"cmp_errors={cmp_errors}, usb_errors={usb_errors}, "
            f"framing_resyncs={framing_resyncs}, "
            f"residual_buf={len(rx_buf)}"
        )
