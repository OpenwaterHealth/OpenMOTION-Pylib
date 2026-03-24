import logging
import time
import usb.core
import usb.util
import threading
from omotion.USBInterfaceBase import USBInterfaceBase
from omotion import _log_root

logger = logging.getLogger(
    f"{_log_root}.StreamInterface" if _log_root else "StreamInterface"
)


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
        self.packets_received: int = 0  # USB transfers queued since last start_streaming

    def start_streaming(self, queue_obj, expected_size):
        if self.thread and self.thread.is_alive():
            logger.info(f"{self.desc}: Stream already running")
            return
        self.data_queue = queue_obj
        self.expected_size = expected_size
        self.packets_received = 0
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._stream_loop, daemon=True)
        self.thread.start()
        self.isStreaming = True
        logger.info(f"{self.desc}: Streaming started")

    def stop_streaming(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2.0)
        self.isStreaming = False
        self.data_queue = None
        self.expected_size = None
        logger.info(
            f"{self.desc}: Streaming stopped — "
            f"{self.packets_received} USB read chunk(s) received"
        )

    def flush_stale_data(
        self,
        expected_size: int,
        read_timeout_ms: int = 50,
        max_total_ms: int = 1500,
    ) -> int:
        """
        Drain and discard any data already buffered in the USB host-side
        endpoint from a previous streaming session.

        Call this *before* ``start_streaming()`` at scan startup, while the
        MCU trigger is still off, so that leftover USB transfers from the
        prior scan cannot appear at the top of the new scan's CSV.

        The flush works by issuing blocking reads (with a short timeout) until
        the endpoint returns a timeout error — at which point the buffer is
        empty and no more data is expected before the trigger fires.

        Parameters
        ----------
        expected_size
            Read buffer size passed to ``dev.read()``.  Use the same value as
            the upcoming ``start_streaming()`` call (i.e. ``request.expected_size``).
        read_timeout_ms
            Milliseconds to wait per read attempt.  Should be short — just
            long enough for the USB host controller to confirm the endpoint is
            empty.  Default 50 ms is sufficient for all known configurations.
        max_total_ms
            Hard cap on total flush duration.  Prevents startup hangs if the
            backend returns empty reads indefinitely or data keeps arriving.

        Returns
        -------
        int
            Number of bytes discarded.
        """
        if self.isStreaming:
            logger.warning(f"{self.desc}: flush_stale_data called while streaming — skipping")
            return 0

        if self.ep_in is None:
            logger.warning(f"{self.desc}: flush_stale_data called before endpoint claimed — skipping")
            return 0

        bytes_discarded = 0
        reads = 0
        t_start = time.monotonic()
        while True:
            if int((time.monotonic() - t_start) * 1000) >= max_total_ms:
                logger.warning(
                    f"{self.desc}: flush_stale_data reached max duration "
                    f"({max_total_ms} ms), proceeding with stream startup"
                )
                break
            try:
                data = self.dev.read(
                    self.ep_in.bEndpointAddress, expected_size, timeout=read_timeout_ms
                )
                reads += 1
                if data:
                    bytes_discarded += len(data)
                else:
                    # Some backends can return an empty read rather than raising
                    # a timeout USBError. Treat as endpoint-empty and stop flush.
                    break
            except usb.core.USBError as e:
                if e.errno in (110, 10060):
                    # Timeout — endpoint buffer is now empty.
                    break
                elif e.errno in (19, 5, 32):
                    # Device lost during flush — stop silently.
                    logger.warning(f"{self.desc}: device error during flush: {e}")
                    break
                else:
                    logger.warning(f"{self.desc}: USB error during flush: {e}")
                    break

        if bytes_discarded:
            logger.info(
                f"{self.desc}: flushed {bytes_discarded} stale bytes "
                f"({bytes_discarded // expected_size} transfer(s)) from USB endpoint"
            )
        elif reads > 0:
            logger.info(f"{self.desc}: stale-data flush complete ({reads} read attempt(s), no payload)")
        return bytes_discarded

    def drain_final(
        self,
        expected_size: int,
        timeout_ms: int = 750,
    ) -> list[bytes]:
        """
        After ``stop_streaming()`` has returned, attempt one or more reads to
        recover any USB transfers that landed in the host-side endpoint buffer
        after ``_stream_loop`` exited.

        This handles the race where the MCU delivers its final bulk transfer
        significantly later than the normal inter-frame cadence (e.g. > 350 ms
        after trigger-off), causing ``_stream_loop`` to exit on a timeout+stop
        before the transfer arrives.

        Parameters
        ----------
        expected_size
            Read buffer size — same value used for ``start_streaming()``.
        timeout_ms
            How long to wait per read attempt.  750 ms is comfortably beyond
            the ~250 ms worst-case MCU DMA flush latency.

        Returns
        -------
        list[bytes]
            Byte chunks recovered (0 or 1 items in the normal case).
        """
        if self.isStreaming:
            logger.warning(f"{self.desc}: drain_final called while streaming — skipping")
            return []
        if self.ep_in is None:
            logger.warning(f"{self.desc}: drain_final called before endpoint claimed — skipping")
            return []

        chunks: list[bytes] = []
        while True:
            try:
                data = self.dev.read(
                    self.ep_in.bEndpointAddress, expected_size, timeout=timeout_ms
                )
                if data:
                    chunks.append(bytes(data))
                    logger.info(
                        f"{self.desc}: drain_final recovered {len(data)} bytes "
                        f"(chunk {len(chunks)})"
                    )
            except usb.core.USBError as e:
                if e.errno in (110, 10060):
                    # Timeout — endpoint is empty, nothing more to recover.
                    break
                elif e.errno in (19, 5, 32):
                    logger.warning(f"{self.desc}: device error during drain_final: {e}")
                    break
                else:
                    logger.warning(f"{self.desc}: USB error during drain_final: {e}")
                    break

        if chunks:
            logger.info(
                f"{self.desc}: drain_final recovered {len(chunks)} chunk(s) "
                f"({sum(len(c) for c in chunks)} bytes total)"
            )
        return chunks

    def _stream_loop(self):
        # Read timeout must exceed the worst-case USB transfer latency for the
        # final frame.  Normal cadence is ~25 ms; the last frame of a scan can
        # take up to 250 ms because the MCU flushes its DMA buffer only after
        # the trigger is stopped.  500 ms gives a comfortable 2x margin.
        #
        # Exit condition: stop was requested AND the last read timed out.
        # A timeout while stop is pending means the endpoint is empty — all
        # in-flight transfers have been received.  Exiting on stop_event alone
        # (the old behaviour) caused the final frame to be dropped whenever it
        # arrived after the 100 ms read window had already closed.
        _READ_TIMEOUT_MS = 500

        while True:
            try:
                data = self.dev.read(
                    self.ep_in.bEndpointAddress, self.expected_size,
                    timeout=_READ_TIMEOUT_MS,
                )
                if data and self.data_queue:
                    self.data_queue.put(bytes(data))
                    self.packets_received += 1
            except usb.core.USBError as e:
                if e.errno in (110, 10060):
                    # Timeout — no data arrived within the read window.
                    if self.stop_event.is_set():
                        # Stop requested and endpoint is now empty: exit cleanly.
                        break
                    # Otherwise keep waiting — scan is still running.
                elif e.errno in (19, 5, 32):
                    # Fatal device errors: ENODEV, EIO, EPIPE — device is gone.
                    logger.error(f"{self.desc} stream error (device lost): {e}")
                    break
                else:
                    logger.error(f"{self.desc} stream error: {e}")
                    if self.stop_event.is_set():
                        break
