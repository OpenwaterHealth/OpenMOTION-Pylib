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
from omotion.connection_state import ConnectionState

logger = logging.getLogger(f"{_log_root}.MotionComposite" if _log_root else "MotionComposite")

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
        self.state = ConnectionState.DISCONNECTED

        # Interfaces
        self.comm = CommInterface(dev, 0, desc=f"{desc}-COMM", async_mode=True) # TODO: fix async mode in higher levels
        self.histo = StreamInterface(dev, 1, desc=f"{desc}-HISTO")
        self.imu = StreamInterface(dev, 2, desc=f"{desc}-IMU")

        self.packet_count = 0
        self.read_buffer = bytearray()

        self.stop_event = threading.Event()
        self.pause_event = threading.Event()


    def connect(self):
        self._set_state(ConnectionState.CONNECTING)
        self.dev.set_configuration()
        self.comm.claim()
        self.histo.claim()
        self.imu.claim()

        if self.comm.async_mode:
            self.comm.start_read_thread()

        self.running = True
        self._set_state(ConnectionState.CONNECTED)
        self.signal_connect.emit(self.desc, "composite_usb")
        logger.info(f"{self.desc}: Connected")

    def disconnect(self):
        if self.state == ConnectionState.DISCONNECTED:
            return
        if self.async_mode:
            self.comm.stop_read_thread()
        self.histo.stop_streaming()
        self.imu.stop_streaming()
        
        self.comm.release()
        self.histo.release()
        self.imu.release()

        self.running = False
        self._set_state(ConnectionState.DISCONNECTED)
        usb.util.dispose_resources(self.dev)
        self.signal_disconnect.emit(self.desc, "composite_usb")
        logger.info(f"{self.desc}: Disconnected")
    
    def is_connected(self) -> bool:
        """
        Check if the device is connected.
        """
        return self.state == ConnectionState.CONNECTED
    
    def check_usb_status(self):
        """
        Check if the device is connected.
        """
        return self.state == ConnectionState.CONNECTED

    def _set_state(self, new_state: ConnectionState, reason: str | None = None):
        if self.state == new_state:
            return
        prior = self.state
        self.state = new_state
        if reason:
            logger.info("%s state %s -> %s (%s)", self.desc, prior.name, new_state.name, reason)
        else:
            logger.info("%s state %s -> %s", self.desc, prior.name, new_state.name)