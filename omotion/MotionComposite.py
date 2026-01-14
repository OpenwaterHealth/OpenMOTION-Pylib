# MotionComposite.py
import asyncio
import logging
import usb.core
import usb.util
import threading
from omotion.utils import util_crc16
from omotion.CommInterface import CommInterface
from omotion.StreamInterface import StreamInterface
from omotion.signal_wrapper import SignalWrapper
from omotion.config import OW_START_BYTE, OW_END_BYTE, OW_ERROR, OW_RESP, OW_CMD_NOP, OW_ACK
from omotion import _log_root

logger = logging.getLogger(f"{_log_root}.MotionComposite" if _log_root else "MotionComposite")

# ===============================
# One Physical Composite Device
# ===============================
class MotionComposite(SignalWrapper):
    def __init__(self, dev, desc="COMPOSITE", async_mode=True):
        super().__init__()
        self.dev = dev
        self.desc = desc
        async_mode = True
        self.async_mode = async_mode
        self.running = False
        self.demo_mode = False

        # Interfaces
        self.comm = CommInterface(dev, 0, desc=f"{desc}-COMM", async_mode=async_mode)
        self.histo = StreamInterface(dev, 1, desc=f"{desc}-HISTO")
        self.imu = StreamInterface(dev, 2, desc=f"{desc}-IMU")

        self.packet_count = 0
        self.read_buffer = bytearray()

        self.stop_event = threading.Event()
        self.pause_event = threading.Event()


    def connect(self):
        self.dev.set_configuration()
        self.comm.claim()
        self.histo.claim()
        self.imu.claim()

        # Always start read thread if in async mode (or if we want to process packets)
        if self.async_mode:
            self.comm.start_read_thread()

        self.running = True
        self.signal_connect.emit(self.desc, "composite_usb")
        logger.info(f"{self.desc}: Connected")

    def disconnect(self):
        if self.async_mode:
            self.comm.stop_read_thread()
        self.histo.stop_streaming()
        self.imu.stop_streaming()
        
        self.comm.release()
        self.histo.release()
        self.imu.release()

        self.running = False
        usb.util.dispose_resources(self.dev)
        self.signal_disconnect.emit(self.desc, "composite_usb")
        logger.info(f"{self.desc}: Disconnected")
    
    def is_connected(self) -> bool:
        """
        Check if the device is connected.
        """
        return self.running
    
    def check_usb_status(self):
        """
        Check if the device is connected.
        """
        return self.running