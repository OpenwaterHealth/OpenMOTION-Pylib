import json
import logging
import asyncio
import time
import queue
import struct
import csv
import threading
import usb.core
import usb.util
import serial
import serial.tools.list_ports

from .config import OW_CMD_NOP, OW_START_BYTE, OW_END_BYTE, OW_ACK, OW_RESP, OW_ERROR
from .utils import util_crc16

# Set up logging
log = logging.getLogger("UART")
log.setLevel(logging.INFO)  # or INFO depending on what you want to see

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
            print("Missing Start or End Byte Packet LEN " + str(len(buffer)))
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
        
    def __str__(self):
        return (
            f"UartPacket(id={self.id}, "
            f"type=0x{self.packet_type:02X}, "
            f"cmd=0x{self.command:02X}, "
            f"addr=0x{self.addr:02X}, "
            f"reserved=0x{self.reserved:02X}, "
            f"data_len={self.data_len}, "
            f"data={self.data.hex()}"
            f"crc=0x{self.crc:04X}"
    )

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
            try:
                slot(*args, **kwargs)
            except Exception as e:
                log.error("Signal emit error in slot %s: %s", slot, e)

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
        self.serial = None

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
            self.serial = None
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
            time.sleep(0.5)  # Short delay to avoid replug thrash
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
            bytes_waiting = 0
            try:
                if not self.serial or not self.serial.is_open:
                    log.warning("Serial port closed during read loop.")
                    self.running = False
                    break
                if self.serial.in_waiting > 0:
                    time.sleep(0.002)  # Brief sleep to avoid a busy loop
                    bytes_waiting = self.serial.in_waiting
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
                                    log.warning("Packet type: 0x%02X, Command: 0x%02X", packet.packet_type, packet.command)  
                        else:
                            self.signal_data_received.emit(self.descriptor, packet)

                    except ValueError as ve:
                        log.error(f"Data bytes {bytes_waiting}")
                        log.error("Error parsing packet: %s", ve)
                else:
                    time.sleep(0.01)  # Brief sleep to avoid a busy loop
            except serial.SerialException as se:
                self.running = False
            except Exception as e:
                log.error("Unexpected serial error on %s: %s", self.descriptor, e)
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

                if self.packet_count >= 0xFFFF:
                    self.packet_count = 1
                    
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

            # print("Sending packet: ", packet.hex())
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

class MotionComposite:
    
    _USB_TIMEOUT_MS = 100          # tweak once, used everywhere
    _PAUSE_POLL_S  = 0.01          # sleep while paused

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
        self.desc = desc
        self.read_thread = None
        self.dev = None
        self.last_rx = time.monotonic()
        self.read_buffer = bytearray()
        
        self.uart_interface = 0  # default interface index
        self.uart_ep_out = None
        self.uart_ep_in = None
        self.histo_ep_in = None
        self.histo_expected_size = 32833
        self.histo_queue = None

        self.histo_interface = 1  # default interface index
        self.histo_thread = None

        self.imu_interface = 2  # default interface index
        self.imu_thread = None

        self.stop_event = threading.Event()
        self.pause_event = threading.Event()

        # Signals: each signal emits (descriptor, port or data)
        self.signal_connect = MOTIONSignal()
        self.signal_disconnect = MOTIONSignal()
        self.signal_data_received = MOTIONSignal()

        self.attempt_count = 0

        if async_mode:
            self.loop = asyncio.get_event_loop()
            self.response_queues = {} 
            self.response_lock = threading.Lock()  # Lock for thread-safe access to response_queues

    def connect(self):
        if(self.is_connected()):
            return
        
        self.dev = usb.core.find(idVendor=self.vid, idProduct=self.pid)
        if self.dev is None:
            raise ValueError("Device not found")

        self.dev.set_configuration()
        cfg = self.dev.get_active_configuration()
        intf = cfg[(self.uart_interface, 0)]

        # Claim the interface
        usb.util.claim_interface(self.dev, self.uart_interface)

        # Assume first bulk OUT and IN
        self.uart_ep_out = usb.util.find_descriptor(
            intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)
        self.uart_ep_in = usb.util.find_descriptor(
            intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)

        if self.uart_ep_out is None or self.uart_ep_in is None:
            raise ValueError("Bulk endpoints not found")

        # self.imu_thread = threading.Thread(target=self._stream_imu_data, daemon=True)
        # self.imu_thread.start()

        if self.asyncMode:
            log.info("Starting read thread for %s.", self.desc)
            self.read_thread = threading.Thread(target=self._read_data)
            self.read_thread.daemon = True
            self.read_thread.start()

        self.running = True
        log.info(f'Connected to {self.desc} with VID: {self.vid}, PID: {self.pid}')
        self.signal_connect.emit(self.desc, "bulk_usb")

    def disconnect(self):
        self.running = False

        self.stop_event.set()
        if self.histo_thread:
            self.histo_thread.join()
        if self.imu_thread:
            self.imu_thread.join()

        if self.read_thread:
            self.read_thread.join()

        if self.dev:
            usb.util.release_interface(self.dev, self.uart_interface)
            usb.util.dispose_resources(self.dev)
        if self.uart_ep_in:
            self.uart_ep_in = None
        if self.uart_ep_out:
            self.uart_ep_out = None
        if self.histo_ep_in:
            self.histo_ep_in = None
        log.info(f'Disconnected from {self.desc} with VID: {self.vid}, PID: {self.pid}')
        self.signal_disconnect.emit(self.desc, "bulk_usb")

    def is_connected(self) -> bool:
        """
        Check if the device is connected.

        Returns:
            bool: True if connected, False otherwise.
        """
        if self.demo_mode:
            return True
        return (self.uart_ep_out is not None or self.uart_ep_in is not None)

    def check_usb_status(self):
        """Check if the USB device is connected or disconnected."""
        device = self.find_usb_bulk_device()
        if device and not self.running:
            log.debug("USB device connected.")
            try:
                self.connect()
            except Exception as e:
                log.error("Failed to connect to device: %s", e)
        elif not device and self.running:
            log.debug("USB device disconnected.")
            self.disconnect()

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
        print(f"Starting monitoring with interval {interval} seconds")
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

    def find_usb_bulk_device(self):
        """Find the USB device by VID and PID."""
        return usb.core.find(idVendor=self.vid, idProduct=self.pid)

    def _read_data(self, timeout=20):
        """Read data from the serial port in a separate thread."""
        log.debug("Starting data read loop for %s.", self.desc)
        if self.demo_mode:
            log.info("Demo mode: Simulating UART read NOT IMPLEMENTED.")
            return

        # In async mode, run the reading loop in a thread
        while self.running:
            try:
                packet = self.read_packet(timeout=timeout)
                if self.asyncMode:
                    with self.response_lock:
                        # Check if a queue is waiting for this packet ID.
                        if packet.id in self.response_queues:
                            self.response_queues[packet.id].put(packet)
                        else:
                            log.warning("Received an unsolicited packet with ID %d", packet.id)
                else:
                    self.signal_data_received.emit(self.desc, packet)                        

            except usb.core.USBError as e:
                if e.errno == 10060:  # Timeout
                    # log.debug("USB read timeout on %s: %s", self.desc, e)
                    continue
                else:
                    log.error("USB errno %d on %s: %s", e.errno, self.desc, e)
                    # log.error("USB read error on %s: %s", self.desc, e)
                continue
            except TimeoutError as te:
                continue
            except Exception as e:
                log.error("Unexpected USB error on %s: %s", self.desc, e)
                

    def _tx(self, data: bytes):
        """Send data over UART."""
        if self.uart_ep_in is None or self.uart_ep_out is None:
            log.error("Port is not available.")
            return
        if self.demo_mode:
            log.info("Demo mode: Simulating data transmission: %s", data)
            return
        try:
            if self.align > 0:
                while len(data) % self.align != 0:
                    data += bytes([OW_END_BYTE])
            self.dev.write(self.uart_ep_out.bEndpointAddress, data, timeout=self.timeout)
        except usb.core.USBError as e:
            log.error("USB write error on %s: %s", self.desc, e)
            raise e
        
    def read_packet(self, timeout=20) -> UartPacket:
        """
        Attempt to read a complete UartPacket from the USB read buffer.

        Returns:
            UartPacket: Parsed packet or raises on failure.
        """
        start_time = time.monotonic()
        self.read_buffer.clear()
        expected_length = None
        
        while (time.monotonic() - start_time) < timeout:
            # Read with short timeout for each chunk
            data = self.dev.read(self.uart_ep_in.bEndpointAddress, 
                            self.uart_ep_in.wMaxPacketSize, 
                            timeout=100)  # ms
            
            if data:
                self.read_buffer.extend(data)
                
                # If we have enough data to parse length header
                if len(self.read_buffer) >= 4 and expected_length is None:
                    # Parse your packet header to get expected length
                    # Example: first 4 bytes = length (adjust for your protocol)
                    expected_length = int.from_bytes(self.read_buffer[:4], 'little')
                
                # If we know expected length and have all data
                if expected_length and len(self.read_buffer) >= expected_length:
                    break
            else:
                # Only break if we have some data and no more is coming
                if len(self.read_buffer) > 0:
                    break
        
        if not self.read_buffer:
            raise TimeoutError("No data received")
        try: 
            packet = UartPacket(buffer=self.read_buffer)
        except Exception as e:
            log.error("Error parsing packet: %s", e)
            # Return an error packet
            packet = UartPacket(
                id=0,
                packet_type=OW_ERROR,
                command=0,
                addr=0,
                reserved=0,
                data=[]
            )
        return packet

    def send_packet(self, id=None, packetType=OW_ACK, command=OW_CMD_NOP, addr=0, reserved=0, data=None, timeout=20):
        """
        Send a packet over UART and, if not running, return a response packet.
        """

        try:
            if self.uart_ep_in is None:
                log.error("Cannot send packet. TX Port is not available.")
                return None

            if id is None:
                self.packet_count += 1
                
                if self.packet_count >= 0xFFFF:
                    self.packet_count = 1

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

            print("Sending packet: ", packet.hex())
            self.pause_event.set()
            self._tx(packet)
            time.sleep(0.0001)
            
            if not self.asyncMode:
                packet = self.read_packet(timeout=timeout)
                time.sleep(0.0001)
                self.pause_event.clear()
                return packet
            else:
                self.pause_event.clear()
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

    def print(self):
        """Print the current UART configuration."""
        log.info("    Serial Port: %s", self.port)
        log.info("    Serial Baud: %s", self.baudrate)
    
    def clear_buffer(self):
        """Clear the read buffer."""
        self.read_buffer = []

    def histo_thread_func(self) -> None:
        """
        Claim the interface and start draining the histogram endpoint into
        *out_path*.  The outer loop exits when:
            • stop_event is set,
            • a USB stall/IO error persists after one recovery attempt, or
            • the device sends a “short packet” (protocol end‑marker).
        """
        try:            
            cfg = self.dev.get_active_configuration()
            histo_intf = cfg[(self.histo_interface, 0)]
            usb.util.claim_interface(self.dev, self.histo_interface)
            for ep in histo_intf:
                if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN:
                    self.histo_ep_in = ep
                    break

            while not self.stop_event.is_set():
                try:
                    data = self.dev.read(self.histo_ep_in.bEndpointAddress, self.histo_expected_size, timeout=100)
                    if(data):
                        if self.histo_queue:
                            self.histo_queue.put(bytes(data))

                except usb.core.USBError as e:
                    if e.errno != 110:
                        print(f"[HISTO] USB error: {e}")
                    time.sleep(0.01)

        finally:
            usb.util.release_interface(self.dev, self.histo_interface)
            usb.util.dispose_resources(self.dev)
            print("\nStopped HISTO read thread.")
            
    def start_histo_thread(self, expected_frame_size, histo_queue:queue):
        if self.histo_thread and self.histo_thread.is_alive():
            print("HISTO thread already running.")
            return
        self.histo_queue = histo_queue
        self.histo_expected_size = expected_frame_size  # Store for the thread to use
        self.stop_event.clear()
        self.histo_thread = threading.Thread(target=self.histo_thread_func, daemon=True)
        self.histo_thread.start()

    def stop_histo_thread(self):
        self.stop_event.set()
        if self.histo_thread:
            self.histo_thread.join()

    def _stream_imu_data(self):
        try:
            usb.util.claim_interface(self.dev, 2)
            while not self.stop_event.is_set():
                try:
                    data = self.dev.read(0x83, 512, timeout=100)
                    for line in data.splitlines():
                        try:
                            parsed = json.loads(line)
                            print("[IMU] JSON:", parsed)
                        except json.JSONDecodeError:
                            print("[IMU] Invalid JSON:", line)
                except usb.core.USBError as e:
                    if e.errno != 110:
                        print("[IMU] USB error:", e)
        finally:
            try:
                usb.util.release_interface(self.dev, 2)
            except:
                pass
