# MOTIONSensor API Reference

**Module:** `omotion.Sensor`  
**Class:** `MOTIONSensor`

**Purpose:** Provides low-level control for sensor modules including IMU operations, camera/FPGA management, power control, and bitstream programming. Wraps a `MotionComposite` UART connection to communicate with sensor hardware.

---

## Table of Contents

- [Constructor](#constructor)
- [Connection & Basic Operations](#connection--basic-operations)
- [Device Information](#device-information)
- [LED & Fan Control](#led--fan-control)
- [IMU Operations](#imu-operations)
- [FPGA Operations](#fpga-operations)
- [Camera Configuration](#camera-configuration)
- [Camera Streaming Control](#camera-streaming-control)
- [Camera Power Management](#camera-power-management)
- [Camera Status](#camera-status)
- [Histogram Operations](#histogram-operations)
- [Frame Sync Control](#frame-sync-control)
- [I2C Operations](#i2c-operations)
- [Utility Methods](#utility-methods)
- [Usage Examples](#usage-examples)

---

## Constructor

### `__init__(uart: MotionComposite)`

Initialize the MOTIONSensor module.

**Parameters:**
- `uart` (MotionComposite): The UART communication interface for sensor operations

**Notes:**
- In synchronous mode, automatically checks USB connection status on initialization
- Logs connection status to help with debugging

---

## Connection & Basic Operations

### `is_connected() -> bool`

Check if the MOTIONSensor is connected.

**Returns:**
- `bool`: True if connected, False otherwise

---

### `ping() -> bool`

Send a ping command to the MOTIONSensor and verify response.

**Returns:**
- `bool`: True if ping successful, False on error or timeout

**Raises:**
- `ValueError`: If device not connected
- `Exception`: On unexpected errors during communication

**Notes:**
- Returns True immediately in demo mode
- Clears receive buffer after operation

---

### `soft_reset() -> bool`

Perform a soft reset on the Sensor device.

**Returns:**
- `bool`: True if reset successful, False otherwise

**Raises:**
- `ValueError`: If UART not connected
- `Exception`: On communication errors

---

## Device Information

### `get_version() -> str`

Retrieve the firmware version of the Sensor Module.

**Returns:**
- `str`: Firmware version in format 'vX.Y.Z' (e.g., 'v1.2.3')
- Returns 'v0.0.0' on error or invalid response
- Returns 'v0.1.1' in demo mode

**Raises:**
- `ValueError`: If device not connected
- `Exception`: On communication errors

---

### `get_hardware_id() -> str | None`

Retrieve the hardware ID of the Sensor Module.

**Returns:**
- `str`: Hardware ID as 32-character hexadecimal string (16 bytes)
- `None`: On error or invalid response

**Raises:**
- `ValueError`: If device not connected
- `Exception`: On communication errors

**Example:**
```python
hw_id = sensor.get_hardware_id()
# Returns: "deadbeefcafebabe1122334455667788"
```

---

### `echo(echo_data: bytes | bytearray | None = None) -> tuple[bytes, int]`

Send an echo command with data and receive the same data in response.

**Parameters:**
- `echo_data` (bytes | bytearray | None): Data to echo (byte array)

**Returns:**
- `tuple[bytes, int]`: The echoed data and its length

**Raises:**
- `ValueError`: If device not connected
- `TypeError`: If echo_data is not a byte array
- `Exception`: On communication errors

**Notes:**
- Useful for testing communication integrity

---

## LED & Fan Control

### `toggle_led() -> bool`

Toggle the LED on the Sensor Module.

**Returns:**
- `bool`: True if command sent successfully, False otherwise

**Raises:**
- `ValueError`: If device not connected
- `Exception`: On communication errors

---

### `set_fan_control(fan_on: bool) -> bool`

Set the fan control pin state on the Sensor Module.

**Parameters:**
- `fan_on` (bool): True to turn fan ON (HIGH), False to turn fan OFF (LOW)

**Returns:**
- `bool`: True if command sent successfully, False otherwise

**Raises:**
- `ValueError`: If device not connected
- `Exception`: On communication errors

---

### `get_fan_control_status() -> bool`

Get the current fan control pin state from the Sensor Module.

**Returns:**
- `bool`: True if fan is ON (HIGH), False if fan is OFF (LOW)

**Raises:**
- `ValueError`: If device not connected
- `Exception`: On communication errors

---

## IMU Operations

### `imu_get_temperature() -> float`

Retrieve the temperature reading from the IMU.

**Returns:**
- `float`: Temperature value in Celsius (rounded to 2 decimal places)

**Raises:**
- `ValueError`: If device not connected or invalid data length
- `Exception`: On communication errors

---

### `imu_get_accelerometer() -> list[int]`

Retrieve raw accelerometer readings (X, Y, Z) from the IMU.

**Returns:**
- `list[int]`: [x, y, z] accelerometer readings as signed 16-bit integers
- Returns [0, 0, 0] in demo mode

**Raises:**
- `ValueError`: If device not connected or data length invalid
- `Exception`: On communication errors

**Notes:**
- Values are little-endian signed 16-bit integers

---

### `imu_get_gyroscope() -> list[int]`

Retrieve raw gyroscope readings (X, Y, Z) from the IMU.

**Returns:**
- `list[int]`: [x, y, z] gyroscope readings as signed 16-bit integers
- Returns [0, 0, 0] in demo mode

**Raises:**
- `ValueError`: If device not connected or data length invalid
- `Exception`: On communication errors

**Notes:**
- Values are little-endian signed 16-bit integers

---

## FPGA Operations

**Camera Position Bitmask Format:**  
All FPGA operations use a bitmask where each bit represents one camera:
- Bit 0 = Camera 0
- Bit 1 = Camera 1
- ...
- Bit 7 = Camera 7

**Examples:**
- `0x01` (0b00000001) = Camera 0 only
- `0x09` (0b00001001) = Cameras 0 and 3
- `0xFF` (0b11111111) = All cameras

---

### `reset_camera_sensor(camera_position: int) -> bool`

Reset the camera sensor(s) at the specified position(s).

**Parameters:**
- `camera_position` (int): Bitmask representing cameras to reset (0x00 - 0xFF)

**Returns:**
- `bool`: True if reset successful, False otherwise

**Raises:**
- `ValueError`: If camera_position not in valid range or device not connected
- `Exception`: On communication errors

---

### `activate_camera_fpga(camera_position: int) -> bool`

Activate the camera sensor(s) FPGA.

**Parameters:**
- `camera_position` (int): Bitmask representing cameras to activate (0x00 - 0xFF)

**Returns:**
- `bool`: True if activation successful, False otherwise

**Raises:**
- `ValueError`: If camera_position not in valid range or device not connected
- `Exception`: On communication errors

---

### `enable_camera_fpga(camera_position: int) -> bool`

Enable the camera sensor(s) FPGA.

**Parameters:**
- `camera_position` (int): Bitmask representing cameras to enable (0x00 - 0xFF)

**Returns:**
- `bool`: True if enable successful, False otherwise

**Raises:**
- `ValueError`: If camera_position not in valid range or device not connected
- `Exception`: On communication errors

---

### `disable_camera_fpga(camera_position: int) -> bool`

Disable the camera sensor(s) FPGA.

**Parameters:**
- `camera_position` (int): Bitmask representing cameras to disable (0x00 - 0xFF)

**Returns:**
- `bool`: True if disable successful, False otherwise

**Raises:**
- `ValueError`: If camera_position not in valid range or device not connected
- `Exception`: On communication errors

---

### `check_camera_fpga(camera_position: int) -> bool`

Check the camera sensor(s) FPGA ID.

**Parameters:**
- `camera_position` (int): Bitmask representing cameras to check (0x00 - 0xFF)

**Returns:**
- `bool`: True if check successful, False otherwise

**Raises:**
- `ValueError`: If camera_position not in valid range or device not connected
- `Exception`: On communication errors

---

### `enter_sram_prog_fpga(camera_position: int) -> bool`

Enter SRAM Programming mode for the camera sensor(s) FPGA.

**Parameters:**
- `camera_position` (int): Bitmask representing cameras (0x00 - 0xFF)

**Returns:**
- `bool`: True if successful, False otherwise

**Raises:**
- `ValueError`: If camera_position not in valid range or device not connected
- `Exception`: On communication errors

---

### `exit_sram_prog_fpga(camera_position: int) -> bool`

Exit SRAM Programming mode for the camera sensor(s) FPGA.

**Parameters:**
- `camera_position` (int): Bitmask representing cameras (0x00 - 0xFF)

**Returns:**
- `bool`: True if successful, False otherwise

**Raises:**
- `ValueError`: If camera_position not in valid range or device not connected
- `Exception`: On communication errors

---

### `erase_sram_fpga(camera_position: int) -> bool`

Erase SRAM for the camera sensor(s) FPGA.

**Parameters:**
- `camera_position` (int): Bitmask representing cameras (0x00 - 0xFF)

**Returns:**
- `bool`: True if erase successful, False otherwise

**Raises:**
- `ValueError`: If camera_position not in valid range or device not connected
- `Exception`: On communication errors

**Notes:**
- Uses extended timeout (30 seconds) due to erase operation duration

---

### `get_status_fpga(camera_position: int) -> bool`

Get status of FPGA for the camera sensor(s).

**Parameters:**
- `camera_position` (int): Bitmask representing cameras (0x00 - 0xFF)

**Returns:**
- `bool`: True if status retrieved successfully, False otherwise

**Raises:**
- `ValueError`: If camera_position not in valid range or device not connected
- `Exception`: On communication errors

---

### `get_usercode_fpga(camera_position: int) -> bool`

Get usercode of FPGA for the camera sensor(s).

**Parameters:**
- `camera_position` (int): Bitmask representing cameras (0x00 - 0xFF)

**Returns:**
- `bool`: True if usercode retrieved successfully, False otherwise

**Raises:**
- `ValueError`: If camera_position not in valid range or device not connected
- `Exception`: On communication errors

---

### `send_bitstream_fpga(filename: str) -> bool`

Send a bitstream file to the FPGA via UART in blocks.

**Parameters:**
- `filename` (str): Full path to the bitstream file

**Returns:**
- `bool`: True if transfer successful, False otherwise

**Raises:**
- `ValueError`: If filename is None
- `FileNotFoundError`: If file doesn't exist
- `Exception`: On transfer errors

**Notes:**
- Sends file in 1024-byte blocks
- Calculates and logs CRC16 checksum
- Logs block count and total bytes transferred

---

### `program_fpga(camera_position: int, manual_process: bool) -> bool`

Program FPGA for the camera sensor(s).

**Parameters:**
- `camera_position` (int): Bitmask representing cameras (0x00 - 0xFF)
- `manual_process` (bool): If True, manual process mode; otherwise automatic

**Returns:**
- `bool`: True if programming successful, False otherwise

**Raises:**
- `ValueError`: If camera_position not in valid range or device not connected
- `Exception`: On communication errors

**Notes:**
- Uses extended timeout (60 seconds) for programming operation

---

## Camera Configuration

### `camera_configure_registers(camera_position: int) -> bool`

Program camera sensor(s) registers at the specified position(s).

**Parameters:**
- `camera_position` (int): Bitmask representing cameras (0x00 - 0xFF)

**Returns:**
- `bool`: True if configuration successful, False otherwise

**Raises:**
- `ValueError`: If camera_position not in valid range or device not connected
- `Exception`: On communication errors

**Notes:**
- Uses extended timeout (60 seconds)

---

### `camera_configure_test_pattern(camera_position: int, test_pattern: int = 0) -> bool`

Set camera sensor(s) test pattern registers.

**Parameters:**
- `camera_position` (int): Bitmask representing cameras (0x00 - 0xFF)
- `test_pattern` (int): Test pattern ID (0x00 - 0x04), defaults to 0 (bars)

**Returns:**
- `bool`: True if configuration successful, False otherwise

**Raises:**
- `ValueError`: If parameters not in valid range or device not connected
- `Exception`: On communication errors

**Notes:**
- Uses extended timeout (60 seconds)

---

### `switch_camera(camera_id: int, packet_id: int | None = None)`

Switch to a specific camera.

**Parameters:**
- `camera_id` (int): Camera ID to switch to (0-7)
- `packet_id` (int | None): Optional packet identifier

**Returns:**
- Response packet from the device

---

## Camera Streaming Control

### `enable_camera(camera_position: int) -> bool`

Enable streaming for specified camera(s).

**Parameters:**
- `camera_position` (int): Bitmask representing cameras (0x00 - 0xFF)

**Returns:**
- `bool`: True if successful, False otherwise

**Raises:**
- `ValueError`: If camera_position not in valid range or device not connected
- `Exception`: On communication errors

---

### `disable_camera(camera_position: int) -> bool`

Disable streaming for specified camera(s).

**Parameters:**
- `camera_position` (int): Bitmask representing cameras (0x00 - 0xFF)

**Returns:**
- `bool`: True if successful, False otherwise

**Raises:**
- `ValueError`: If camera_position not in valid range or device not connected
- `Exception`: On communication errors

**Notes:**
- Uses shorter timeout (0.3 seconds)

---

## Camera Power Management

### `enable_camera_power(camera_mask: int) -> bool`

Enable power to the specified camera(s).

**Parameters:**
- `camera_mask` (int): Bitmask representing cameras to power on (0x01 - 0xFF)

**Returns:**
- `bool`: True if successful, False otherwise

**Raises:**
- `ValueError`: If camera_mask not in valid range or device not connected
- `Exception`: On communication errors

---

### `disable_camera_power(camera_mask: int) -> bool`

Disable power to the specified camera(s).

**Parameters:**
- `camera_mask` (int): Bitmask representing cameras to power off (0x01 - 0xFF)

**Returns:**
- `bool`: True if successful, False otherwise

**Raises:**
- `ValueError`: If camera_mask not in valid range or device not connected
- `Exception`: On communication errors

---

### `get_camera_power_status() -> list`

Get power status for all cameras.

**Returns:**
- `list`: List of 8 booleans representing power status of cameras 0-7
  - True = camera is powered on
  - False = camera is powered off

**Raises:**
- `ValueError`: If device not connected
- `Exception`: On communication errors

---

## Camera Status

### `get_camera_status(camera_position: int) -> dict[int, int] | None`

Get status flags from one or more cameras.

**Parameters:**
- `camera_position` (int): Bitmask of cameras to query (0x00 - 0xFF)

**Returns:**
- `dict[int, int] | None`: Mapping of camera ID to status byte, or None on error

**Status Byte Bits:**
- Bit 0: Peripheral READY (SPI/USART)
- Bit 1: Firmware programmed
- Bit 2: Configured
- Bit 7: Streaming enabled

**Raises:**
- `ValueError`: If input invalid or device not connected
- `Exception`: On communication errors

**Example:**
```python
status = sensor.get_camera_status(0x03)  # Check cameras 0 and 1
# Returns: {0: 0x87, 1: 0x83}
```

---

### `decode_camera_status(status: int) -> str` (Static Method)

Decode the camera status byte into a human-readable string.

**Parameters:**
- `status` (int): The status byte to decode

**Returns:**
- `str`: Human-readable status flags (comma-separated)

**Example:**
```python
status_str = MOTIONSensor.decode_camera_status(0x87)
# Returns: "READY, PROGRAMMED, CONFIGURED, STREAMING"
```

---

## Histogram Operations

### `camera_capture_histogram(camera_position: int) -> bool`

Send frame sync to camera sensor(s) to capture histogram.

**Parameters:**
- `camera_position` (int): Bitmask representing cameras (0x00 - 0xFF)

**Returns:**
- `bool`: True if capture triggered successfully, False otherwise

**Raises:**
- `ValueError`: If camera_position not in valid range or device not connected
- `Exception`: On communication errors

**Notes:**
- Uses timeout of 15 seconds

---

### `camera_get_histogram(camera_position: int) -> bytearray`

Get last histogram from camera sensor(s).

**Parameters:**
- `camera_position` (int): Bitmask representing cameras (0x00 - 0xFF)

**Returns:**
- `bytearray`: Histogram data if successful, None otherwise

**Raises:**
- `ValueError`: If camera_position not in valid range or device not connected
- `Exception`: On communication errors

**Notes:**
- Uses timeout of 15 seconds
- Returns raw histogram data that may need further processing

---

## Frame Sync Control

### `enable_aggregator_fsin() -> bool`

Enable the aggregator frame sync.

**Returns:**
- `bool`: True if successful, False otherwise

**Raises:**
- `Exception`: On communication errors

---

### `disable_aggregator_fsin() -> bool`

Disable the aggregator frame sync.

**Returns:**
- `bool`: True if successful, False otherwise

**Raises:**
- `Exception`: On communication errors

---

### `enable_camera_fsin_ext() -> bool`

Enable the camera sensor(s) for external frame synchronization.

**Returns:**
- `bool`: True if successful, False otherwise

**Raises:**
- `ValueError`: If device not connected
- `Exception`: On communication errors

---

### `disable_camera_fsin_ext() -> bool`

Disable the camera sensor(s) for external frame synchronization.

**Returns:**
- `bool`: True if successful, False otherwise

**Raises:**
- `ValueError`: If device not connected
- `Exception`: On communication errors

---

## I2C Operations

### `camera_i2c_write(packet: I2C_Packet, packet_id: int | None = None)`

Write data to a camera sensor's I2C register.

**Parameters:**
- `packet` (I2C_Packet): I2C packet containing device address, register address, and data
- `packet_id` (int | None): Optional packet identifier

**Returns:**
- `bool`: True if write successful, False otherwise

**Raises:**
- `ValueError`: If device not connected or packet invalid
- `Exception`: On communication errors

---

### `camera_set_gain(gain: int, packet_id: int | None = None)`

Set the camera gain value.

**Parameters:**
- `gain` (int): Gain value (0x00 - 0xFF)
- `packet_id` (int | None): Optional packet identifier

**Returns:**
- `bool`: True if successful, False otherwise

**Notes:**
- Writes to registers 0x3508 (coarse) and 0x3509 (fine, set to 0x00)
- Includes 50ms delays between writes

---

### `camera_set_exposure(exposure_selection: int, us: float | None = None)`

Set the camera exposure time.

**Parameters:**
- `exposure_selection` (int): Predefined exposure index (0-4)
  - 0: 242.83µs
  - 1: 250.67µs
  - 2: 344.67µs
  - 3: 352.50µs
  - 4: 1098.00µs
- `us` (float | None): Optional custom exposure time in microseconds

**Returns:**
- `bool`: True if successful, False otherwise

**Notes:**
- Custom exposure calculated as: `(us / 9) & 0xFF`
- Writes to registers 0x3501 (0x00) and 0x3502 (exposure byte)
- Includes 50ms delays between writes

---

## Utility Methods

### `disconnect()`

Disconnect the UART and clean up resources.

**Notes:**
- Safe to call multiple times
- Automatically called by destructor

---

### `__del__()`

Destructor that ensures cleanup when object is garbage collected.

**Notes:**
- Calls `disconnect()` to ensure proper resource cleanup
- Logs warnings if errors occur during cleanup

---

## Usage Examples

### Basic Connection and Version Check

```python
from omotion.MotionComposite import MotionComposite
from omotion.Sensor import MOTIONSensor

# Create UART connection
uart = MotionComposite()

# Initialize sensor
sensor = MOTIONSensor(uart)

if sensor.is_connected():
    # Check version
    version = sensor.get_version()
    print(f"Firmware version: {version}")
    
    # Ping device
    if sensor.ping():
        print("Device responding")
```

### Reading IMU Data

```python
# Get temperature
temp = sensor.imu_get_temperature()
print(f"Temperature: {temp}°C")

# Get accelerometer data
accel = sensor.imu_get_accelerometer()
print(f"Acceleration [x,y,z]: {accel}")

# Get gyroscope data
gyro = sensor.imu_get_gyroscope()
print(f"Gyroscope [x,y,z]: {gyro}")
```

### Camera Operations

```python
# Reset camera 0
sensor.reset_camera_sensor(0x01)

# Enable FPGA for cameras 0 and 1
sensor.enable_camera_fpga(0x03)

# Configure camera registers
sensor.camera_configure_registers(0x01)

# Set test pattern
sensor.camera_configure_test_pattern(0x01, test_pattern=2)

# Capture and retrieve histogram
sensor.camera_capture_histogram(0x01)
histogram_data = sensor.camera_get_histogram(0x01)
```

### Camera Status Check

```python
# Get status for all cameras
status_dict = sensor.get_camera_status(0xFF)

for cam_id, status_byte in status_dict.items():
    status_str = MOTIONSensor.decode_camera_status(status_byte)
    print(f"Camera {cam_id}: {status_str}")
```

### FPGA Programming

```python
# Program FPGA with bitstream
camera_mask = 0x01  # Camera 0

# Enter programming mode
sensor.enter_sram_prog_fpga(camera_mask)

# Erase SRAM
sensor.erase_sram_fpga(camera_mask)

# Send bitstream file
sensor.send_bitstream_fpga("path/to/bitstream.bit")

# Program and exit
sensor.program_fpga(camera_mask, manual_process=False)
sensor.exit_sram_prog_fpga(camera_mask)
```

### Power Management

```python
# Enable power for cameras 0 and 1
sensor.enable_camera_power(0x03)

# Check power status
power_status = sensor.get_camera_power_status()
for i, powered in enumerate(power_status):
    print(f"Camera {i}: {'ON' if powered else 'OFF'}")

# Disable power
sensor.disable_camera_power(0x03)
```

### Complete Workflow Example

```python
from omotion.Interface import MOTIONInterface

# Acquire interface (returns console, left, right sensors)
iface, console_ok, left_ok, right_ok = MOTIONInterface.acquire_motion_interface()

if console_ok and left_ok:
    # Use the left sensor through the interface
    left_sensor = iface.left
    
    # Get temperature
    temp = left_sensor.imu_get_temperature()
    
    # Get histogram from camera 0 with test pattern 4
    parsed = iface.get_camera_histogram("left", camera_id=0, 
                                        test_pattern_id=4, 
                                        auto_upload=True)
    if parsed:
        values, flags = parsed
        print(f"First 8 histogram values: {values[:8]}")
        print(f"First 8 flags: {flags[:8]}")
```

---

## Notes

- All methods that communicate with hardware return `True`/`False` for success/failure
- Demo mode is supported throughout for testing without hardware
- Most camera operations use bitmask addressing (0x00 - 0xFF) to control multiple cameras simultaneously
- Extended timeouts are used for long-running operations (erase, program, configure)
- Always check connection status before performing operations
- Use proper cleanup (`disconnect()`) when done to release resources
