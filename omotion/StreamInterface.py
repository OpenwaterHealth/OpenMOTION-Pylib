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


_HEADER_SIZE = 6   # SOF(1) + type(1) + size(4)
_FOOTER_SIZE = 3   # CRC(2) + EOF(1)


def _decompress_histo_cmp(raw: bytes) -> bytes:
    """
    Given a raw TYPE_HISTO_CMP packet, decompress the payload and return
    a reconstructed TYPE_HISTO packet (so downstream consumers are unaffected).
    """
    if len(raw) < _HEADER_SIZE + _FOOTER_SIZE:
        return raw  # too small, pass through

    # Decompress the payload between header and footer
    compressed_payload = raw[_HEADER_SIZE : len(raw) - _FOOTER_SIZE]
    decompressed = _rle_decompress(compressed_payload)

    # Rebuild as a TYPE_HISTO packet: header + decompressed payload + footer
    new_total = _HEADER_SIZE + len(decompressed) + _FOOTER_SIZE
    header = struct.pack("<BBI", raw[0], TYPE_HISTO, new_total)

    # Recompute CRC over header + decompressed payload (excluding last byte, matching firmware)
    try:
        from omotion.utils import util_crc16
    except ImportError:
        import binascii
        def util_crc16(buf):
            return binascii.crc_hqx(buf, 0xFFFF)

    crc_data = header + decompressed
    crc = util_crc16(crc_data[: len(crc_data) - 1])
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

    def _stream_loop(self):
        pkt_count = 0
        cmp_count = 0
        cmp_errors = 0
        usb_errors = 0
        while not self.stop_event.is_set():
            try:
                data = self.dev.read(
                    self.ep_in.bEndpointAddress, self.expected_size, timeout=100
                )
                if data and self.data_queue:
                    raw = bytes(data)
                    pkt_count += 1
                    pkt_type = raw[1] if len(raw) > 1 else -1

                    # If this is a compressed histogram packet, decompress it
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
                    elif pkt_type not in (TYPE_HISTO, TYPE_HISTO_CMP) and pkt_count <= 5:
                        logger.warning(
                            f"{self.desc}: unexpected pkt_type=0x{pkt_type:02X}, "
                            f"len={len(raw)}, pkt#{pkt_count}"
                        )

                    self.data_queue.put(raw)
            except usb.core.USBError as e:
                if e.errno not in (110, 10060):
                    usb_errors += 1
                    logger.error(
                        f"{self.desc} stream USB error #{usb_errors}: {e} "
                        f"(after {pkt_count} pkts, {cmp_count} compressed)"
                    )
        # Log summary when stream loop exits
        if cmp_count > 0:
            logger.info(
                f"{self.desc}: stream loop exited. "
                f"Total pkts={pkt_count}, compressed={cmp_count}, "
                f"cmp_errors={cmp_errors}, usb_errors={usb_errors}"
            )
