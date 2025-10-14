# MOTIONInterface (Interface.py)

**Purpose:** High-level façade that wires up one Console and two Sensor endpoints (“left” and “right”), exposes connection/monitoring helpers, and provides a convenience API for per-sensor camera workflows (e.g., histogram capture).  

## Constructor

`MOTIONInterface(vid=0x0483, sensor_pid=SENSOR_MODULE_PID, console_pid=CONSOLE_MODULE_PID, baudrate=921600, timeout=30, run_async=False, demo_mode=False)`
Initializes console UART + dual sensor composite, then wraps each side in `MOTIONSensor`. Emits Qt signals when PyQt is available.   

### Attributes

* `console_module: MOTIONConsole` – console device wrapper. 
* `sensors: dict[str, MOTIONSensor]` – `{"left": ..., "right": ...}`. 

## Monitoring

* `async start_monitoring(interval: int = 1) -> None` – Starts USB/device status monitors for console and each sensor side. Awaitable. 
* `stop_monitoring() -> None` – Stops all monitors safely. 

## Connectivity

* `is_device_connected() -> tuple[bool, bool, bool]` – `(console_connected, left_connected, right_connected)`. 

## Sensor fan-out helper

* `run_on_sensors(func_name: str, *args, target: str|Iterable[str]|None=None, include_disconnected: bool=True, **kwargs) -> dict[str, Any]` – Calls a named `MOTIONSensor` method on one/both sides, returns a dict of results; logs and continues on errors.  

## Camera workflow (high-level)

* `get_camera_histogram(sensor_side: str, camera_id: int, test_pattern_id: int = 4, auto_upload: bool = True) -> tuple[list[int], list[int]] | None`
  Orchestrates: status check → (optional) FPGA program → sensor register config → set test pattern → capture → read histogram → parse into `(values, hidden_flags)`. Returns `None` on any step failure.    

## Utilities

* `@staticmethod bytes_to_integers(byte_array: bytes) -> tuple[list[int], list[int]]` – Accepts exactly 4096 bytes; parses each 4-byte group into a 24-bit little-endian integer + collects the fourth byte as a “hidden figure.” 
* `@staticmethod get_sdk_version() -> str` – Returns SDK version string. 
* `@staticmethod acquire_motion_interface() -> tuple["MOTIONInterface", bool, bool, bool]` – Convenience creator + connectivity check. 

## Cleanup

* `__del__` – Stops monitoring, disconnects console and dual composite defensively. 

