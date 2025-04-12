import json
import logging
import asyncio
import time
import queue
import struct
import csv
import threading

import serial
import serial.tools.list_ports

from .config import OW_CMD_NOP, OW_START_BYTE, OW_END_BYTE, OW_ACK, OW_RESP, OW_ERROR
from .utils import util_crc16

# Set up logging
log = logging.getLogger("UART")

class UartPacket:
    def __init__(self, id=None, packet_type=None, command=None, addr=None, reserved=None, data=[], buffer=None):
        if buffer:
            self.from_buffer(buffer)
        else:
            self.id = id
            self.packet_type = packet_type
            self.command = command
            self.addr = addr
            self.reserved = reserved
            self.data = data
            self.data_len = len(data)
            self.crc = self.calculate_crc()

    def calculate_crc(self) -> int:
        crc_value = 0xFFFF
        packet = bytearray()
        packet.append(OW_START_BYTE)
        packet.extend(self.id.to_bytes(2, 'big'))
        packet.append(self.packet_type)
        packet.append(self.command)
        packet.append(self.addr)
        packet.append(self.reserved)
        packet.extend(self.data_len.to_bytes(2, 'big'))
        if self.data_len > 0:
            packet.extend(self.data)
        crc_value = util_crc16(packet[1:])
        return crc_value

    def to_bytes(self) -> bytes:
        buffer = bytearray()
        buffer.append(OW_START_BYTE)
        buffer.extend(self.id.to_bytes(2, 'big'))
        buffer.append(self.packet_type)
        buffer.append(self.command)
        buffer.append(self.addr)
        buffer.append(self.reserved)
        buffer.extend(self.data_len.to_bytes(2, 'big'))
        if self.data_len > 0:
            buffer.extend(self.data)
        crc_value = util_crc16(buffer[1:])
        buffer.extend(crc_value.to_bytes(2, 'big'))
        buffer.append(OW_END_BYTE)
        return bytes(buffer)

    def from_buffer(self, buffer: bytes):
        if buffer[0] != OW_START_BYTE or buffer[-1] != OW_END_BYTE:
            print("length" + str(len(buffer)))
            print(buffer)
            raise ValueError("Invalid buffer format")

        self.id = int.from_bytes(buffer[1:3], 'big')
        self.packet_type = buffer[3]
        self.command = buffer[4]
        self.addr = buffer[5]
        self.reserved = buffer[6]
        self.data_len = int.from_bytes(buffer[7:9], 'big')
        self.data = bytearray(buffer[9:9+self.data_len])
        crc_value = util_crc16(buffer[1:9+self.data_len])
        self.crc = int.from_bytes(buffer[9+self.data_len:11+self.data_len], 'big')
        if self.crc != crc_value:
            print("Packet CRC: " + str(self.crc) + ", Calculated CRC: " + str(crc_value) )
            raise ValueError("CRC mismatch")

    def print_packet(self,full=False):
        print("UartPacket:")
        print("  Packet ID:", self.id)
        print("  Packet Type:", hex(self.packet_type))
        print("  Command:", hex(self.command))
        print("  Data Length:", self.data_len)
        if(full):
            print("  Address:", hex(self.addr))
            print("  Reserved:", hex(self.reserved))
            print("  Data:", self.data.hex())
            print("  CRC:", hex(self.crc))
        

class MOTIONSignal:
    def __init__(self):
        # Initialize a list to store connected slots (callback functions)
        self._slots = []

    def connect(self, slot):
        """
        Connect a slot (callback function) to the signal.

        Args:
            slot (callable): A callable to be invoked when the signal is emitted.
        """
        if callable(slot) and slot not in self._slots:
            self._slots.append(slot)

    def disconnect(self, slot):
        """
        Disconnect a slot (callback function) from the signal.

        Args:
            slot (callable): The callable to disconnect.
        """
        if slot in self._slots:
            self._slots.remove(slot)

    def emit(self, *args, **kwargs):
        """
        Emit the signal, invoking all connected slots.

        Args:
            *args: Positional arguments to pass to the connected slots.
            **kwargs: Keyword arguments to pass to the connected slots.
        """
        for slot in self._slots:
            slot(*args, **kwargs)

class MOTIONUart:
    def __init__(self, vid, pid, baudrate=921600, timeout=10, align=0, async_mode=False, demo_mode=False, desc="VCP"):
        self.vid = vid
        self.pid = pid
        self.port = None
        self.baudrate = baudrate
        self.timeout = timeout
        self.align = align
        self.packet_count = 0
        self.asyncMode = async_mode
        self.running = False
        self.monitoring_task = None
        self.demo_mode = demo_mode
        self.descriptor = desc
        self.read_thread = None
        self.last_rx = time.monotonic()
        self.read_buffer = []

        # Signals: each signal emits (descriptor, port or data)
        self.signal_connect = MOTIONSignal()
        self.signal_disconnect = MOTIONSignal()
        self.signal_data_received = MOTIONSignal()

        if async_mode:
            self.loop = asyncio.get_event_loop()
            self.response_queues = {} 
            self.response_lock = threading.Lock()  # Lock for thread-safe access to response_queues

    def connect(self):
        """Open the serial port."""
        if self.demo_mode:
            log.info("Demo mode: Simulating UART connection.")
            self.signal_connect.emit(self.descriptor, "demo_mode")
            return
        try:
            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout
            )
            log.info("Connected to UART on port %s.", self.port)
            self.signal_connect.emit(self.descriptor, self.port)

            if self.asyncMode:
                log.info("Starting read thread for %s.", self.descriptor)
                self.running = True
                self.read_thread = threading.Thread(target=self._read_data)
                self.read_thread.daemon = True
                self.read_thread.start()
        except serial.SerialException as se:
            log.error("Failed to connect to %s: %s", self.port, se)
            self.running = False
            self.port = None
        except Exception as e:
            raise e

    def disconnect(self):
        """Close the serial port."""
        self.running = False
        if self.demo_mode:
            log.info("Demo mode: Simulating UART disconnection.")
            self.signal_disconnect.emit(self.descriptor, "demo_mode")
            return

        if self.read_thread:
            self.read_thread.join()
        if self.serial and self.serial.is_open:
            self.serial.close()
            self.serial = None
        log.info("Disconnected from UART.")
        self.signal_disconnect.emit(self.descriptor, self.port)
        self.port = None

    def is_connected(self) -> bool:
        """
        Check if the device is connected.

        Returns:
            bool: True if connected, False otherwise.
        """
        if self.demo_mode:
            return True
        return self.port is not None and self.serial is not None and self.serial.is_open

    def check_usb_status(self):
        """Check if the USB device is connected or disconnected."""
        device = self.list_vcp_with_vid_pid()
        if device and not self.port:
            log.debug("Device found; trying to connect.")
            self.port = device
            self.connect()
        elif not device and self.port:
            log.debug("Device removed; disconnecting.")
            self.running = False
            self.disconnect()
            self.port = None

    async def monitor_usb_status(self, interval=1):
        """Periodically check for USB device connection."""
        if self.demo_mode:
            log.debug("Monitoring in demo mode.")
            self.connect()
            return
        while True:
            self.check_usb_status()
            await asyncio.sleep(interval)

    def start_monitoring(self, interval=1):
        """Start the periodic USB device connection check."""
        if self.demo_mode:
            log.debug("Monitoring in demo mode.")
            return
        if not self.monitoring_task and self.asyncMode:
            self.monitoring_task = asyncio.create_task(self.monitor_usb_status(interval))

    def stop_monitoring(self):
        """Stop the periodic USB device connection check."""
        if self.demo_mode:
            log.info("Monitoring in demo mode.")
            return
        if self.monitoring_task:
            self.monitoring_task.cancel()
            self.monitoring_task = None

    def list_vcp_with_vid_pid(self):
        """Find the USB device by VID and PID."""
        ports = serial.tools.list_ports.comports()
        for port in ports:
            if hasattr(port, 'vid') and hasattr(port, 'pid') and port.vid == self.vid and port.pid == self.pid:
                return port.device
        return None

    def _read_data(self, timeout=20):
        """Read data from the serial port in a separate thread."""
        log.debug("Starting data read loop for %s.", self.descriptor)
        if self.demo_mode:
            log.info("Demo mode: Simulating UART read NOT IMPLEMENTED.")
            return

        # In async mode, run the reading loop in a thread
        while self.running:
            try:
                if self.serial.in_waiting > 0:
                    data = self.serial.read(self.serial.in_waiting)
                    self.read_buffer.extend(data)
                    log.info("Data received on %s: %s", self.descriptor, data)
                    # Attempt to parse a complete packet from read_buffer.
                    try:
                        # Note: Depending on your protocol, you might need to check for start/end bytes
                        # and possibly handle partial packets.
                        packet = UartPacket(buffer=bytes(self.read_buffer))
                        # Clear the buffer after a successful parse.

                        self.read_buffer = []
                        if self.asyncMode:
                            with self.response_lock:
                                # Check if a queue is waiting for this packet ID.
                                if packet.id in self.response_queues:
                                    self.response_queues[packet.id].put(packet)
                                else:
                                    log.warning("Received an unsolicited packet with ID %d", packet.id)
                        else:
                            self.signal_data_received.emit(self.descriptor, packet)

                    except ValueError as ve:
                        log.error("Error parsing packet: %s", ve)
                else:
                    time.sleep(0.05)  # Brief sleep to avoid a busy loop
            except serial.SerialException as e:
                log.error("Serial _read_data error on %s: %s", self.descriptor, e)
                self.running = False

    def _tx(self, data: bytes):
        """Send data over UART."""
        if not self.serial or not self.serial.is_open:
            log.error("Serial port is not initialized.")
            return
        if self.demo_mode:
            log.info("Demo mode: Simulating data transmission: %s", data)
            return
        try:
            if self.align > 0:
                while len(data) % self.align != 0:
                    data += bytes([OW_END_BYTE])
            self.serial.write(data)
        except Exception as e:
            log.error("Error during transmission: %s", e)
            raise e

    def read_packet(self, timeout=20) -> UartPacket:
        """
        Read a packet from the UART interface.

        Returns:
            UartPacket: Parsed packet or an error packet if parsing fails.
        """
        start_time = time.monotonic()
        raw_data = b""
        count = 0

        while timeout == -1 or time.monotonic() - start_time < timeout:
            time.sleep(0.05)
            raw_data += self.serial.read_all()
            if raw_data:
                count += 1
                if count > 1:
                    break

        try:
            if not raw_data:
                raise ValueError("No data received from UART within timeout")
            packet = UartPacket(buffer=raw_data)
        except Exception as e:
            log.error("Error parsing packet: %s", e)
            packet = UartPacket(
                id=0,
                packet_type=OW_ERROR,
                command=0,
                addr=0,
                reserved=0,
                data=[]
            )
            raise e

        return packet

    def send_packet(self, id=None, packetType=OW_ACK, command=OW_CMD_NOP, addr=0, reserved=0, data=None, timeout=20):
        """
        Send a packet over UART and, if not running, return a response packet.
        """

        try:
            if not self.serial or not self.serial.is_open:
                log.error("Cannot send packet. Serial port is not connected.")
                return None

            if id is None:
                self.packet_count += 1
                id = self.packet_count

            if data:
                if not isinstance(data, (bytes, bytearray)):
                    raise ValueError("Data must be bytes or bytearray")
                payload = data
                payload_length = len(payload)
            else:
                payload_length = 0
                payload = b''

            packet = bytearray()
            packet.append(OW_START_BYTE)
            packet.extend(id.to_bytes(2, 'big'))
            packet.append(packetType)
            packet.append(command)
            packet.append(addr)
            packet.append(reserved)
            packet.extend(payload_length.to_bytes(2, 'big'))
            if payload_length > 0:
                packet.extend(payload)

            crc_value = util_crc16(packet[1:])  # Exclude start byte
            packet.extend(crc_value.to_bytes(2, 'big'))
            packet.append(OW_END_BYTE)

            self._tx(packet)

            if not self.asyncMode:
                return self.read_packet(timeout=timeout)
            else:
                response_queue = queue.Queue()
                with self.response_lock:
                    self.response_queues[id] = response_queue

                try:
                    # Wait for a response that matches the packet ID.
                    response = response_queue.get(timeout=timeout)
                    # Optionally, check that the response has the expected type and command.
                    if response.packet_type == OW_RESP and response.command == command:
                        return response
                    else:
                        log.error("Received unexpected response: %s", response)
                        return response
                except queue.Empty:
                    log.error("Timeout waiting for response to packet ID %d", id)
                    return None
                finally:
                    with self.response_lock:
                        # Clean up the queue entry regardless of outcome.
                        self.response_queues.pop(id, None)

        except ValueError as ve:
            log.error("Validation error in send_packet: %s", ve)
            raise
        except Exception as e:
            log.error("Unexpected error in send_packet: %s", e)
            raise

    def clear_buffer(self):
        """Clear the read buffer."""
        self.read_buffer = []

    def run_coroutine(self, coro):
        """Run a coroutine using the internal event loop."""
        if not self.loop.is_running():
            return self.loop.run_until_complete(coro)
        else:
            return asyncio.create_task(coro)
               
    def print(self):
        """Print the current UART configuration."""
        log.info("    Serial Port: %s", self.port)
        log.info("    Serial Baud: %s", self.baudrate)
