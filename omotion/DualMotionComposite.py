import logging
import usb.core
import usb.util
import asyncio
import time

from omotion.usb_backend import get_libusb1_backend
from omotion.MotionComposite import MotionComposite

logger = logging.getLogger("DualMotionComposite")
logger.setLevel(logging.INFO)

backend = get_libusb1_backend()

# ===============================
# Left/Right Device Manager
# ===============================
class DualMotionComposite:
    def __init__(self, vid, pid, async_mode=False):
        super().__init__()
        self.vid = vid
        self.pid = pid
        self.async_mode = async_mode
        self.left = None
        self.right = None
        self._left_dev = None
        self._right_dev = None
        self.monitoring_task = None
        self.demo_mode = False

    def connect(self,target = None):
        """Scan USB for matching VID/PID and connect left/right sensors if present."""
        devices = list(usb.core.find(find_all=True, idVendor=self.vid, idProduct=self.pid, backend=backend))
        if not devices:
            logger.info(f"No sensor devices found (VID=0x{self.vid:X}, PID=0x{self.pid:X})")
            return

        for dev in devices:
            try:
                port_numbers = getattr(dev, "port_numbers", [])
                if not port_numbers:
                    logger.warning(f"Device at bus {dev.bus} addr {dev.address} has no port_numbers; skipping")
                    continue

                # Left sensor (port ends with 2)
                if port_numbers[-1] == 2:
                    if not self.left and target == "left":
                        logger.info(f"Connecting LEFT sensor (bus {dev.bus}, ports {port_numbers})")
                        self._left_dev = dev
                        self.left = MotionComposite(dev, desc="LEFT", async_mode=self.async_mode)
                        if self.async_mode:
                            self.left.signal_connect.connect(self.signal_connect.emit)
                            self.left.signal_disconnect.connect(self.signal_disconnect.emit)
                            self.left.signal_data_received.connect(self.signal_data_received.emit)

                        self.left.connect()
                        if self.async_mode:
                            self.signal_connect.emit("LEFT", "composite_usb")
                            

                # Right sensor (port ends with 3)
                elif port_numbers[-1] == 3:
                    if not self.right and target == "right":
                        logger.info(f"Connecting RIGHT sensor (bus {dev.bus}, ports {port_numbers})")
                        self._right_dev = dev
                        self.right = MotionComposite(dev, desc="RIGHT", async_mode=self.async_mode)
                        if self.async_mode:
                            self.right.signal_connect.connect(self.signal_connect.emit)
                            self.right.signal_disconnect.connect(self.signal_disconnect.emit)
                            self.right.signal_data_received.connect(self.signal_data_received.emit)

                        self.right.connect()
                        if self.async_mode:
                            self.signal_connect.emit("RIGHT", "composite_usb")

            except Exception as e:
                logger.error(f"Error connecting to sensor device: {e}")

    def disconnect(self,target = None):
        """Disconnect any connected sensors."""
        if self.left and target == "left":
            logger.info("Disconnecting LEFT sensor")
            self.left.disconnect()
            self.left = None
            self._left_dev = None
            if self.async_mode:
                self.signal_disconnect.emit("LEFT", "composite_usb")

        if self.right and target == "right":
            logger.info("Disconnecting RIGHT sensor")
            self.right.disconnect()
            self.right = None
            self._right_dev = None
            if self.async_mode:
                self.signal_disconnect.emit("RIGHT", "composite_usb")

    def check_usb_status(self):
        """Check if the USB device is connected or disconnected."""
        print("checking dual composite usb status")
        devices = list(usb.core.find(find_all=True, idVendor=self.vid, idProduct=self.pid, backend=backend))

        for dev in devices:
            # print(f"dev: {dev}")
            try:
                port_numbers = getattr(dev, "port_numbers", [])
                # print(f"port_numbers: {port_numbers}")
                if not port_numbers:
                    logger.warning(f"Device at bus {dev.bus} addr {dev.address} has no port_numbers; skipping")
                    continue
                # Left sensor (port ends with 2)
                if port_numbers[-1] == 2:
                    if not self.left:
                        print("left is not connected, connecting")
                        self.connect(target = "left")                
                    print("left is connected")                          
                # Right sensor (port ends with 3)
                if port_numbers[-1] == 3:
                    if not self.right:
                        print("right is not connected, connecting")
                        self.connect(target = "right")
                    print("right is connected")
            except Exception as e:
                logger.error(f"Error connecting to sensor device: {e}")
        print(f"self.left: {self.left}")
        print(f"self.right: {self.right}")
        

        try:
            #if there is not a device with port_numbers[-1] == 2, disconnect the left sensor
            if not any(getattr(dev, "port_numbers", [])[-1] == 2 for dev in devices) and self.left:
                print("no left device found, disconnecting left")
                self.disconnect(target = "left")
            #if there is not a device with port_numbers[-1] == 3, disconnect the right sensor
            if not any(getattr(dev, "port_numbers", [])[-1] == 3 for dev in devices) and self.right:
                print("no right device found, disconnecting right")
                self.disconnect(target = "right")
        except Exception as e:
                logger.error(f"Error disconnecting from sensor device")
            

    async def monitor_usb_status(self, interval=1):
        """Periodically check for USB device connection."""
        if self.demo_mode:
            logger.debug("Monitoring in demo mode.")
            self.connect()
            return
        while True:
            self.check_usb_status()
            await asyncio.sleep(interval)

    def start_monitoring(self, interval=1):
        """Start the periodic USB device connection check."""
        if self.demo_mode:
            logger.debug("Monitoring in demo mode.")
            return
        if not self.monitoring_task and self.asyncMode:
            self.monitoring_task = asyncio.create_task(self.monitor_usb_status(interval))

    def stop_monitoring(self):
        """Stop the periodic USB device connection check."""
        if self.demo_mode:
            logger.info("Monitoring in demo mode.")
            return
        if self.monitoring_task:
            self.monitoring_task.cancel()
            self.monitoring_task = None

    def has_sensors(self) -> bool:
        """Return True if at least one sensor is connected."""
        return bool(self.left or self.right)

    def sensor_count(self) -> int:
        """Return the number of connected sensors."""
        return int(self.left is not None) + int(self.right is not None)

    def get_sensor(self, side: str):
        """Return the MotionComposite for a given side ('left' or 'right')."""
        side = side.lower()
        if side == "left":
            return self.left
        elif side == "right":
            return self.right
        else:
            raise ValueError("Invalid side; must be 'left' or 'right'")