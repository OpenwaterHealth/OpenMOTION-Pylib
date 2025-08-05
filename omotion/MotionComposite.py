# MotionComposite.py
import logging
import usb.core
import usb.util
import time
import threading
import queue
import json
import asyncio
from omotion.utils import util_crc16
from omotion.signal_wrapper import SignalWrapper
from omotion.config import OW_START_BYTE, OW_END_BYTE, OW_ERROR, OW_RESP, OW_CMD_NOP, OW_ACK, OW_ERROR, OW_RESP, OW_CMD_NOP, OW_END_BYTE, OW_START_BYTE, OW_ACK, OW_CMD_NOP, OW_RESP, OW_ERROR
from .MotionUart import UartPacket  # Import UartPacket from core.py or adjust import as needed

logger = logging.getLogger("Composite")
logger.setLevel(logging.INFO)

class MotionComposite(SignalWrapper):
    
    _USB_TIMEOUT_MS = 100          # tweak once, used everywhere
    _PAUSE_POLL_S  = 0.01          # sleep while paused

    def __init__(self, vid, pid, timeout=10, align=0, async_mode=False, demo_mode=False, desc="COMPOSITE"):
        super().__init__() 
        self.vid = vid
        self.pid = pid
        self.timeout = timeout
        self.align = align
        self.packet_count = 0
        self.asyncMode = async_mode
        self.running = False
        self.monitoring_task = None
        self.desc = desc
        self.demo_mode = demo_mode
        self.read_thread = None
        self.dev = None
        self.read_buffer = bytearray()
        
        self.comm_interface = 0  # default interface index
        self.right_comm_ep_in = None
        self.right_comm_ep_out = None
        self.left_comm_ep_in = None
        self.left_comm_ep_out = None

        self.histo_interface = 1  # default interface index
        self.histo_ep_in = None
        self.histo_expected_size = 32833
        self.histo_queue = None
        self.histo_thread = None

        self.imu_interface = 2  # default interface index
        self.imu_thread = None

        self.right_stop_event = threading.Event()
        self.left_stop_event = threading.Event()
        
        self.right_pause_event = threading.Event()
        self.left_pause_event= threading.Event()

        self.attempt_count = 0

        if async_mode:
            self.loop = threading.get_ident()  # Placeholder, replace with asyncio.get_event_loop() if needed
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
        intf = cfg[(self.comm_interface, 0)]

        # Claim the interface
        usb.util.claim_interface(self.dev, self.comm_interface)

        # Assume first bulk OUT and IN
        self.right_comm_ep_out = usb.util.find_descriptor(
            intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)
        self.right_comm_ep_in = usb.util.find_descriptor(
            intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)

        if self.right_comm_ep_out is None or self.right_comm_ep_in is None:
            raise ValueError("Bulk endpoints not found")

        # self.imu_thread = threading.Thread(target=self._stream_imu_data, daemon=True)
        # self.imu_thread.start()

        if self.asyncMode:
            logger.info("Starting read thread for %s.", self.desc)
            self.read_thread = threading.Thread(target=self._read_data)
            self.read_thread.daemon = True
            self.read_thread.start()

        self.running = True
        logger.info(f'Connected to {self.desc} with VID: {self.vid}, PID: {self.pid}')
        self.signal_connect.emit(self.desc, "bulk_usb")

    def disconnect(self):
        self.running = False

        self.right_stop_event .set()
        if self.histo_thread:
            self.histo_thread.join()
        if self.imu_thread:
            self.imu_thread.join()

        if self.read_thread:
            self.read_thread.join()

        if self.dev:
            usb.util.release_interface(self.dev, self.comm_interface)
            usb.util.dispose_resources(self.dev)
        if self.right_comm_ep_in:
            self.right_comm_ep_in = None
        if self.right_comm_ep_out:
            self.right_comm_ep_out = None
        if self.histo_ep_in:
            self.histo_ep_in = None
        logger.info(f'Disconnected from {self.desc} with VID: {self.vid}, PID: {self.pid}')
        self.signal_disconnect.emit(self.desc, "bulk_usb")

    def is_connected(self) -> bool:
        """
        Check if the device is connected.

        Returns:
            bool: True if connected, False otherwise.
        """
        if self.demo_mode:
            return True
        return (self.right_comm_ep_out is not None or self.right_comm_ep_in is not None)

    def check_usb_status(self):
        """Check if the USB device is connected or disconnected."""
        device = self.find_usb_bulk_device()
        if device and not self.running:
            logger.debug("USB device connected.")
            try:
                self.connect()
            except Exception as e:
                logger.error("Failed to connect to device: %s", e)
        elif not device and self.running:
            logger.debug("USB device disconnected.")
            self.disconnect()

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
        logger.info(f"Starting monitoring with interval {interval} seconds")
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

    def find_usb_bulk_device(self):
        """Find the USB device by VID and PID."""
        return usb.core.find(idVendor=self.vid, idProduct=self.pid)

    def _read_data(self, timeout=20):
        """Read data from the serial port in a separate thread."""
        logger.debug("Starting data read loop for %s.", self.desc)
        if self.demo_mode:
            logger.info("Demo mode: Simulating UART read NOT IMPLEMENTED.")
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
                            logger.warning("Received an unsolicited packet with ID %d", packet.id)
                else:
                    self.signal_data_received.emit(self.desc, packet)                        

            except usb.core.USBError as e:
                if e.errno == 10060:  # Timeout
                    # logger.debug("USB read timeout on %s: %s", self.desc, e)
                    continue
                else:
                    logger.error("USB errno %d on %s: %s", e.errno, self.desc, e)
                    # logger.error("USB read error on %s: %s", self.desc, e)
                continue
            except TimeoutError as te:
                continue
            except Exception as e:
                logger.error("Unexpected USB error on %s: %s", self.desc, e)
                

    def _tx(self, data: bytes):
        """Send data over UART."""
        if self.right_comm_ep_in is None or self.right_comm_ep_out is None:
            logger.error("Port is not available.")
            return
        if self.demo_mode:
            logger.info("Demo mode: Simulating data transmission: %s", data)
            return
        try:
            if self.align > 0:
                while len(data) % self.align != 0:
                    data += bytes([OW_END_BYTE])
            self.dev.write(self.right_comm_ep_out.bEndpointAddress, data, timeout=self.timeout)
        except usb.core.USBError as e:
            logger.error("USB write error on %s: %s", self.desc, e)
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
            data = self.dev.read(self.right_comm_ep_in.bEndpointAddress, 
                            self.right_comm_ep_in.wMaxPacketSize, 
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
            logger.error("Error parsing packet: %s", e)
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
            if self.right_comm_ep_in is None:
                logger.error("Cannot send packet. TX Port is not available.")
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

            logger.debug("Sending packet: ", packet.hex())
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

    def print(self):
        """Print the current UART configuration."""
        print(f"UART Configuration: VID={self.vid}, PID={self.pid}, "
              f"Timeout={self.timeout}, Align={self.align}, AsyncMode={self.asyncMode}, "
              f"Desc={self.desc}")
    
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

            while not self.right_stop_event .is_set():
                try:
                    data = self.dev.read(self.histo_ep_in.bEndpointAddress, self.histo_expected_size, timeout=100)
                    if(data):
                        if self.histo_queue:
                            self.histo_queue.put(bytes(data))

                except usb.core.USBError as e:
                    if e.errno != 110:
                        logger.error(f"[HISTO] USB error: {e}")
                    time.sleep(0.01)

        finally:
            usb.util.release_interface(self.dev, self.histo_interface)
            usb.util.dispose_resources(self.dev)
            logger.info("Stopped HISTO read thread.")
            
    def start_histo_thread(self, expected_frame_size, histo_queue:queue):
        if self.histo_thread and self.histo_thread.is_alive():
            logger.info("HISTO thread already running.")
            return
        self.histo_queue = histo_queue
        self.histo_expected_size = expected_frame_size  # Store for the thread to use
        self.right_stop_event .clear()
        self.histo_thread = threading.Thread(target=self.histo_thread_func, daemon=True)
        self.histo_thread.start()

    def stop_histo_thread(self):
        self.right_stop_event .set()
        if self.histo_thread:
            self.histo_thread.join()

    def _stream_imu_data(self):
        try:
            usb.util.claim_interface(self.dev, 2)
            while not self.right_stop_event .is_set():
                try:
                    data = self.dev.read(0x83, 512, timeout=100)
                    for line in data.splitlines():
                        try:
                            parsed = json.loads(line)
                            logger.debug("[IMU] JSON:", parsed)
                        except json.JSONDecodeError:
                            logger.error("[IMU] Invalid JSON:", line)
                except usb.core.USBError as e:
                    if e.errno != 110:
                        logger.error("[IMU] USB error:", e)
        finally:
            try:
                usb.util.release_interface(self.dev, 2)
            except:
                pass
