import logging
import usb.core
import usb.util

from omotion.MotionComposite import MotionComposite

logger = logging.getLogger("DualMotionComposite")
logger.setLevel(logging.INFO)


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

    def connect(self):
        """Scan USB for matching VID/PID and connect left/right sensors if present."""
        devices = list(usb.core.find(find_all=True, idVendor=self.vid, idProduct=self.pid))
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
                    if not self.left:
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
                    if not self.right:
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

    def disconnect(self):
        """Disconnect any connected sensors."""
        if self.left:
            logger.info("Disconnecting LEFT sensor")
            self.left.disconnect()
            self.left = None
            self._left_dev = None
            if self.async_mode:
                self.signal_disconnect.emit("LEFT", "composite_usb")

        if self.right:
            logger.info("Disconnecting RIGHT sensor")
            self.right.disconnect()
            self.right = None
            self._right_dev = None
            if self.async_mode:
                self.signal_disconnect.emit("RIGHT", "composite_usb")

    def _check_and_update_connections(self):
        pass


    def monitor_usb_status(self, interval: int = 1) -> None:
        """Periodically check USB status and connect/disconnect sensors as needed."""
        import time
        import threading

        def monitor_loop():
            while True:
                try:
                    self._check_and_update_connections()
                except Exception as e:
                    logger.error(f"Error during USB monitoring: {e}")
                time.sleep(interval)

        self._monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        self._monitor_thread.start()

    def stop_monitoring(self) -> None:
        """Stop USB monitoring thread."""
        if hasattr(self, '_monitor_thread') and self._monitor_thread.is_alive():
            self._stop_event.set()
            self._monitor_thread.join()

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