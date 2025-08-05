import logging
import asyncio
import time
import queue
import threading
import logging
import asyncio
import serial
import serial.tools.list_ports

from omotion.UartPacket import UartPacket
from omotion.signal_wrapper import SignalWrapper, PYQT_AVAILABLE
from omotion.config import OW_CMD_NOP, OW_START_BYTE, OW_END_BYTE, OW_ACK, OW_RESP, OW_ERROR
from omotion.utils import util_crc16

# Set up logging
logger = logging.getLogger("UART")
logger.setLevel(logging.INFO)  # or INFO depending on what you want to see

class MOTIONUart(SignalWrapper):
    def __init__(self, vid, pid, baudrate=921600, timeout=10, align=0, async_mode=False, demo_mode=False, desc="VCP"):
        super().__init__() 
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

        if async_mode:
            self.loop = asyncio.get_event_loop()
            self.response_queues = {} 
            self.response_lock = threading.Lock()  # Lock for thread-safe access to response_queues

    def connect(self):
        """Open the serial port."""
        if self.demo_mode:
            logger.info("Demo mode: Simulating UART connection.")
            self.signal_connect.emit(self.descriptor, "demo_mode")
            return
        try:
            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout
            )

            logger.info("Connected to UART on port %s.", self.port)
            self.signal_connect.emit(self.descriptor, self.port)

            if self.asyncMode:
                logger.info("Starting read thread for %s.", self.descriptor)
                self.running = True
                self.read_thread = threading.Thread(target=self._read_data)
                self.read_thread.daemon = True
                self.read_thread.start()
        except serial.SerialException as se:
            logger.error("Failed to connect to %s: %s", self.port, se)
            self.serial = None
            self.running = False
            self.port = None
        except Exception as e:
            raise e

    def disconnect(self):
        """Close the serial port."""
        self.running = False
        if self.demo_mode:
            logger.info("Demo mode: Simulating UART disconnection.")
            self.signal_disconnect.emit(self.descriptor, "demo_mode")
            return

        if self.read_thread:
            self.read_thread.join()
        if self.serial and self.serial.is_open:
            self.serial.close()
            self.serial = None
        logger.info("Disconnected from UART.")
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
            logger.debug("Device found; trying to connect.")
            self.port = device
            self.connect()
        elif not device and self.port:
            logger.debug("Device removed; disconnecting.")
            self.running = False
            time.sleep(0.5)  # Short delay to avoid replug thrash
            self.disconnect()
            self.port = None

    async def monitor_usb_status(self, interval=1):
        """Periodically check for USB device connection."""
        if self.demo_mode:
            logger.debug("Monitoring in demo mode.")
            self.connect()
            return
        while True:
            self.check_usb_status()
            await asyncio.sleep(interval)

    def start_monitoring(self, interval=1):
        """Start the periodic USB device connection check."""
        if self.demo_mode:
            logger.debug("Monitoring in demo mode.")
            return
        if not self.monitoring_task and self.asyncMode:
            self.monitoring_task = asyncio.create_task(self.monitor_usb_status(interval))

    def stop_monitoring(self):
        """Stop the periodic USB device connection check."""
        if self.demo_mode:
            logger.info("Monitoring in demo mode.")
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
        logger.debug("Starting data read loop for %s.", self.descriptor)
        if self.demo_mode:
            logger.info("Demo mode: Simulating UART read NOT IMPLEMENTED.")
            return

        # In async mode, run the reading loop in a thread
        while self.running:
            bytes_waiting = 0
            try:
                if not self.serial or not self.serial.is_open:
                    logger.warning("Serial port closed during read loop.")
                    self.running = False
                    break
                if self.serial.in_waiting > 0:
                    time.sleep(0.002)  # Brief sleep to avoid a busy loop
                    bytes_waiting = self.serial.in_waiting
                    data = self.serial.read(self.serial.in_waiting)
                    self.read_buffer.extend(data)
                    
                    logger.info("Data received on %s: %s", self.descriptor, data)
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
                                    logger.warning("Received an unsolicited packet with ID %d", packet.id)
                                    logger.warning("Packet type: 0x%02X, Command: 0x%02X", packet.packet_type, packet.command)  
                        else:
                            self.signal_data_received.emit(self.descriptor, packet)

                    except ValueError as ve:
                        logger.error(f"Data bytes {bytes_waiting}")
                        logger.error("Error parsing packet: %s", ve)
                else:
                    time.sleep(0.01)  # Brief sleep to avoid a busy loop
            except serial.SerialException as se:
                self.running = False
            except Exception as e:
                logger.error("Unexpected serial error on %s: %s", self.descriptor, e)
                self.running = False

    def _tx(self, data: bytes):
        """Send data over UART."""
        if not self.serial or not self.serial.is_open:
            logger.error("Serial port is not initialized.")
            return
        if self.demo_mode:
            logger.info("Demo mode: Simulating data transmission: %s", data)
            return
        try:
            if self.align > 0:
                while len(data) % self.align != 0:
                    data += bytes([OW_END_BYTE])
            self.serial.write(data)
        except Exception as e:
            logger.error("Error during transmission: %s", e)
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
            logger.error("Error parsing packet: %s", e)
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
                logger.error("Cannot send packet. Serial port is not connected.")
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
            time.sleep(0.0001)
            
            if not self.asyncMode:
                ret_packet = self.read_packet(timeout=timeout)
                time.sleep(0.0001)            
                return ret_packet
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
                        logger.error("Received unexpected response: %s", response)
                        return response
                except queue.Empty:
                    logger.error("Timeout waiting for response to packet ID %d", id)
                    return None
                finally:
                    with self.response_lock:
                        # Clean up the queue entry regardless of outcome.
                        self.response_queues.pop(id, None)

        except ValueError as ve:
            logger.error("Validation error in send_packet: %s", ve)
            raise
        except Exception as e:
            logger.error("Unexpected error in send_packet: %s", e)
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
        logger.info("    Serial Port: %s", self.port)
        logger.info("    Serial Baud: %s", self.baudrate)
