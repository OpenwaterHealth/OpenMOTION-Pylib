import asyncio
import logging
from typing import Any, Iterable
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
            tasks = []
            if self._console_uart:
                coro = self._console_uart.monitor_usb_status(interval)
                if asyncio.iscoroutine(coro):
                    tasks.append(coro)

            if self._dual_composite:
                if self._dual_composite.left:
                    coro = self._dual_composite.left.monitor_usb_status(interval)
                    if asyncio.iscoroutine(coro):
                        tasks.append(coro)
                if self._dual_composite.right:
                    coro = self._dual_composite.right.monitor_usb_status(interval)
                    if asyncio.iscoroutine(coro):
                        tasks.append(coro)

            if tasks:
                await asyncio.gather(*tasks)
                
        except Exception as e:
            logger.error("Error starting monitoring: %s", e)
            raise

    def stop_monitoring(self) -> None:
        """Stop monitoring for USB device connections."""
        try:
            if self._console_uart and hasattr(self._console_uart, "stop_monitoring"):
                self._console_uart.stop_monitoring()

            if self._dual_composite:
                if hasattr(self._dual_composite, "stop_monitoring"):
                    # If DualMotionComposite has a single stop method for both
                    self._dual_composite.stop_monitoring()
                else:
                    # Stop individually if needed
                    if self._dual_composite.left and hasattr(self._dual_composite.left, "stop_monitoring"):
                        self._dual_composite.left.stop_monitoring()
                    if self._dual_composite.right and hasattr(self._dual_composite.right, "stop_monitoring"):
                        self._dual_composite.right.stop_monitoring()
                        
        except Exception as e:
            logger.error("Error stopping monitoring: %s", e)
            raise
        
    def run_on_sensors(
        self,
        func_name: str,
        *args,
        target: str | Iterable[str] | None = None,
        include_disconnected: bool = True,
        **kwargs
    ) -> dict[str, Any]:
        """
        Run a MOTIONSensor method on selected sensors and return results.

        Args:
            func_name: Name of the MOTIONSensor method to call.
            *args: Positional args to pass to the method.
            target: Which sensor(s) to target:
                - None (default): run on all sensors in self.sensors
                - "left" or "right": run only on that sensor
                - "all" or "*": same as None
                - Iterable[str]: e.g. ["left", "right"]
            include_disconnected: If True, include keys for selected sensors
                that are not connected with value None; if False, skip them.
            **kwargs: Keyword args to pass to the method.

        Returns:
            dict[str, Any]: {sensor_name: return_value or None}
        """
        # Normalize target(s)
        if target is None or (isinstance(target, str) and target.lower() in ("all", "*")):
            selected_names = set(self.sensors.keys())
        elif isinstance(target, str):
            selected_names = {target.lower()}
        else:
            selected_names = {str(t).lower() for t in target}

        results: dict[str, Any] = {}

        # Validate requested targets exist
        unknown = selected_names - {n.lower() for n in self.sensors.keys()}
        if unknown:
            logger.warning(f"Unknown sensor target(s): {sorted(unknown)}")

        # Iterate over requested sensors only
        for name, sensor in self.sensors.items():
            if name.lower() not in selected_names:
                continue

            if sensor and sensor.is_connected():
                method = getattr(sensor, func_name, None)
                if callable(method):
                    try:
                        results[name] = method(*args, **kwargs)
                    except Exception as e:
                        logger.error(f"Error running {func_name} on {name}: {e}")
                        results[name] = None
                else:
                    logger.error(f"{func_name} is not a valid MOTIONSensor method")
                    results[name] = None
            else:
                if include_disconnected:
                    logger.warning(f"{name} sensor not connected.")
                    results[name] = None
                # else skip disconnected sensor entirely

        return results
    
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
        sensor_side: str,         # "left" or "right"
        camera_id: int,
        test_pattern_id: int = 4,
        auto_upload: bool = True
    ) -> tuple[list[int], list[int]] | None:
        """
        High-level method to get a histogram from a specific camera
        on a specific sensor module ("left" or "right").
        """

        import time

        if sensor_side not in ("left", "right"):
            logger.error("sensor_side must be 'left' or 'right'.")
            return None

        if not (1 <= camera_id <= 8):
            logger.error("Camera ID must be 1–8.")
            return None

        sensor = self.sensors[sensor_side]
        camera_mask = 1 << (camera_id - 1)
        test_pattern = test_pattern_id

        # Step 1: Get status
        status_map = sensor.get_camera_status(camera_mask)
        print(f"[{sensor_side.capitalize()}] Camera {camera_id} Status: {status_map}")
        if not status_map or camera_id - 1 not in status_map:
            logger.error(f"[{sensor_side.capitalize()}] Failed to get camera status.")
            return None

        status = status_map[camera_id - 1]
        logger.debug(f"[{sensor_side.capitalize()}] Camera {camera_id} status: 0x{status:02X} → {sensor.decode_camera_status(status)}")

        if not status & (1 << 0):  # Not READY
            logger.debug(f"[{sensor_side.capitalize()}] Camera peripheral not READY.")
            return None

        # Step 2: Program FPGA if needed
        if not (status & (1 << 1) and status & (1 << 2)):
            logger.debug(f"[{sensor_side.capitalize()}] FPGA Configuration Started")
            start_time = time.time()
            if auto_upload:
                if not sensor.program_fpga(camera_position=camera_mask, manual_process=False):
                    logger.error(f"[{sensor_side.capitalize()}] Failed to program FPGA.")
                    return None
            logger.debug(f"[{sensor_side.capitalize()}] FPGAs programmed | Time: {(time.time() - start_time)*1000:.2f} ms")

        # Step 3: Configure registers if needed
        if not (status & (1 << 1) and status & (1 << 3)):
            logger.debug(f"[{sensor_side.capitalize()}] Programming camera sensor registers.")
            if not sensor.camera_configure_registers(camera_mask):
                logger.error(f"[{sensor_side.capitalize()}] Failed to configure registers.")
                return None

        # Step 4: Set test pattern
        logger.debug(f"[{sensor_side.capitalize()}] Setting test pattern...")
        if not sensor.camera_configure_test_pattern(camera_mask, test_pattern):
            logger.error(f"[{sensor_side.capitalize()}] Failed to set test pattern.")
            return None

        # Step 5: Verify ready for histogram
        status_map = sensor.get_camera_status(camera_mask)
        if not status_map or camera_id - 1 not in status_map:
            logger.error(f"[{sensor_side.capitalize()}] Failed to get camera status.")
            return None

        status = status_map[camera_id - 1]
        logger.debug(f"[{sensor_side.capitalize()}] Camera {camera_id} status: 0x{status:02X} → {sensor.decode_camera_status(status)}")
        if not (status & (1 << 0) and status & (1 << 1) and status & (1 << 2)):
            logger.error(f"[{sensor_side.capitalize()}] Not configured for histogram.")
            return None

        # Step 6: Capture histogram
        logger.debug(f"[{sensor_side.capitalize()}] Capturing histogram...")
        if not sensor.camera_capture_histogram(camera_mask):
            logger.error(f"[{sensor_side.capitalize()}] Capture failed.")
            return None

        # Step 7: Retrieve histogram
        logger.debug(f"[{sensor_side.capitalize()}] Retrieving histogram...")
        histogram = sensor.camera_get_histogram(camera_mask)
        if histogram is None:
            logger.error(f"[{sensor_side.capitalize()}] Histogram retrieval failed.")
            return None

        logger.debug(f"[{sensor_side.capitalize()}] Histogram frame received successfully.")
        histogram = histogram[:4096]
        return self.bytes_to_integers(histogram)

    def __del__(self):
        try:
            self.stop_monitoring()
            if self.console_module:
                self.console_module.disconnect()
            if self._dual_composite:
                self._dual_composite.disconnect()
        except RuntimeError as e:
            logger.debug(f"Destructor skipped due to RuntimeError: {e}")
        except Exception as e:
            logger.debug(f"Destructor skipped due to: {e}")

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
        return "1.3.0"

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