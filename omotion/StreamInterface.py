import logging
import usb.core
import usb.util
import threading
import queue
from omotion.USBInterfaceBase import USBInterfaceBase

logger = logging.getLogger("StreamInterface")
logger.setLevel(logging.INFO)

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
        while not self.stop_event.is_set():
            try:
                data = self.dev.read(self.ep_in.bEndpointAddress, self.expected_size, timeout=100)
                if data and self.data_queue:
                    self.data_queue.put(bytes(data))
            except usb.core.USBError as e:
                if e.errno != 110:
                    logger.error(f"{self.desc} stream error: {e}")

