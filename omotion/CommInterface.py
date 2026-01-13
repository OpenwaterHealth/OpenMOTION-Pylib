import logging
from omotion.UartPacket import UartPacket
from omotion.config import OW_ACK, OW_CMD_NOP, OW_END_BYTE, OW_START_BYTE, OW_RESP
from omotion.utils import util_crc16
import usb.core
import usb.util
import time
import threading
import queue
from omotion.usb_backend import get_libusb1_backend
from omotion.USBInterfaceBase import USBInterfaceBase
from omotion import _log_root

logger = logging.getLogger(f"{_log_root}.CommInterface" if _log_root else "CommInterface")

# =========================================
# Comm Interface (IN + OUT + threads)
# =========================================
class CommInterface(USBInterfaceBase):
    def __init__(self, dev, interface_index, desc="Comm", async_mode=False):
        super().__init__(dev, interface_index, desc)
        self.read_thread = None
        self.stop_event = threading.Event()
        self.read_queue = queue.Queue()  # Queue for raw USB data
        self.packet_count = 0
        self.async_mode = async_mode
        self.read_buffer = bytearray()  # Buffer for accumulating packet data
        
        if async_mode:
            self.response_queues = {}  # Dict mapping packet ID to response queue
            self.response_lock = threading.Lock()  # Lock for thread-safe access to response_queues

    def claim(self):
        super().claim()
        intf = self.dev.get_active_configuration()[(self.interface_index, 0)]
        self.ep_out = usb.util.find_descriptor(
            intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
        )
        if not self.ep_out:
            raise RuntimeError(f"{self.desc}: No OUT endpoint found")

    def send_packet(self, id=None, packetType=OW_ACK, command=OW_CMD_NOP, addr=0, reserved=0, data=None, timeout=0.010) -> UartPacket:        
        if id is None:
            self.packet_count = (self.packet_count + 1) & 0xFFFF or 1
            id = self.packet_count
            
        if data:
            if not isinstance(data, (bytes, bytearray)):
                raise ValueError("Data must be bytes or bytearray")
            payload = data
            payload_length = len(payload)
        else:
            payload_length = 0
            payload = b''
        
        uart_packet = UartPacket(
            id=id,
            packet_type=packetType,
            command=command,
            addr=addr,
            reserved=reserved,
            data=payload
        )
        
        if not self.async_mode:
            # Synchronous mode: block and read directly from USB
            tx_bytes = uart_packet.to_bytes()
            logger.debug(f"{self.desc}: Sending packet ID={id}, type=0x{packetType:02X}, cmd=0x{command:02X}")
            self.write(tx_bytes)
            time.sleep(0.0005)
            
            start = time.monotonic()
            data = bytearray()

            while time.monotonic() - start < timeout:
                try:
                    resp = self.receive()
                    time.sleep(0.0005)
                    if resp:
                        data.extend(resp)
                        if data and data[-1] == OW_END_BYTE:  # OW_END_BYTE
                            break
                except usb.core.USBError:
                    continue

            if not data:
                raise TimeoutError("No response")

            return UartPacket(buffer=data)
        else:
            # Async mode: use response queue
            # CRITICAL: Set up response queue BEFORE sending packet to avoid race condition
            # Ensure read thread is running
            if not self.read_thread or not self.read_thread.is_alive():
                logger.warning(f"{self.desc}: Read thread not running in async mode, starting it now")
                self.start_read_thread()
                # Give thread a moment to start
                time.sleep(0.01)
            
            response_queue = queue.Queue()
            with self.response_lock:
                self.response_queues[id] = response_queue
            logger.debug(f"{self.desc}: Created response queue for packet ID {id} BEFORE sending")
            
            # Now send the packet
            tx_bytes = uart_packet.to_bytes()
            logger.debug(f"{self.desc}: Sending packet ID={id}, type=0x{packetType:02X}, cmd=0x{command:02X}")
            self.write(tx_bytes)
            time.sleep(0.0005)
            logger.debug(f"{self.desc}: Packet sent, waiting for response...")

            try:
                # Wait for a response that matches the packet ID
                response = response_queue.get(timeout=timeout)
                logger.debug(f"{self.desc}: Received response for packet ID {id}, type=0x{response.packet_type:02X}, cmd=0x{response.command:02X}")
                # check that the response has the same ID as the sent packet
                if response.id == id:
                    logger.warning(f"{self.desc}: Received response with correct ID: {response.id} (expected {id})")
                    return response
                else:
                    logger.warning("Received response with unexpected type/command: type=0x%02X, cmd=0x%02X (expected type=0x%02X, cmd=0x%02X)", 
                                 response.packet_type, response.command, OW_RESP, command)
                    return response
            except queue.Empty:
                logger.error("Timeout waiting for response to packet ID %d (timeout=%.3f s)", id, timeout)
                # Log current state for debugging
                with self.response_lock:
                    logger.debug(f"{self.desc}: Active response queues: {list(self.response_queues.keys())}")
                raise TimeoutError(f"No response to packet ID {id}")
            finally:
                with self.response_lock:
                    # Clean up the queue entry regardless of outcome
                    self.response_queues.pop(id, None)
            
    def clear_buffer(self):
        """Clear both the read queue and read buffer."""
        while not self.read_queue.empty():
            try:
                self.read_queue.get_nowait()
            except queue.Empty:
                break
        self.read_buffer.clear()

    def write(self, data, timeout=100):
        return self.dev.write(self.ep_out.bEndpointAddress, data, timeout=timeout)

    def receive(self, length=512, timeout=100):
        data = self.dev.read(self.ep_in.bEndpointAddress, length, timeout=timeout)
        logger.debug(f"Received {len(data)} bytes.")
        return data
    
    def start_read_thread(self):
        if self.read_thread and self.read_thread.is_alive():
            logger.info(f"{self.desc}: Read thread already running")
            return
        self.stop_event.clear()
        self.read_thread = threading.Thread(target=self._read_loop, daemon=True)
        self.read_thread.start()
        logger.info(f"{self.desc}: Read thread started (async_mode={self.async_mode})")

    def stop_read_thread(self):
        self.stop_event.set()
        if self.read_thread:
            self.read_thread.join()
        logger.info(f"{self.desc}: Read thread stopped")
        
    def _read_loop(self):
        while not self.stop_event.is_set():
            try:
                data = self.dev.read(self.ep_in.bEndpointAddress, self.ep_in.wMaxPacketSize, timeout=100)
                if data:
                    self.read_buffer.extend(bytes(data))
                    # Try to parse complete packets from buffer
                    self._parse_packets_from_buffer()
            except usb.core.USBError as e:
                if e.errno != 110:
                    logger.error(f"{self.desc} read error: {e}")
    
    def _parse_packets_from_buffer(self):
        """Parse complete packets from read_buffer and route them appropriately."""
        while len(self.read_buffer) > 0:
            # Look for start byte
            start_idx = -1
            for i in range(len(self.read_buffer)):
                if self.read_buffer[i] == OW_START_BYTE:
                    start_idx = i
                    break
            
            if start_idx == -1:
                # No start byte found, clear buffer
                self.read_buffer.clear()
                break
            
            # Remove any data before start byte
            if start_idx > 0:
                self.read_buffer = self.read_buffer[start_idx:]
            
            # Need at least 9 bytes for header (START + ID(2) + TYPE + CMD + ADDR + RES + LEN(2))
            if len(self.read_buffer) < 9:
                break
            
            # Extract data length
            data_len = int.from_bytes(self.read_buffer[7:9], 'big')
            
            # Calculate total packet size: START(1) + ID(2) + TYPE(1) + CMD(1) + ADDR(1) + RES(1) + LEN(2) + DATA + CRC(2) + END(1)
            total_packet_size = 9 + data_len + 3  # 9 header bytes + data + CRC(2) + END(1)
            
            if len(self.read_buffer) < total_packet_size:
                # Incomplete packet, wait for more data
                break
            
            # Extract complete packet
            packet_data = bytes(self.read_buffer[:total_packet_size])
            self.read_buffer = self.read_buffer[total_packet_size:]
            
            # Try to parse the packet
            try:
                packet = UartPacket(buffer=packet_data)
                
                if self.async_mode:
                    # In async mode, route packet to appropriate response queue
                    with self.response_lock:
                        if packet.id in self.response_queues:
                            logger.debug(f"{self.desc}: Routing packet ID {packet.id} to response queue")
                            self.response_queues[packet.id].put(packet)
                        else:
                            logger.warning(f"{self.desc}: Received unsolicited packet with ID {packet.id}")
                            logger.warning(f"{self.desc}: Packet type: 0x{packet.packet_type:02X}, Command: 0x{packet.command:02X}")
                            logger.debug(f"{self.desc}: Active response queues: {list(self.response_queues.keys())}")
                else:
                    # In sync mode, put raw packet data in queue for send_packet to process
                    self.read_queue.put(packet_data)
                    
            except ValueError as e:
                # Invalid packet, skip it
                logger.debug(f"Invalid packet received: {e}")
                # Try to find next start byte
                continue