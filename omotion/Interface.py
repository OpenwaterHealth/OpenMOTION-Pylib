import asyncio
import logging
import platform
import socket
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

        # If the console was already connected at construction time, start its poller.
        if self.console_module and self.console_module.is_connected():
            logger.info("Console already connected at init – starting telemetry poller")
            self.console_module.telemetry.start()

        # Connect console UART signals to interface (works with PyQt or MOTIONSignal shim)
        if self._console_uart:
            logger.info("Connecting console COMM signals to MOTIONInterface")
            self._console_uart.signal_connect.connect(self._on_console_connect)
            self._console_uart.signal_disconnect.connect(self._on_console_disconnect)
            self._console_uart.signal_data_received.connect(self.signal_data_received)

        # Connect DualMotionComposite signals to interface (works with PyQt or MOTIONSignal shim)
        if self._dual_composite:
            logger.info("Connecting dual composite signals to MOTIONInterface")
            self._dual_composite.signal_connect.connect(self._on_sensor_connect)
            self._dual_composite.signal_disconnect.connect(self._on_sensor_disconnect)
            self._dual_composite.signal_data_received.connect(self.signal_data_received)

    def log_system_info(self) -> None:
        """Log host system information to the SDK logger."""
        try:
            logger.info("--- System Information ---")
            logger.info("Hostname:    %s", socket.gethostname())
            logger.info("Platform:    %s", platform.platform())
            logger.info("System:      %s %s", platform.system(), platform.release())
            logger.info("Version:     %s", platform.version())
            logger.info("Arch:        %s", platform.machine())
            logger.info("Processor:   %s", platform.processor())
            logger.info("Python:      %s (%s)", platform.python_version(),
                        platform.python_implementation())
            logger.info("SDK version: %s", _SDK_VERSION)

            if platform.system() == "Windows":
                try:
                    import ctypes

                    class _MEMSTATUSEX(ctypes.Structure):
                        _fields_ = [
                            ("dwLength",                ctypes.c_ulong),
                            ("dwMemoryLoad",            ctypes.c_ulong),
                            ("ullTotalPhys",            ctypes.c_ulonglong),
                            ("ullAvailPhys",            ctypes.c_ulonglong),
                            ("ullTotalPageFile",        ctypes.c_ulonglong),
                            ("ullAvailPageFile",        ctypes.c_ulonglong),
                            ("ullTotalVirtual",         ctypes.c_ulonglong),
                            ("ullAvailVirtual",         ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                        ]

                    mem = _MEMSTATUSEX()
                    mem.dwLength = ctypes.sizeof(_MEMSTATUSEX)
                    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
                    logger.info("RAM:         %.2f GB", mem.ullTotalPhys / (1024 ** 3))
                except Exception:
                    pass
        except Exception as e:
            logger.warning("Failed to log system information: %s", e)

    def log_console_info(self) -> None:
        """Log console device info via MOTIONConsole.log_device_info()."""
        if self.console_module and self.console_module.is_connected():
            self.console_module.log_device_info()

    def log_sensor_info(self, side: str) -> None:
        """Log sensor device info via MOTIONSensor.log_device_info() for *side*."""
        sensor = self.sensors.get(side) if self.sensors else None
        if sensor and sensor.is_connected():
            sensor.log_device_info()

    def _on_console_connect(self, device_id: str, connection_type: str) -> None:
        """Handle console connection: start the telemetry poller and forward the signal."""
        logger.info("Console connected (%s) – starting telemetry poller", device_id)
        if self.console_module:
            self.console_module.telemetry.start()
        self.signal_connect.emit(device_id, connection_type)

    def _on_console_disconnect(self, device_id: str, connection_type: str) -> None:
        """Handle console disconnection: stop the telemetry poller and forward the signal."""
        logger.info("Console disconnected (%s) – stopping telemetry poller", device_id)
        if self.console_module:
            self.console_module.telemetry.stop()
        self.signal_disconnect.emit(device_id, connection_type)

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

    def run_contact_quality_check(
        self,
        duration_s: float = 3.0,
        subject_id: str = "_contact_quality_check",
        data_dir: str | None = None,
    ) -> "ContactQualityResult":
        """Run a brief acquisition and return contact-quality warnings.

        Always uses camera mask 0xFF on both sensor modules. Histograms are
        consumed only by the contact-quality monitor; no CSV files are written
        and no live-data callbacks are fired. Blocks until the scan completes
        or fails.
        """
        import threading
        import tempfile
        from omotion.ContactQuality import ContactQualityResult, ContactQualityWarning
        from omotion.ScanWorkflow import ConfigureRequest, ConfigureResult, ScanRequest

        warnings: list[ContactQualityWarning] = []
        warnings_lock = threading.Lock()

        def _on_warning(w: ContactQualityWarning) -> None:
            with warnings_lock:
                warnings.append(w)

        # Always configure cameras before the scan. The play-button path does
        # this via startConfigureCameraSensors; skipping it here (as quick-check
        # did previously) produced flaky enable_camera timeouts when the
        # cameras had not been freshly programmed.
        config_request = ConfigureRequest(
            left_camera_mask=0xFF,
            right_camera_mask=0xFF,
            power_off_unused_cameras=False,
        )

        config_result_holder: dict[str, ConfigureResult] = {}
        config_done_evt = threading.Event()

        def _on_config_complete(res: ConfigureResult) -> None:
            config_result_holder["result"] = res
            config_done_evt.set()

        started = self.start_configure_camera_sensors(
            config_request, on_complete_fn=_on_config_complete
        )
        if not started:
            return ContactQualityResult(
                ok=False,
                warnings=[],
                error="Camera configuration already in progress",
            )

        # Block until the configure worker signals completion. ScanWorkflow
        # exposes no public wait(); joining _config_thread is acceptable
        # within the same package, and we also wait on the event in case the
        # worker clears _config_thread before we observe it.
        config_done_evt.wait()
        if self.scan_workflow is not None and self.scan_workflow._config_thread is not None:
            self.scan_workflow._config_thread.join()

        config_result = config_result_holder.get("result")
        if config_result is None or not config_result.ok:
            err = (config_result.error if config_result else "") or "unknown"
            return ContactQualityResult(
                ok=False,
                warnings=[],
                error=f"Camera configuration failed: {err}",
            )

        # Scan teardown overhead + camera enable + warmup-discard means
        # anything under ~3 s is unlikely to produce usable data. Enforce a
        # floor so callers requesting very short durations still get a
        # meaningful acquisition window.
        duration_sec = max(3, int(round(duration_s)))

        request = ScanRequest(
            subject_id=subject_id,
            duration_sec=duration_sec,
            left_camera_mask=0xFF,
            right_camera_mask=0xFF,
            data_dir=data_dir or tempfile.gettempdir(),
            disable_laser=False,
            write_raw_csv=False,
            write_corrected_csv=False,
            write_telemetry_csv=False,
        )

        ok = self.start_scan(request, contact_quality_callback=_on_warning)
        if not ok:
            return ContactQualityResult(
                ok=False,
                warnings=[],
                error="Failed to start scan",
            )

        # Block until the scan worker completes. ScanWorkflow exposes no
        # public wait()/join(); reading _thread is acceptable within the
        # same package.
        if self.scan_workflow is not None and self.scan_workflow._thread is not None:
            self.scan_workflow._thread.join()

        # Inspect per-side USB chunk counts to detect silent acquisition
        # failures (e.g. enable_camera timeout, FPGA not programmed, cable
        # unplugged). Mirrors the pattern used by ScanWorkflow._worker around
        # the per-side summary log.
        per_side_chunks: dict[str, int] = {}
        for side, sensor in (self.sensors or {}).items():
            if sensor is None or not sensor.is_connected():
                continue
            try:
                per_side_chunks[side] = int(sensor.uart.histo.packets_received)
            except Exception:
                # If histo interface isn't available, skip — we can't diagnose.
                continue

        with warnings_lock:
            warnings_snapshot = list(warnings)

        if per_side_chunks:
            zero_sides = [s for s, n in per_side_chunks.items() if n == 0]
            if len(zero_sides) == len(per_side_chunks):
                # All connected sides got nothing — hard failure.
                return ContactQualityResult(
                    ok=False,
                    warnings=warnings_snapshot,
                    error="No data received from sensors — check cabling and FPGA programming",
                )
            if zero_sides:
                # Any connected side with zero data is a failure — the scan
                # did not deliver what the caller configured.
                labels = [s.capitalize() for s in zero_sides]
                if len(labels) == 1:
                    who = labels[0]
                else:
                    who = " and ".join(labels)
                return ContactQualityResult(
                    ok=False,
                    warnings=warnings_snapshot,
                    error=f"{who} sensor received no data — check cabling",
                )

        return ContactQualityResult(ok=True, warnings=warnings_snapshot)

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

    def disconnect(self) -> None:
        """Disconnect all devices and clean up resources.

        Stops the telemetry poller, disconnects the console UART, and
        disconnects all sensor composites.  Each step is wrapped individually
        so a failure at one does not prevent the remaining cleanup.
        """
        if self.console_module and hasattr(self.console_module, "telemetry"):
            try:
                self.console_module.telemetry.stop()
            except Exception as e:
                logger.warning("Error stopping telemetry poller: %s", e)

        if self.console_module:
            try:
                self.console_module.disconnect()
            except Exception as e:
                logger.warning("Error disconnecting console: %s", e)

        # DualMotionComposite.disconnect() only acts when given a specific target;
        # calling it with no target does nothing.  Disconnect each side explicitly
        # so their read threads are stopped before the process exits.
        if self._dual_composite:
            for side in ("left", "right"):
                try:
                    self._dual_composite.disconnect(target=side)
                except Exception as e:
                    logger.warning("Error disconnecting %s sensor: %s", side, e)

    def __del__(self):
        try:
            self.disconnect()
        except Exception as e:
            logger.debug(f"Destructor skipped due to: {e}")

    def log_system_info(self) -> None:
        """Log host system and SDK version information."""
        import sys
        import platform

        logger.info("SDK version: %s", _SDK_VERSION)
        logger.info(
            "Python %s (%s)", platform.python_version(), sys.executable
        )
        logger.info(
            "Platform: %s %s (%s)",
            platform.system(),
            platform.release(),
            platform.machine(),
        )

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
