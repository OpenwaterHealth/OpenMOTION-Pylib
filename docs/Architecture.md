# OpenMotion SDK — Software Architecture

## Overview

The OpenMotion SDK is a Python library for controlling optical speckle imaging hardware. It manages two classes of physical device: a **console module** (connected via USB virtual COM port / UART) and up to two **sensor modules** (connected via composite USB bulk-transfer interfaces). The SDK handles device discovery, connection lifecycle, command/response communication, high-speed histogram streaming, science computation, and firmware programming. A thin signal abstraction makes the same SDK usable in both PyQt6 desktop applications and headless Python scripts.

---

## Layer diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Application / QML UI                         │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ signals / callbacks
┌──────────────────────────────▼──────────────────────────────────────┐
│                         MOTIONInterface                              │
│          console_module  ·  sensors  ·  scan_workflow               │
└──────────────┬──────────────────────────────┬───────────────────────┘
               │                              │
               │  Console path                │  Sensor path
               │  (USB VCP / pyserial)        │  (USB bulk transfer)
               │                              │
┌──────────────▼──────────────┐  ┌────────────▼──────────────────────┐
│       MOTIONConsole         │  │       DualMotionComposite          │
│  + ConsoleTelemetryPoller   │  │   (left + right MotionComposite)   │
└──────────────┬──────────────┘  └────────────┬──────────────────────┘
               │                              │
┌──────────────▼──────────────┐  ┌────────────▼──────────────────────┐
│         MOTIONUart          │  │    MotionComposite  (per side)     │
│      (pyserial VCP)         │  │                                    │
│  UartPacket framing + CRC   │  │  CommInterface   StreamInterface   │
└──────────────┬──────────────┘  │  (cmd/resp)      (histo / imu)    │
               │                 │  USB bulk IF 0   USB bulk IF 1+2   │
          pyserial               └────────────┬──────────────────────┘
               │                              │
          USB VCP                       libusb / pyusb
```

---

## Module reference

### Foundational

| Module | Purpose |
|---|---|
| `__init__.py` | Package entry point; exposes `_log_root`, `set_log_root()`, SDK version |
| `config.py` | All protocol constants: packet types, command bytes, PID/VID, hardware geometry |
| `connection_state.py` | `ConnectionState` enum: `DISCONNECTED → DISCOVERED → CONNECTING → CONNECTED / ERROR` |
| `utils.py` | CRC-16 lookup table, `util_crc16()`, VCP port listing, hex formatting |
| `CommandError.py` | `CommandError(RuntimeError)` — raised when hardware returns NAK / BAD_CRC / OW_ERROR |
| `usb_backend.py` | Platform-specific libusb-1.0 backend loader (vendored DLL on Windows) |

### Signal system

| Module | Purpose |
|---|---|
| `MotionSignal.py` | `MOTIONSignal` — lightweight signal with `.connect()` / `.disconnect()` / `.emit()` |
| `signal_wrapper.py` | `SignalWrapper` base class — uses real `pyqtSignal` if PyQt6 is present, falls back to `MOTIONSignal` |

Every class that exposes device events inherits `SignalWrapper` and uses the three standard signals: `signal_connect(str, str)`, `signal_disconnect(str, str)`, `signal_data_received(str, str)`.

### Packet structures

| Module | Wire format | CRC |
|---|---|---|
| `UartPacket.py` | `[0xAA][id:2][type][cmd][addr][rsv][len:2][data:N][crc:2][0xDD]` | CRC-16 lookup table |
| `i2c_packet.py` | `<HBHBH` (little-endian) | CRC-16-CCITT-FALSE (crcmod) |
| `i2c_data_packet.py` | `<BHBBB` + payload | CRC-16-CCITT-FALSE |
| `i2c_status_packet.py` | `<HBBBBH` | CRC-16-CCITT-FALSE |

All packet types validate CRC on receive and raise `ValueError` on mismatch.

### Transport layer

The console and sensor modules use entirely separate transport stacks. They share no base classes at this layer.

**Console transport — `MOTIONUart`** — communicates with the console over a USB virtual COM port using pyserial. Frames messages as `UartPacket` (start byte, ID, type, command, data, CRC-16, end byte). Supports sync mode (blocking read) and async mode (background read thread with per-ID response queues). Emits `signal_connect` / `signal_disconnect` on port insertion/removal.

**Sensor transport — `USBInterfaceBase`** — base class that claims a USB bulk interface and locates its endpoints. `CommInterface` and `StreamInterface` both subclass it. Used exclusively by the sensor path.

**`CommInterface`** — bidirectional command/response over a sensor USB bulk interface (interface 0). Maintains a read thread and a contiguous `_read_buffer`. Supports two modes:

- **Sync mode** — `send_packet()` writes and then blocks until a complete response packet is found in the buffer.
- **Async mode** — a second thread parses packets from the buffer and routes them to per-packet-ID `queue.Queue` objects; `send_packet()` waits on the appropriate queue.

**`StreamInterface`** — input-only bulk streaming for sensor histogram and IMU data (interfaces 1 and 2). Reads fixed-size chunks (one histogram block = 4105 bytes) into a `queue.Queue`. No framing or CRC at this layer — the caller handles packet parsing.

### Device abstraction

**`MotionComposite`** — represents one physical sensor module. Owns three interface instances:

| Interface | Index | Class | Direction | Purpose |
|---|---|---|---|---|
| COMM | 0 | `CommInterface` | Full-duplex | Command / response |
| HISTO | 1 | `StreamInterface` | IN only | Histogram bulk stream |
| IMU | 2 | `StreamInterface` | IN only | IMU data stream |

Always creates `CommInterface` in async mode. Claims all three on `connect()`, releases all three on `disconnect()`.

**`DualMotionComposite`** — scans USB for devices matching the sensor PID and assigns them to left/right slots based on USB port topology (`port_numbers[-1] == 2` → left, `== 3` → right). Manages a `monitor_usb_status()` async coroutine that auto-connects arriving devices and auto-disconnects departing ones.

**`MOTIONConsole`** — wraps `MOTIONUart` with the full console command set (ping, version, TEC, PDU monitor, I2C pass-through, LSYNC counter, FPGA programming commands, etc.). Creates a `ConsoleTelemetryPoller` at init time; the poller is started and stopped externally by `MOTIONInterface` in response to connection signals.

**`MOTIONSensor`** — wraps `MotionComposite` with the full sensor command set (FPGA control, camera enable/disable/config, histogram capture, IMU, firmware DFU). Provides `stream_histograms_to_queue()` and `stream_histograms_to_csv()` for data acquisition.

**`MOTIONInterface`** — top-level entry point. Composes console, dual-composite, and scan workflow. Intercepts raw USB signals from the transport layer and re-emits them as named events (`"CONSOLE"`, `"SENSOR_LEFT"`, `"SENSOR_RIGHT"`). Starts and stops the console telemetry poller in response to console connect/disconnect events.

### Data acquisition

**`MotionProcessing`** — stateless parsing and science computation:

| Class / Function | Purpose |
|---|---|
| `FrameIdUnwrapper` | Converts rolling 8-bit frame counter (0–255) to monotonic absolute frame ID |
| `parse_histogram_packet()` | Extracts `HistogramSample` list from raw bytes; handles multi-camera packets |
| `bytes_to_integers()` | Converts 4096 histogram bytes to 1024 int bins + hidden figures |
| `compute_realtime_stats()` | Computes mean, std, contrast, BFI, BVI from a histogram |
| `SciencePipeline` | Single background thread; aligns frames from left and right sensors by `absolute_frame_id`; fires corrected-sample and science-frame callbacks |
| `stream_queue_to_csv_file()` | CSV writer thread; consumes from a `queue.Queue` |

**`ScanWorkflow`** — orchestrates a complete acquisition:
1. Enable cameras on active sides.
2. Start frame sync (internal or external).
3. Begin histogram streaming on both sides simultaneously.
4. Run `SciencePipeline` for real-time BFI/BVI.
5. Write raw histogram CSV (parallel writer threads per side).
6. Invoke application callbacks for logging, progress, and live sample display.
7. Tear down on completion or cancellation.

### Configuration

**`MotionConfig`** — persistent device configuration stored as a 16-byte binary header (`magic`, `version`, `seq`, `crc`, `json_len`) followed by a JSON payload. Used to store and retrieve per-device parameters.

### Firmware programming

| Module | Mechanism |
|---|---|
| `DFUProgrammer` | Spawns `dfu-util` subprocess; parses progress output; reports phase and percent via callback |
| `FPGAProgrammer` | Page-by-page FPGA flash over the console UART; erases, writes CFG pages in 32-page batches, optionally verifies, writes feature row, refreshes |
| `jedecParser` | Parses JEDEC ASCII fuse files into `JedecImage` (rows × 16 bytes) + extra feature row data |
| `GitHubReleases` | GitHub Releases API client; lists, fetches, and downloads release assets |

---

## Threading model

| Thread | Owner | Daemon | Lifecycle | Purpose |
|---|---|---|---|---|
| `CommInterface.read_thread` | `CommInterface` | Yes | `claim()` → `release()` | USB bulk read into `_read_buffer` |
| `CommInterface.response_thread` | `CommInterface` | Yes | async mode only | Parse packets from buffer, route to response queues |
| `MOTIONUart.read_thread` | `MOTIONUart` | Yes | `connect()` → `disconnect()` | Serial read, parse packets or queue by ID |
| `StreamInterface.thread` | `StreamInterface` | Yes | `start_streaming()` → `stop_streaming()` | Fixed-size USB reads into data queue |
| `ConsoleTelemetryPoller._thread` | `ConsoleTelemetryPoller` | Yes | `start()` → `stop()` | ~1 Hz console health polls |
| `ScanWorkflow._thread` | `ScanWorkflow` | No | `start_scan()` → completion | Full scan lifecycle management |
| `ScanWorkflow._config_thread` | `ScanWorkflow` | No | `start_configure_camera_sensors()` → completion | Camera configuration |
| CSV writer threads | `MotionProcessing` | Yes | scan duration | Write histogram rows to CSV |

**Synchronisation primitives in use:**

| Primitive | Location | Protects |
|---|---|---|
| `threading.RLock` | `CommInterface._io_lock` | USB write/read operations |
| `threading.RLock` | `MOTIONUart._io_lock` | Serial write/read + alignment padding |
| `threading.Lock` | `CommInterface._buffer_lock` | `_read_buffer` |
| `threading.Condition` | `CommInterface._buffer_condition` | Wait for data in async mode |
| `threading.Lock` | `MOTIONUart.response_lock` | `response_queues` dict |
| `threading.Lock` | `ConsoleTelemetryPoller._lock` | `_snapshot`, `_listeners` |
| `threading.Event` | `ConsoleTelemetryPoller._wake` | Smart sleep interrupt |
| `threading.Event` | `ScanWorkflow._stop_evt` | Scan cancellation |
| `threading.Lock` | `ScanWorkflow._lock` | `_running` guard |

Listener callbacks in `ConsoleTelemetryPoller` are copied under the lock but invoked outside it, preventing deadlocks at the cost of snapshot staleness. They run on the poller thread and must be non-blocking.

---

## Signal and event flow

```
USB insert / serial port appears
         │
    MOTIONUart / DualMotionComposite
         │  signal_connect("CONSOLE" | "SENSOR_LEFT" | "SENSOR_RIGHT", ...)
         ▼
    MOTIONInterface._on_console_connect / _on_sensor_connect
         │  ├─ start ConsoleTelemetryPoller (console only)
         │  └─ instantiate MOTIONSensor (sensors only)
         │  signal_connect(forwarded)
         ▼
    Application (MOTIONConnector / QML)
```

The same flow operates in reverse on disconnect. Applications register with `MOTIONInterface.signal_connect` / `signal_disconnect`; they never reference `MOTIONUart` or `DualMotionComposite` directly.

---

## Error handling

The SDK uses a layered catch-log-reraise pattern: each layer catches hardware exceptions, logs them with the module-scoped logger, and re-raises so that the caller can decide what to do.

| Exception | Raised by | Meaning |
|---|---|---|
| `CommandError(RuntimeError)` | `MOTIONUart`, `MOTIONConsole`, `MOTIONSensor` | Device returned NAK, BAD_CRC, or OW_ERROR |
| `ValueError` | All packet parsers, device methods | CRC mismatch, invalid framing, device not connected, bad argument |
| `TypeError` | `MOTIONConsole.echo()` | Wrong argument type |
| `TimeoutError` | `CommInterface`, `MOTIONUart` | No response within timeout |
| `serial.SerialException` | `MOTIONUart` | Serial port failure |
| `usb.core.USBError` | `CommInterface`, `StreamInterface` | USB communication error |
| `JedecError` | `jedecParser` | JEDEC file format violation |
| `FpgaUpdateError` | `FPGAProgrammer` | FPGA programming sequence failure |
| `FileNotFoundError` | `DFUProgrammer`, `usb_backend` | Missing dfu-util binary or libusb DLL |

USB timeout errnos (110 on Linux, 10060 on Windows) are suppressed inside read loops and treated as normal idle conditions. Errno 32 (broken pipe) and 19/5 (IO/no-device) trigger disconnect callbacks.

---

## Logging

Every module creates its logger as:

```python
logger = logging.getLogger(f"{_log_root}.ModuleName" if _log_root else "ModuleName")
```

`_log_root` defaults to `"openmotion.sdk"` and is set once by the application via `set_log_root()`. If no handlers are configured on the root logger at import time, a console handler is added automatically. This allows applications to control the entire SDK log hierarchy through a single prefix.

---

## Demo mode

`MOTIONUart`, `MotionComposite`, and `DualMotionComposite` all accept a `demo_mode` flag. When set, `MOTIONUart` skips serial I/O and emits a synthetic connect signal immediately. `MOTIONConsole` returns hardcoded mock values from `tec_status()`, `get_version()`, etc. This allows the application UI to be developed and tested without physical hardware.

---

## Key data types

| Type | Module | Description |
|---|---|---|
| `UartPacket` | `UartPacket` | Parsed or constructed UART frame |
| `MotionConfigHeader` / `MotionConfig` | `MotionConfig` | Binary header + JSON device configuration |
| `HistogramSample` | `MotionProcessing` | One camera's histogram for one frame |
| `RealtimeSample` | `MotionProcessing` | `HistogramSample` + mean / std / contrast / BFI / BVI |
| `CorrectedSample` | `MotionProcessing` | Dark-frame corrected BFI / BVI |
| `ScienceFrame` | `MotionProcessing` | All corrected samples for one aligned frame (both sides) |
| `ConsoleTelemetry` | `ConsoleTelemetry` | One snapshot of all console health data |
| `PDUMon` | `Console` | 16-channel ADC raw counts and scaled voltages |
| `TelemetrySample` | `Console` | Timestamped temperature + TEC ADC snapshot |
| `ScanRequest` / `ScanResult` | `ScanWorkflow` | Scan parameters and outcome |
| `JedecImage` | `jedecParser` | Parsed FPGA bitstream rows |
| `DFUProgress` / `DFUResult` | `DFUProgrammer` | Firmware flash progress and result |
