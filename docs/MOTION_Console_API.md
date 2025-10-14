# MOTIONConsole (Console.py)

**Purpose:** Command/telemetry interface to the controller/console MCU (LED, HWID, version, resets, trigger pins, I2C scan, TEC status, etc.). Uses `MOTIONUart` primitives. 

## Constructor

`MOTIONConsole(uart: MOTIONUart)` – Stores UART, optionally checks connection in sync mode. 

## Connection

* `is_connected() -> bool` – True when underlying UART is connected. 
* `disconnect() -> None` – Closes UART and nulls handle; also called from `__del__`.  

## Basic commands

* `ping() -> bool` – Returns `True` on success; error → `False`/exception. 
* `get_version() -> str` – `'vX.Y.Z'` or `'v0.0.0'` when invalid. Demo returns `'v0.1.1'`. 
* `echo(echo_data: bytes|bytearray|None) -> tuple[bytes, int] | (None, None)` – Round-trip data test with validation.  
* `toggle_led() -> bool` – Toggles console LED, returns `True` on send. 
* `get_hardware_id() -> str|None` – 16-byte ID hex string; demo returns fixed bytes.  

## Resets / DFU

* `enter_dfu_mode() -> bool` – Sends DFU command; `True` on success. 
* `soft_reset() -> bool` – Issues soft reset. 

## I²C & diagnostics

* `scan_i2c_mux_channel(mux_index: int, channel: int) -> list[int]` – Scans a specific mux (0/1) and channel (0–7), returns detected addresses. Validates args.  
* `get_tec_enabled() -> bool` – Returns TEC-DAC ready flag from controller.  
* (Example ADC/pulse read shown in file under controller reads.) 
