import asyncio
import logging

from omotion.Sensor import MOTIONSensor
from omotion.core import MOTIONUart, MOTIONSignal
from omotion.config import SENSOR_MODULE_PID

logger = logging.getLogger(__name__)

class MOTIONInterface:
    signal_connect: MOTIONSignal = MOTIONSignal()
    signal_disconnect: MOTIONSignal = MOTIONSignal()
    signal_data_received: MOTIONSignal = MOTIONSignal()
    sensor_module: MOTIONSensor = None
    
    def __init__(self, vid: int = 0x0483, sensor_pid: int = SENSOR_MODULE_PID, baudrate: int = 921600, timeout: int = 30, run_async: bool = False, demo_mode: bool = False) -> None:
        
        # Store parameters in instance variables
        self.vid = vid
        self.sensor_pid = sensor_pid
        self.baudrate = baudrate
        self._test_mode = demo_mode
        self.timeout = timeout
        self._async_mode = run_async
        self._sensor_uart = None

        # Create a MOTIONSensor Device instance as part of the interface
        logger.debug("Initializing TX Module of MOTIONInterface with VID: %s, PID: %s, baudrate: %s, timeout: %s", vid, sensor_pid, baudrate, timeout)
        self._sensor_uart = MOTIONUart(vid=vid, pid=sensor_pid, baudrate=baudrate, timeout=timeout, desc="sensor", demo_mode=False, async_mode=run_async)
        self.sensor_module = MOTIONSensor(uart=self._sensor_uart)

        # Connect signals to internal handlers
        if self._async_mode:
            self._sensor_uart.signal_connect.connect(self.signal_connect.emit)
            self._sensor_uart.signal_disconnect.connect(self.signal_disconnect.emit)
            self._sensor_uart.signal_data_received.connect(self.signal_data_received.emit)
            
    async def start_monitoring(self, interval: int = 1) -> None:
        """Start monitoring for USB device connections."""
        try:
            await asyncio.gather(
                self._sensor_uart.monitor_usb_status(interval)
            )

        except Exception as e:
            logger.error("Error starting monitoring: %s", e)
            raise e

    def stop_monitoring(self) -> None:
        """Stop monitoring for USB device connections."""
        try:
            self._sensor_uart.stop_monitoring()
        except Exception as e:
            logger.error("Error stopping monitoring: %s", e)
            raise e


    def is_device_connected(self) -> tuple:
        """
        Check if the device is currently connected.

        Returns:
            tuple: (console_connected, sensor_connected)
        """
        console_connected = False
        sensor_connected = self.sensor_module.is_connected()
        
        return console_connected, sensor_connected

    def __del__(self):
        """Finalizer: clean up if object is garbage collected."""
        print("Exiting MOTIONInterface...")
        # Clean up resources
        self.stop_monitoring()
        if self.sensor_module:
            self.sensor_module.disconnect()
