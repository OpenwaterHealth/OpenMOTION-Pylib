# MOTIONSensor (Sensor.py)

**Purpose:** Per-sensor endpoint (left/right) for IMU, camera/FPGA, power, and file-based bitstream operations. Wraps a `MotionComposite` side. 

## Constructor

`MOTIONSensor(uart: MotionComposite)` – Stores composite, optionally checks link in sync mode. 

## Connection & basics

* `is_connected() -> bool` – True when underlying composite is connected. 
* `ping() -> bool` – Ping over command channel; maps error codes to `False`. 
* `get_version() -> str` – `'vX.Y.Z'` (or `'v0.0.0'` on invalid). Demo `'v0.1.1'`. 
* `echo(echo_data: bytes|bytearray|None) -> tuple[bytes, int] | (None, None)` – Loopback test with type checking. 

## IMU

* `imu_get_temperature() -> float` – Returns °C, 2-decimal rounding.  
* `imu_get_accelerometer() -> list[int]` – `[x,y,z]` int16 (little-endian).  
* `imu_get_gyroscope() -> list[int]` – `[x,y,z]` int16 (little-endian). 

## Camera / FPGA control (bitmask-based)

* `reset_camera_sensor(camera_position: int) -> bool` – Bitmask of cameras (bit 0 → cam0 … bit7 → cam7). Validates 0x00–0xFF.  
* `activate_camera_fpga(camera_position: int) -> bool` – Assert ACTIVATE for selected cameras. 
* `enable_camera_fpga(camera_position: int) -> bool` – Power/enable selected cameras’ FPGA domain. 
* `disable_camera_fpga(camera_position: int) -> bool` – Disable selected cameras’ FPGA domain. (See error-checked send/clear pattern.) 
* `get_status_fpga(camera_position: int) -> bool` / `get_usercode_fpga(camera_position: int) -> bool` – Status/usercode queries with error handling. 
* `enter_sram_prog_fpga(...)`, `exit_sram_prog_fpga(...)`, `erase_sram_fpga(...)`, `program_fpga(camera_position: int, manual_process: bool) -> bool` – SRAM program flow helpers; `program_fpga` uses extended timeout. 

## Camera power & status

* `get_camera_power_status() -> list[bool]` – Returns list of 8 booleans (cam0–cam7 powered?). Reads a single power-mask byte.  
* `decode_camera_status(status: int) -> str` – Human-readable flags: READY/PROGRAMMED/CONFIGURED/STREAMING. 

## Bitstream I/O

* `send_bitstream_fpga(filename: str) -> bool` – Sends file in 1024-byte blocks, then sends file CRC16 as EOF marker; logs totals. (Robust error handling and CRC computed via `calculate_file_crc`.) 

## High-level camera helpers (used by interface)

* `camera_configure_registers(camera_position: int) -> bool` – Programs sensor registers before capture.
* `camera_configure_test_pattern(camera_position: int, pattern: int) -> bool` – Sets sensor test pattern.
* `camera_capture_histogram(camera_position: int) -> bool` – Triggers histogram capture.
* `camera_get_histogram(camera_position: int) -> bytes|None` – Returns raw frame; interface trims to 4096 and decodes via `bytes_to_integers`.
  (See `MOTIONInterface.get_camera_histogram()` orchestration for order of operations.)   

## Cleanup

* `disconnect() -> None` and `__del__` – Safe teardown of composite handle.  

---

## Quick usage sketch

```python
from omotion.Interface import MOTIONInterface

iface, console_ok, left_ok, right_ok = MOTIONInterface.acquire_motion_interface()  # returns tuple
if console_ok and left_ok:
    # get a histogram from left camera 0 using pattern 4
    parsed = iface.get_camera_histogram("left", camera_id=0, test_pattern_id=4, auto_upload=True)
    if parsed:
        values, flags = parsed
        print(values[:8], flags[:8])
```
