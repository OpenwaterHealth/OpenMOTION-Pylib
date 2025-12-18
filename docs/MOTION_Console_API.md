# MOTIONConsole API Reference

**Module:** `omotion.Console`  
**Class:** `MOTIONConsole`

**Purpose:** Provides command and telemetry interface to the controller/console MCU including LED control, hardware identification, version information, resets, trigger management, I2C operations, TEC (Thermoelectric Cooler) control, and system monitoring. Uses `MOTIONUart` primitives for communication.

---

## Table of Contents

- [Constructor](#constructor)
- [Connection Management](#connection-management)
- [Basic Commands](#basic-commands)
- [Device Information](#device-information)
- [LED Control](#led-control)
- [Fan Control](#fan-control)
- [Reset & DFU Operations](#reset--dfu-operations)
- [I2C Operations](#i2c-operations)
- [Trigger Management](#trigger-management)
- [Pulse Counters](#pulse-counters)
- [GPIO & ADC Operations](#gpio--adc-operations)
- [Temperature Monitoring](#temperature-monitoring)
- [TEC (Thermoelectric Cooler) Control](#tec-thermoelectric-cooler-control)
- [System Information](#system-information)
- [PDU Monitoring](#pdu-monitoring)
- [Usage Examples](#usage-examples)

---

## Constructor

### `__init__(uart: MOTIONUart)`

Initialize the MOTIONConsole module.

**Parameters:**
- `uart` (MOTIONUart): The UART communication interface for console operations

**Notes:**
- In synchronous mode, automatically checks USB connection status on initialization
- Logs connection status to help with debugging

---

## Connection Management

### `is_connected() -> bool`

Check if the MOTIONConsole is connected.

**Returns:**
- `bool`: True if connected, False otherwise

---

### `disconnect()`

Disconnect the UART and clean up resources.

**Notes:**
- Safe to call multiple times
- Automatically called by destructor
- Sets uart reference to None after disconnection

---

### `__del__()`

Destructor that ensures cleanup when object is garbage collected.

**Notes:**
- Calls `disconnect()` to ensure proper resource cleanup
- Logs warnings if errors occur during cleanup

---

## Basic Commands

### `ping() -> bool`

Send a ping command to the MOTIONConsole and verify response.

**Returns:**
- `bool`: True if ping successful, False on error or timeout

**Raises:**
- `ValueError`: If device not connected
- `Exception`: On unexpected errors during communication

**Notes:**
- Returns True immediately in demo mode
- Clears receive buffer after operation

---

### `echo(echo_data: bytes | bytearray | None = None) -> tuple[bytes, int]`

Send an echo command with data and receive the same data in response.

**Parameters:**
- `echo_data` (bytes | bytearray | None): Data to echo (byte array)

**Returns:**
- `tuple[bytes, int]`: The echoed data and its length
- `(None, None)`: On error or if device not connected

**Raises:**
- `ValueError`: If device not connected
- `TypeError`: If echo_data is not a byte array
- `Exception`: On communication errors

**Notes:**
- Useful for testing communication integrity
- In demo mode returns `(b"Hello LIFU!", 11)`

---

## Device Information

### `get_version() -> str`

Retrieve the firmware version of the Console Module.

**Returns:**
- `str`: Firmware version in format 'vX.Y.Z' (e.g., 'v1.2.3')
- Returns 'v0.0.0' on error or invalid response
- Returns 'v0.1.1' in demo mode

**Raises:**
- `ValueError`: If device not connected
- `Exception`: On communication errors

---

### `get_hardware_id() -> str | None`

Retrieve the hardware ID of the Console Module.

**Returns:**
- `str`: Hardware ID as 32-character hexadecimal string (16 bytes)
- `None`: On error or invalid response

**Raises:**
- `ValueError`: If device not connected
- `Exception`: On communication errors

**Example:**
```python
hw_id = console.get_hardware_id()
# Returns: "deadbeefcafebabe1122334455667788"
```

---

## LED Control

### `toggle_led() -> bool`

Toggle the LED on the Console Module.

**Returns:**
- `bool`: True if command sent successfully, False otherwise

**Raises:**
- `ValueError`: If device not connected
- `Exception`: On communication errors

---

### `set_rgb_led(rgb_state: int) -> int`

Set the RGB LED state.

**Parameters:**
- `rgb_state` (int): The desired RGB state
  - 0: OFF
  - 1: IND1 (Red)
  - 2: IND2 (Green)
  - 3: IND3 (Blue)

**Returns:**
- `int`: The current RGB state after setting, -1 on error

**Raises:**
- `ValueError`: If device not connected or rgb_state invalid
- `Exception`: On communication errors

**Notes:**
- RGB state is sent as the reserved byte in the packet

---

### `get_rgb_led() -> int`

Get the current RGB LED state.

**Returns:**
- `int`: The current RGB state (0=OFF, 1=IND1, 2=IND2, 3=IND3)
- Returns -1 on error

**Raises:**
- `ValueError`: If device not connected
- `Exception`: On communication errors

**Notes:**
- Returns 1 (RED) in demo mode

---

## Fan Control

### `set_fan_speed(fan_speed: int = 50) -> int`

Set the fan speed percentage.

**Parameters:**
- `fan_speed` (int): The desired fan speed (0-100), default is 50

**Returns:**
- `int`: The fan speed value that was set, -1 on error

**Raises:**
- `ValueError`: If device not connected or fan_speed not in range 0-100
- `Exception`: On communication errors

---

### `get_fan_speed() -> int`

Get the current fan speed percentage.

**Returns:**
- `int`: The current fan speed (0-100)
- Returns -1 on error
- Returns 40 in demo mode

**Raises:**
- `ValueError`: If device not connected
- `Exception`: On communication errors

---

## Reset & DFU Operations

### `soft_reset() -> bool`

Perform a soft reset on the Console device.

**Returns:**
- `bool`: True if reset successful, False otherwise

**Raises:**
- `ValueError`: If device not connected
- `Exception`: On communication errors

---

### `enter_dfu() -> bool`

Enter DFU (Device Firmware Update) mode on Console device.

**Returns:**
- `bool`: True if successfully entered DFU mode, False otherwise

**Raises:**
- `ValueError`: If device not connected
- `Exception`: On communication errors

**Notes:**
- Device will reset into DFU mode for firmware updates

---

## I2C Operations

### `scan_i2c_mux_channel(mux_index: int, channel: int) -> list[int]`

Scan a specific channel on an I2C MUX and return detected I2C addresses.

**Parameters:**
- `mux_index` (int): Index of the I2C MUX
  - 0: MUX at 0x70
  - 1: MUX at 0x71
- `channel` (int): Channel number on the MUX to activate (0-7)

**Returns:**
- `list[int]`: List of detected I2C device addresses on the specified mux/channel
- Returns empty list if no devices found

**Raises:**
- `ValueError`: If mux_index not in [0, 1] or channel not in range 0-7
- `Exception`: On communication errors

**Example:**
```python
devices = console.scan_i2c_mux_channel(0, 3)
# Returns: [0x50, 0x51] (example detected addresses)
```

---

### `read_i2c_packet(mux_index: int, channel: int, device_addr: int, reg_addr: int, read_len: int) -> tuple[bytes, int]`

Read data from I2C device through MUX.

**Parameters:**
- `mux_index` (int): Which MUX to use (0 or 1)
- `channel` (int): Which MUX channel to select (0-7)
- `device_addr` (int): I2C device address (7-bit)
- `reg_addr` (int): Register address to read from
- `read_len` (int): Number of bytes to read

**Returns:**
- `tuple[bytes, int]`: The received data and its length
- `(None, None)`: On error

**Raises:**
- `ValueError`: If mux_index not in [0, 1] or channel not in range 0-7
- `Exception`: On communication errors

---

### `write_i2c_packet(mux_index: int, channel: int, device_addr: int, reg_addr: int, data: bytes) -> bool`

Write data to I2C device through MUX.

**Parameters:**
- `mux_index` (int): Which MUX to use (0 or 1)
- `channel` (int): Which MUX channel to select (0-7)
- `device_addr` (int): I2C device address (7-bit)
- `reg_addr` (int): Register address to write to
- `data` (bytes): Bytes to write

**Returns:**
- `bool`: True if write succeeded, False otherwise

**Raises:**
- `ValueError`: If mux_index not in [0, 1] or channel not in range 0-7
- `Exception`: On communication errors

---

## Trigger Management

### `set_trigger_json(data: dict) -> dict | None`

Set the trigger configuration for console device using JSON format.

**Parameters:**
- `data` (dict): Dictionary containing the trigger configuration

**Returns:**
- `dict`: JSON response from the device
- `None`: On error or invalid data

**Raises:**
- `ValueError`: If data is None or device not connected
- `Exception`: On communication errors

**Notes:**
- Data is JSON-encoded before sending
- Response is JSON-decoded if valid

---

### `get_trigger_json() -> dict | None`

Get the current trigger configuration from the Console device.

**Returns:**
- `dict`: JSON object containing trigger configuration
- `None`: On error or if response is not valid JSON

**Raises:**
- `ValueError`: If device not connected
- `Exception`: On communication errors

---

### `start_trigger() -> bool`

Start the trigger on the Console device.

**Returns:**
- `bool`: True if trigger started successfully, False otherwise

**Raises:**
- `ValueError`: If device not connected
- `Exception`: On communication errors

---

### `stop_trigger() -> bool`

Stop the trigger on the Console device.

**Returns:**
- `bool`: True if trigger stopped successfully, False otherwise

**Raises:**
- `ValueError`: If device not connected
- `Exception`: On communication errors

---

## Pulse Counters

### `get_fsync_pulsecount() -> int`

Get FSYNC (Frame Sync) pulse count from the Console device.

**Returns:**
- `int`: The number of FSYNC pulses received
- Returns 0 on error

**Raises:**
- `ValueError`: If device not connected
- `Exception`: On communication errors

**Notes:**
- Pulse count is returned as a 4-byte little-endian unsigned integer

---

### `get_lsync_pulsecount() -> int`

Get LSYNC (Line Sync) pulse count from the Console device.

**Returns:**
- `int`: The number of LSYNC pulses received
- Returns 0 on error

**Raises:**
- `ValueError`: If device not connected
- `Exception`: On communication errors

**Notes:**
- Pulse count is returned as a 4-byte little-endian unsigned integer

---

## GPIO & ADC Operations

### `read_gpio_value() -> float`

Read GPIO value from the Console device.

**Returns:**
- `float`: The GPIO value
- Returns 0 on error

**Raises:**
- `ValueError`: If device not connected
- `Exception`: On communication errors

**Notes:**
- Value is returned as a 4-byte little-endian unsigned integer

---

### `read_adc_value() -> float`

Read ADC value from the Console device.

**Returns:**
- `float`: The ADC value
- Returns 0 on error

**Raises:**
- `ValueError`: If device not connected
- `Exception`: On communication errors

**Notes:**
- Value is returned as a 4-byte little-endian unsigned integer

---

## Temperature Monitoring

### `get_temperatures() -> tuple[float, float, float]`

Get the current temperatures from the Console device.

**Returns:**
- `tuple[float, float, float]`: (mcu_temp, safety_temp, ta_temp) in °C
- Returns (35.0, 45.0, 25.0) in demo mode

**Raises:**
- `ValueError`: If device not connected or payload size is unexpected
- `Exception`: On communication errors

**Notes:**
- Temperatures are returned as three 4-byte little-endian floats (12 bytes total)

**Example:**
```python
mcu, safety, ta = console.get_temperatures()
print(f"MCU: {mcu}°C, Safety: {safety}°C, TA: {ta}°C")
```

---

## TEC (Thermoelectric Cooler) Control

### `tec_voltage(voltage: float | None = None) -> float`

Get or set TEC setpoint voltage.

**Parameters:**
- `voltage` (float | None): Optional voltage to set (0.0 - 5.0 V)
  - If None: Get current voltage
  - If specified: Set new voltage

**Returns:**
- `float`: The current TEC voltage
- Returns 0 on error

**Raises:**
- `ValueError`: If device not connected or voltage out of range
- `Exception`: On communication errors

**Notes:**
- Valid voltage range: 0.0 to 5.0 V
- Voltage is sent/received as 4-byte little-endian float

**Example:**
```python
# Set voltage
console.tec_voltage(2.5)

# Get voltage
current_v = console.tec_voltage()
```

---

### `tec_adc(channel: int) -> float | list[float]`

Get TEC ADC voltage(s).

**Parameters:**
- `channel` (int): ADC channel to read
  - 0-3: Single channel
  - 4: All channels

**Returns:**
- `float`: Single channel voltage (if channel 0-3)
- `list[float]`: Four voltages [ch0, ch1, ch2, ch3] (if channel 4)
- Returns 0 on error

**Raises:**
- `ValueError`: If device not connected or channel not in [0, 1, 2, 3, 4]
- `Exception`: On communication errors

**Notes:**
- Single channel returns 4 bytes (1 float)
- All channels returns 16 bytes (4 floats)

**Example:**
```python
# Read single channel
v = console.tec_adc(0)

# Read all channels
voltages = console.tec_adc(4)
```

---

### `tec_status() -> tuple[float, float, float, float, bool]`

Get comprehensive TEC status information.

**Returns:**
- `tuple`: (vout, temp_set, tec_curr, tec_volt, tec_good)
  - `vout` (str): Output voltage (formatted to 6 decimals)
  - `temp_set` (str): Temperature setpoint (formatted to 6 decimals)
  - `tec_curr` (str): TEC current (formatted to 6 decimals)
  - `tec_volt` (str): TEC voltage (formatted to 6 decimals)
  - `tec_good` (bool): TEC status flag
- Returns (1.0, 0.5, 0.5, 25.0, True) in demo mode

**Raises:**
- `ValueError`: If device not connected or response lengths unexpected
- `Exception`: If device reports an error

**Notes:**
- Makes two UART requests: one for status flag, one for ADC values

**Example:**
```python
vout, temp_set, tec_curr, tec_volt, tec_good = console.tec_status()
print(f"TEC Good: {tec_good}, Vout: {vout}V, Current: {tec_curr}V")
```

---

## System Information

### `read_board_id() -> int`

Read the Board ID from the Console device.

**Returns:**
- `int`: The board ID value
- Returns 0 on error

**Raises:**
- `ValueError`: If device not connected
- `Exception`: On communication errors

**Notes:**
- Board ID is returned as a single byte

---

## PDU Monitoring

### `PDUMon` (DataClass)

Data structure for PDU monitoring data.

**Attributes:**
- `raws` (List[int]): 16 raw uint16 values from ADC
- `volts` (List[float]): 16 converted voltage values (float32)

---

### `read_pdu_mon() -> PDUMon | None`

Read PDU (Power Distribution Unit) monitoring data.

**Returns:**
- `PDUMon`: Object containing raw ADC values and converted voltages
- `None`: On error
- Returns zeroed PDUMon in demo mode

**Raises:**
- `ValueError`: If device not connected or payload size unexpected
- `Exception`: On communication errors

**Notes:**
- Expects 96 bytes: 32 bytes for 16 uint16 values + 64 bytes for 16 float32 values
- Data is unpacked using little-endian format: `<16H16f`

**Example:**
```python
pdu = console.read_pdu_mon()
if pdu:
    print(f"Raw values: {pdu.raws}")
    print(f"Voltages: {pdu.volts}")
```

---

## Usage Examples

### Basic Connection and Version Check

```python
from omotion.MotionUart import MOTIONUart
from omotion.Console import MOTIONConsole

# Create UART connection
uart = MOTIONUart()

# Initialize console
console = MOTIONConsole(uart)

if console.is_connected():
    # Check version
    version = console.get_version()
    print(f"Firmware version: {version}")
    
    # Ping device
    if console.ping():
        print("Device responding")
```

### LED Control

```python
# Toggle LED
console.toggle_led()

# Set RGB LED to red
console.set_rgb_led(1)

# Get current RGB state
state = console.get_rgb_led()
print(f"RGB LED state: {state}")
```

### Fan Control

```python
# Set fan speed to 75%
console.set_fan_speed(75)

# Get current fan speed
speed = console.get_fan_speed()
print(f"Fan speed: {speed}%")
```

### I2C Operations

```python
# Scan I2C devices on MUX 0, channel 3
devices = console.scan_i2c_mux_channel(0, 3)
print(f"Found devices: {[hex(d) for d in devices]}")

# Read from I2C device
data, length = console.read_i2c_packet(
    mux_index=0,
    channel=3,
    device_addr=0x50,
    reg_addr=0x00,
    read_len=4
)
if data:
    print(f"Read {length} bytes: {data.hex()}")

# Write to I2C device
success = console.write_i2c_packet(
    mux_index=0,
    channel=3,
    device_addr=0x50,
    reg_addr=0x00,
    data=b'\x01\x02\x03\x04'
)
print(f"Write {'succeeded' if success else 'failed'}")
```

### Trigger Management

```python
# Configure trigger
trigger_config = {
    "enabled": True,
    "frequency": 1000,
    "duty_cycle": 50
}
response = console.set_trigger_json(trigger_config)
print(f"Trigger configured: {response}")

# Start trigger
console.start_trigger()

# Get pulse counts
fsync_count = console.get_fsync_pulsecount()
lsync_count = console.get_lsync_pulsecount()
print(f"FSYNC: {fsync_count}, LSYNC: {lsync_count}")

# Stop trigger
console.stop_trigger()
```

### Temperature Monitoring

```python
# Get all temperatures
mcu_temp, safety_temp, ta_temp = console.get_temperatures()
print(f"MCU: {mcu_temp:.2f}°C")
print(f"Safety: {safety_temp:.2f}°C")
print(f"TA: {ta_temp:.2f}°C")
```

### TEC Control

```python
# Set TEC voltage
console.tec_voltage(2.5)
print("TEC voltage set to 2.5V")

# Get current TEC voltage
current_v = console.tec_voltage()
print(f"Current TEC voltage: {current_v}V")

# Read all TEC ADC channels
voltages = console.tec_adc(4)
for i, v in enumerate(voltages):
    print(f"Channel {i}: {v}V")

# Get comprehensive TEC status
vout, temp_set, tec_curr, tec_volt, tec_good = console.tec_status()
print(f"TEC Status:")
print(f"  Output: {vout}V")
print(f"  Setpoint: {temp_set}V")
print(f"  Current: {tec_curr}V")
print(f"  Voltage: {tec_volt}V")
print(f"  Good: {tec_good}")
```

### PDU Monitoring

```python
# Read PDU monitoring data
pdu = console.read_pdu_mon()
if pdu:
    print("PDU Monitoring:")
    print(f"  Raw values: {pdu.raws}")
    print(f"  Voltages: {[f'{v:.3f}V' for v in pdu.volts]}")
    
    # Check specific channels
    for i in range(16):
        print(f"  Channel {i}: {pdu.raws[i]} (raw) = {pdu.volts[i]:.3f}V")
```

### Complete Workflow Example

```python
from omotion.MotionUart import MOTIONUart
from omotion.Console import MOTIONConsole

# Initialize
uart = MOTIONUart()
console = MOTIONConsole(uart)

if console.is_connected():
    # Get device info
    version = console.get_version()
    hw_id = console.get_hardware_id()
    board_id = console.read_board_id()
    
    print(f"Version: {version}")
    print(f"Hardware ID: {hw_id}")
    print(f"Board ID: {board_id}")
    
    # Set LED to green
    console.set_rgb_led(2)
    
    # Set fan to 60%
    console.set_fan_speed(60)
    
    # Monitor temperatures
    mcu, safety, ta = console.get_temperatures()
    print(f"Temperatures - MCU: {mcu}°C, Safety: {safety}°C, TA: {ta}°C")
    
    # Configure and start trigger
    trigger_cfg = {"enabled": True, "freq": 100}
    console.set_trigger_json(trigger_cfg)
    console.start_trigger()
    
    # Monitor for a while...
    import time
    time.sleep(1)
    
    # Check pulse counts
    fsync = console.get_fsync_pulsecount()
    print(f"FSYNC pulses: {fsync}")
    
    # Stop trigger
    console.stop_trigger()
    
    # Cleanup
    console.disconnect()
```

### DFU Mode Entry

```python
# Enter DFU mode for firmware update
if console.enter_dfu():
    print("Device entered DFU mode")
    # Device is now in DFU mode and will need to be reconnected
    # after firmware update
```

---

## Notes

- All methods that communicate with hardware return appropriate types on success or error indicators on failure
- Demo mode is supported throughout for testing without hardware
- Most operations automatically clear the receive buffer after completion
- Extended timeouts may be needed for some operations
- Always check connection status before performing operations
- Use proper cleanup (`disconnect()`) when done to release resources
- Temperature values are in Celsius
- Voltage values are in Volts
- TEC operations require careful monitoring to prevent thermal damage
- I2C operations use 7-bit addressing
- Pulse counters are cumulative and reset on device restart
