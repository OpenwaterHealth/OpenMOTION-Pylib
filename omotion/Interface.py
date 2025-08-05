import asyncio
import logging

from omotion.Console import MOTIONConsole
from omotion.DualMotionComposite import DualMotionComposite
from omotion.Sensor import MOTIONSensor
from omotion.MotionUart import MOTIONUart
from omotion.MotionComposite import MotionComposite

from omotion.config import CONSOLE_MODULE_PID, SENSOR_MODULE_PID
from omotion.signal_wrapper import SignalWrapper, PYQT_AVAILABLE

logger = logging.getLogger("Interface")
logger.setLevel(logging.INFO)


class MOTIONInterface(SignalWrapper):
    
    sensors: MOTIONSensor = None
    
    def __init__(self, vid: int = 0x0483, sensor_pid: int = SENSOR_MODULE_PID, console_pid: int = CONSOLE_MODULE_PID, baudrate: int = 921600, timeout: int = 30, run_async: bool = False, demo_mode: bool = False) -> None:
        super().__init__()
            
        # Store parameters in instance variables
        self.vid = vid
        self.sensor_pid = sensor_pid
        self.console_pid = console_pid
        self.baudrate = baudrate
        self._test_mode = demo_mode
        self.timeout = timeout
        self._async_mode = run_async
        self._sensor_uart = None
        self._console_uart = None
        self.console_module = None
        self.sensors = None

        # Create a MOTIONConsole Device instance as part of the interface
        logger.debug("Initializing Console Module of MOTIONInterface with VID: %s, PID: %s, baudrate: %s, timeout: %s", vid, console_pid, baudrate, timeout)
        self._console_uart = MOTIONUart(vid=vid, pid=console_pid, baudrate=baudrate, timeout=timeout, desc="console", demo_mode=False, async_mode=run_async)
        self.console_module = MOTIONConsole(uart=self._console_uart)

        # Create a MOTIONSensor Device instance as part of the interface
        logger.debug("Initializing Sensor Module of MOTIONInterface with VID: %s, PID: %s, timeout: %s", vid, sensor_pid, timeout)
        self._dual_composite = DualMotionComposite(vid=vid, pid=sensor_pid, async_mode=run_async)
        self._dual_composite.connect()

        # Wrap them in MOTIONSensor
        self.sensors = {
            "left": MOTIONSensor(uart=self._dual_composite.left),
            "right": MOTIONSensor(uart=self._dual_composite.right)
        }

        # Connect signals to internal handlers
        if PYQT_AVAILABLE:
            if self._console_uart:
                logger.info("Connecting console COMM signals to MOTIONInterface")
                self._console_uart.signal_connect.connect(self.signal_connect.emit)
                self._console_uart.signal_disconnect.connect(self.signal_disconnect.emit)
                self._console_uart.signal_data_received.connect(self.signal_data_received.emit)
            if self._sensor_uart:
                logger.info("Connecting sensor COMM signals to MOTIONInterface")
                self._dual_composite.signal_disconnect.connect(self.signal_connect.emit)
                self._dual_composite.signal_disconnect.disconnect(self.signal_disconnect.emit)
                self._dual_composite.signal_data_received.connect(self.signal_data_received.emit)
            
    async def start_monitoring(self, interval: int = 1) -> None:
        """Start monitoring for USB device connections."""
        try:
            await asyncio.gather(
                self._console_uart.monitor_usb_status(interval),
                self._dual_composite.monitor_usb_status(interval)
            )

        except Exception as e:
            logger.error("Error starting monitoring: %s", e)
            raise e

    def stop_monitoring(self) -> None:
        """Stop monitoring for USB device connections."""
        try:
            self._console_uart.stop_monitoring()
            self._dual_composite.stop_monitoring()
        except Exception as e:
            logger.error("Error stopping monitoring: %s", e)
            raise e


    def is_device_connected(self) -> tuple[bool, bool, bool]:
        """
        Check if the console, left sensor, and right sensor are connected.

        Returns:
            tuple: (console_connected, left_connected, right_connected)
        """
        console_connected = self.console_module.is_connected()

        left_connected = False
        right_connected = False
        if self._dual_composite.left:
            left_connected = self._dual_composite.left.is_connected()
        if self._dual_composite.right:
            right_connected = self._dual_composite.right.is_connected()

        return console_connected, left_connected, right_connected
    
    def get_camera_histogram(
        self,
        camera_id: int,
        test_pattern_id: int = 4,
        auto_upload: bool = True
    ) -> tuple[list[int], list[int]] | None:
        """
        High-level method to get a histogram from a specific camera.

        Args:
            camera_id (int): Camera index (1–8).
            test_pattern_id (int): Test pattern ID to use (if enabled).
            auto_upload (bool): Whether to upload bitstream if needed.

        Returns:
            bytearray | None: Histogram data or None if failed.
        """
        import time

        if not (1 <= camera_id <= 8):
            logger.error("Camera ID must be 1–8.")
            return None

        CAMERA_MASK = 1 << (camera_id - 1)
        TEST_PATTERN_ID = test_pattern_id

        # Get status
        status_map = self.sensors.get_camera_status(CAMERA_MASK)
        if not status_map or camera_id - 1 not in status_map:
            logger.error("Failed to get camera status.")
            return None

        status = status_map[camera_id - 1]
        logger.debug(f"Camera {camera_id} status: 0x{status:02X} → {self.sensors.decode_camera_status(status)}")

        if not status & (1 << 0):  # Not READY
            logger.debug("Camera peripheral not READY.")
            return None

        if not (status & (1 << 1) and status & (1 << 2)):  # Not programmed
            logger.debug("FPGA Configuration Started")
            start_time = time.time()

            if auto_upload:
                if not self.sensors.program_fpga(camera_position=CAMERA_MASK, manual_process=False):
                    logger.error("Failed to enter sram programming mode for camera FPGA.")
                    return None
                
            logger.debug(f"FPGAs programmed | Time: {(time.time() - start_time)*1000:.2f} ms")

        if not (status & (1 << 1) and status & (1 << 3)):  # Not configured
            logger.debug ("Programming camera sensor registers.")
            if not self.sensors.camera_configure_registers(CAMERA_MASK):
                logger.error("Failed to configure default registers for camera FPGA.")
                return None
        
        logger.debug("Setting test pattern...")
        if not self.sensors.camera_configure_test_pattern(CAMERA_MASK, TEST_PATTERN_ID):
            logger.error("Failed to set test pattern.")
            return None

        # Get status
        status_map = self.sensors.get_camera_status(CAMERA_MASK)
        if not status_map or camera_id - 1 not in status_map:
            logger.error("Failed to get camera status.")
            return None

        status = status_map[camera_id - 1]
        logger.debug(f"Camera {camera_id} status: 0x{status:02X} → {self.sensors.decode_camera_status(status)}")

        if not (status & (1 << 0) and status & (1 << 1) and status & (1 << 2)):  # Not ready for histo
            logger.error("Not configured.")
            return None

        # Capture + Get Histogram
        logger.debug("Capturing histogram...")
        if not self.sensors.camera_capture_histogram(CAMERA_MASK):
            logger.error("Capture failed.")
            return None

        logger.debug("Retrieving histogram...")
        histogram = self.sensors.camera_get_histogram(CAMERA_MASK)
        if histogram is None:
            logger.error("Histogram retrieval failed.")
            return None

        logger.debug("Histogram frame received successfully.")
        histogram = histogram[:4096]
        return self.bytes_to_integers(histogram)

    def __del__(self):
        """Finalizer: clean up if object is garbage collected."""
        logger.debug("Exiting MOTIONInterface...")
        # Clean up resources
        self.stop_monitoring()
        if self.console_module:
            self.console_module.disconnect()
        if self._dual_composite:
            self._dual_composite.disconnect()
            
    @staticmethod
    def bytes_to_integers(byte_array):
        # Check that the byte array is exactly 4096 bytes
        if len(byte_array) != 4096:
            raise ValueError("Input byte array must be exactly 4096 bytes.")
        # Initialize an empty list to store the converted integers
        integers = []
        hidden_figures = []
        # Iterate over the byte array in chunks of 4 bytes
        for i in range(0, len(byte_array), 4):
            bytes = byte_array[i:i+4]
            # Unpack each 4-byte chunk as a single integer (big-endian)
#            integer = struct.unpack_from('<I', byte_array, i)[0]
            # if(bytes[0] + bytes[1] + bytes[2] + bytes[3] > 0):
            #     print(str(i) + " " + str(bytes[0:3]))
            hidden_figures.append(bytes[3])
            integers.append(int.from_bytes(bytes[0:3],byteorder='little'))
        return (integers, hidden_figures)

    @staticmethod
    def get_sdk_version() -> str:
        return "1.1.0"
        

    @staticmethod
    def acquire_motion_interface():
        """
        Create a MOTIONInterface instance and check if devices are connected.
        Returns:
            tuple: (interface, console_connected, left_sensor, right_sensor)
        """
        interface = MOTIONInterface()
        console_connected, left_sensor, right_sensor = interface.is_device_connected()
        return interface, console_connected, left_sensor, right_sensor