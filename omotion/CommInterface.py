import logging
from omotion.UartPacket import UartPacket
from omotion.config import OW_ACK, OW_CMD_NOP, OW_END_BYTE, OW_START_BYTE
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
logger.setLevel(logging.INFO)

# =========================================
# Comm Interface (IN + OUT + threads)
# =========================================
class CommInterface(USBInterfaceBase):
    def __init__(self, dev, interface_index, desc="Comm"):
        super().__init__(dev, interface_index, desc)
        self.read_thread = None
        self.stop_event = threading.Event()
        self.read_queue = queue.Queue()
        self.packet_count = 0

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
        
        tx_bytes = uart_packet.to_bytes()
        self.write(tx_bytes)
        time.sleep(0.0005)

        # Wait for response
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
            
    def clear_buffer(self):
        while not self.read_queue.empty():
            try:
                self.read_queue.get_nowait()
            except queue.Empty:
                break

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
        logger.info(f"{self.desc}: Read thread started")

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
                    self.read_queue.put(bytes(data))
            except usb.core.USBError as e:
                if e.errno != 110:
                    logger.error(f"{self.desc} read error: {e}")