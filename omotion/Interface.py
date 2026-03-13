import asyncio
import logging
from typing import Any, Iterable
from omotion.Console import MOTIONConsole
from omotion.DualMotionComposite import DualMotionComposite
from omotion.ScanWorkflow import ScanWorkflow
from omotion.Sensor import MOTIONSensor
from omotion.MotionUart import MOTIONUart

from omotion.config import CONSOLE_MODULE_PID, SENSOR_MODULE_PID
from omotion.signal_wrapper import SignalWrapper
from omotion import __version__ as _SDK_VERSION, _log_root

logger = logging.getLogger(f"{_log_root}.Interface" if _log_root else "Interface")


class MOTIONInterface(SignalWrapper):
    sensors: dict[str, MOTIONSensor | None] = None

    def __init__(
        self,
        vid: int = 0x0483,
        sensor_pid: int = SENSOR_MODULE_PID,
        console_pid: int = CONSOLE_MODULE_PID,
        baudrate: int = 921600,
        timeout: int = 30,
        run_async: bool = False,
        demo_mode: bool = False,
    ) -> None:
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
        self.scan_workflow = None

        # Create a MOTIONConsole Device instance as part of the interface
        logger.debug(
            "Initializing Console Module of MOTIONInterface with VID: %s, PID: %s, baudrate: %s, timeout: %s",
            vid,
            console_pid,
            baudrate,
            timeout,
        )
        self._console_uart = MOTIONUart(
            vid=vid,
            pid=console_pid,
            baudrate=baudrate,
            timeout=timeout,
            desc="console",
            demo_mode=False,
            async_mode=run_async,
        )
        self.console_module = MOTIONConsole(uart=self._console_uart)

        # Create a MOTIONSensor Device instance as part of the interface
        logger.debug(
            "Initializing Sensor Module of MOTIONInterface with VID: %s, PID: %s, timeout: %s",
            vid,
            sensor_pid,
            timeout,
        )
        self._dual_composite = DualMotionComposite(
            vid=vid, pid=sensor_pid, async_mode=run_async
        )

        # Initialize sensors dict - will be populated dynamically when devices connect
        self.sensors = {"left": None, "right": None}

        # Initialize any already connected devices
        self._dual_composite.check_usb_status()
        self._initialize_sensors()
        self.scan_workflow = ScanWorkflow(self)

        # Connect console UART signals to interface (works with PyQt or MOTIONSignal shim)
        if self._console_uart:
            logger.info("Connecting console COMM signals to MOTIONInterface")
            self._console_uart.signal_connect.connect(self.signal_connect)
            self._console_uart.signal_disconnect.connect(self.signal_disconnect)
            self._console_uart.signal_data_received.connect(self.signal_data_received)

        # Connect DualMotionComposite signals to interface (works with PyQt or MOTIONSignal shim)
        if self._dual_composite:
            logger.info("Connecting dual composite signals to MOTIONInterface")
            self._dual_composite.signal_connect.connect(self._on_sensor_connect)
            self._dual_composite.signal_disconnect.connect(self._on_sensor_disconnect)
            self._dual_composite.signal_data_received.connect(self.signal_data_received)

    def _initialize_sensors(self):
        """Initialize MOTIONSensor instances for any currently connected MotionComposite devices."""
        if self._dual_composite.left:
            logger.info("Initializing left sensor from existing connection")
            self.sensors["left"] = MOTIONSensor(uart=self._dual_composite.left)

        if self._dual_composite.right:
            logger.info("Initializing right sensor from existing connection")
            self.sensors["right"] = MOTIONSensor(uart=self._dual_composite.right)

    def _on_sensor_connect(self, sensor_id: str, connection_type: str):
        """Handle sensor connection signal from DualMotionComposite."""
        logger.info(f"Sensor connect signal received: {sensor_id}")

        if sensor_id == "SENSOR_LEFT" and self._dual_composite.left:
            if self.sensors["left"] is None:
                logger.info("Initializing new LEFT sensor")
                self.sensors["left"] = MOTIONSensor(uart=self._dual_composite.left)

        elif sensor_id == "SENSOR_RIGHT" and self._dual_composite.right:
            if self.sensors["right"] is None:
                logger.info("Initializing new RIGHT sensor")
                self.sensors["right"] = MOTIONSensor(uart=self._dual_composite.right)

        # Forward the signal to any external listeners
        self.signal_connect.emit(sensor_id, connection_type)

    def _on_sensor_disconnect(self, sensor_id: str, connection_type: str):
        """Handle sensor disconnection signal from DualMotionComposite."""
        logger.info(f"Sensor disconnect signal received: {sensor_id}")

        if sensor_id == "SENSOR_LEFT":
            logger.info("Clearing LEFT sensor")
            self.sensors["left"] = None

        elif sensor_id == "SENSOR_RIGHT":
            logger.info("Clearing RIGHT sensor")
            self.sensors["right"] = None

        # Forward the signal to any external listeners
        self.signal_disconnect.emit(sensor_id, connection_type)

    async def start_monitoring(self, interval: int = 1) -> None:
        """Start monitoring for USB device connections."""
        try:
            tasks = []
            if self._console_uart:
                coro = self._console_uart.monitor_usb_status(interval)
                if asyncio.iscoroutine(coro):
                    tasks.append(coro)

            if self._dual_composite:
                coro = self._dual_composite.monitor_usb_status(interval)
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
                    if self._dual_composite.left and hasattr(
                        self._dual_composite.left, "stop_monitoring"
                    ):
                        self._dual_composite.left.stop_monitoring()
                    if self._dual_composite.right and hasattr(
                        self._dual_composite.right, "stop_monitoring"
                    ):
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
        **kwargs,
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
        if target is None or (
            isinstance(target, str) and target.lower() in ("all", "*")
        ):
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

    def start_scan(self, request, **kwargs) -> bool:
        if not self.scan_workflow:
            self.scan_workflow = ScanWorkflow(self)
        return self.scan_workflow.start_scan(request, **kwargs)

    def cancel_scan(self, **kwargs) -> None:
        if self.scan_workflow:
            self.scan_workflow.cancel_scan(**kwargs)

    def get_single_histogram(
        self,
        side: str,
        camera_id: int,
        test_pattern_id: int = 4,
        auto_upload: bool = True,
    ):
        if not self.scan_workflow:
            self.scan_workflow = ScanWorkflow(self)
        return self.scan_workflow.get_single_histogram(
            side=side,
            camera_id=camera_id,
            test_pattern_id=test_pattern_id,
            auto_upload=auto_upload,
        )

    def start_configure_camera_sensors(self, request, **kwargs) -> bool:
        if not self.scan_workflow:
            self.scan_workflow = ScanWorkflow(self)
        return self.scan_workflow.start_configure_camera_sensors(request, **kwargs)

    def cancel_configure_camera_sensors(self, **kwargs) -> None:
        if self.scan_workflow:
            self.scan_workflow.cancel_configure_camera_sensors(**kwargs)

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
    def get_sdk_version() -> str:
        return _SDK_VERSION

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
