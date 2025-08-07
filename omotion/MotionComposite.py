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

logger = logging.getLogger("MotionComposite")
logger.setLevel(logging.INFO)

# ===============================
# One Physical Composite Device
# ===============================
class MotionComposite(SignalWrapper):
    def __init__(self, dev, desc="COMPOSITE", async_mode=False):
        super().__init__()
        self.dev = dev
        self.desc = desc
        self.async_mode = async_mode
        self.running = False
        self.demo_mode = False

        # Interfaces
        self.comm = CommInterface(dev, 0, desc=f"{desc}-COMM")
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

        if self.async_mode:
            self.comm.start_read_thread()

        self.running = True
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
        logger.info(f"{self.desc}: Disconnected")

    def is_connected(self) -> bool:
        """
        Return True if the USB device is still connected.
        In demo mode, always returns True.
        """
        if not self.dev:
            return False
        try:
            # Try to get the active configuration. If it fails, device is gone.
            _ = self.dev.get_active_configuration()
            return True
        except usb.core.USBError:
            return False
        except ValueError:
            # This happens if the device is disconnected
            return False
        
    def check_usb_status(self) -> bool:
        """
        Checks if the device is still connected and handles disconnect events.
        Returns True if connected, False otherwise.
        """
        logging.info(f"Checking USB status for {self.desc}...")
        if not self.is_connected():
            logger.warning(f"{self.desc}: USB device disconnected.")
            if self.running:
                self.disconnect()
                if self.async_mode:
                    self.signal_disconnect.emit(self.desc, "composite_usb")
            return False
        return True

    async def monitor_usb_status(self, interval=1):
        """Periodically check for USB device connection."""
        if self.demo_mode:
            logger.debug("Monitoring in demo mode.")
            self.connect()
            return
        while True:
            self.check_usb_status()
            await asyncio.sleep(interval)
