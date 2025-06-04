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
        
    def __str__(self):
        return (
            f"UartPacket(id={self.id}, "
            f"type=0x{self.packet_type:02X}, "
            f"cmd=0x{self.command:02X}, "
            f"addr=0x{self.addr:02X}, "
            f"reserved=0x{self.reserved:02X}, "
            f"data_len={self.data_len}, "
            f"data={self.data.hex()})"
            f"crc=0x{self.crc:04X})"
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
        self.desc = desc
        self.read_thread = None
        self.dev = None
        self.last_rx = time.monotonic()
        self.read_buffer = bytearray()
        
        self.uart_interface = 0  # default interface index
        self.uart_ep_out = None
        self.uart_ep_in = None

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

        if async_mode:
            self.loop = asyncio.get_event_loop()
            self.response_queues = {} 
            self.response_lock = threading.Lock()  # Lock for thread-safe access to response_queues

    def connect(self):
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

        # Set up HISTO interface
        histo_intf = cfg[(self.histo_interface, 0)]
        self.histo_ep_in = usb.util.find_descriptor(
            histo_intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)

        # Start background threads
        self.stop_event.clear()
        self.histo_thread = threading.Thread(target=self._stream_histo_data, daemon=True)
        self.histo_thread.start()

        # self.imu_thread = threading.Thread(target=self._stream_imu_data, daemon=True)
        # self.imu_thread.start()

#        self.running = True
#        self.read_thread = threading.Thread(target=self._read_loop)
#        self.read_thread.daemon = True
#        self.read_thread.start()

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
        log.debug("Starting data read loop for %s.", self.descriptor)
        if self.demo_mode:
            log.info("Demo mode: Simulating UART read NOT IMPLEMENTED.")
            return

        # In async mode, run the reading loop in a thread
        while self.running:
            try:
                if self.uart_ep_out is None:
                    log.warning("RX port not available.")
                    self.running = False
                    break
                else:
                    data = self.dev.read(self.uart_ep_in.bEndpointAddress, self.uart_ep_in.wMaxPacketSize, timeout=self.timeout)
                    self.read_buffer.extend(data)
                    log.info("Data received on %s: %s", self.descriptor, data)
                    # Attempt to parse a complete packet from read_buffer.
                    try:
                        packet = UartPacket(buffer=self.read_buffer)
                        self.read_buffer.clear()
                        

                        if self.asyncMode:
                            with self.response_lock:
                                # Check if a queue is waiting for this packet ID.
                                if packet.id in self.response_queues:
                                    self.response_queues[packet.id].put(packet)
                                else:
                                    log.warning("Received an unsolicited packet with ID %d", packet.id)
                        else:
                            self.signal_data_received.emit(self.descriptor, packet)

                    except ValueError:
                        # Incomplete packet, keep accumulating
                        pass
            except usb.core.USBError as e:
                if e.errno == 110:  # Timeout
                    continue
                print("USB read error:", e)
                break

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
            print("USB write error:", e)
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
        
        return UartPacket(buffer=self.read_buffer)

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

            if not self.asyncMode:
                packet = self.read_packet(timeout=timeout)
                self.pause_event.clear()
                return packet
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


    def read_usb_stream(self, dev, endpoint, endpoint_size, timeout=100):
        data = bytearray()
        while True:
            try:
                chunk = dev.read(endpoint, endpoint_size, timeout=timeout)
                data.extend(chunk)
                # If packet is shorter than max size, it's the end
                if len(chunk) < endpoint_size:
                    break
            except usb.core.USBError as e:
                print(f"USB read error: {e}")
                break
        return data

    def _stream_histo_data(self):
        usb.util.claim_interface(self.dev, self.histo_interface)
        with open("histogram.bin","wb") as binary_file:
            binary_file.write(bytearray())
        #TODO(remove the file writing from this and add in a proper handler)
        try:
            while not self.stop_event.is_set():
                if self.pause_event.is_set():
                    continue
                data = self.read_usb_stream(self.dev, self.histo_ep_in.bEndpointAddress, self.histo_ep_in.wMaxPacketSize*4)               
                if data:
                    with open("histogram.bin", "ab") as binary_file:
                        binary_file.write(data)
                time.sleep(0.012)
        except Exception as e:
            print(f"[HISTO] Exception: {e}")
        finally:
            usb.util.release_interface(self.dev, 1)

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
