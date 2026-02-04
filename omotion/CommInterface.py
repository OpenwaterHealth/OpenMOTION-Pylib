import logging
from omotion.UartPacket import UartPacket
from omotion.config import OW_ACK, OW_CMD_NOP, OW_END_BYTE, OW_START_BYTE, OW_DATA, OW_CMD_ECHO

# Max data_len we accept (sanity check to avoid runaway buffer)
OW_MAX_PACKET_DATA_LEN = 4096
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
        # Contiguous byte buffer: USB reader extends the end, packet parser chops from the front
        self._read_buffer = bytearray()
        self._buffer_lock = threading.Lock()
        self._buffer_condition = threading.Condition(self._buffer_lock)
        self.packet_count = 0
        self.async_mode = async_mode
        if self.async_mode:
            self.response_queue = queue.Queue()
            self.response_thread = threading.Thread(target=self._process_responses, daemon=True)
            self.response_thread.start()

    def claim(self):
        super().claim()
        intf = self.dev.get_active_configuration()[(self.interface_index, 0)]
        self.ep_out = usb.util.find_descriptor(
            intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
        )
        if not self.ep_out:
            raise RuntimeError(f"{self.desc}: No OUT endpoint found")

    def send_packet(self, id=None, packetType=OW_ACK, command=OW_CMD_NOP, addr=0, reserved=0, data=None, timeout=0.030, max_retries=2) -> UartPacket:
        if id is None:
            self.packet_count = (self.packet_count + 1) & 0xFFFF or 1
            id = self.packet_count

        if data:
            if not isinstance(data, (bytes, bytearray)):
                raise ValueError("Data must be bytes or bytearray")
            payload = data
        else:
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
        last_error = None

        for attempt in range(max_retries + 1):
            if attempt > 0:
                logger.debug(f"{self.desc}: retry {attempt}/{max_retries}, resending packet id 0x{id:04X}")
            self.write(tx_bytes)
            time.sleep(0.0005)

            if not self.async_mode:
                start = time.monotonic()
                data = bytearray()
                while time.monotonic() - start < timeout:
                    try:
                        resp = self.receive()
                        time.sleep(0.0005)
                        if resp:
                            data.extend(resp)
                            if len(data) == 512:
                                data = data.rstrip(b'\x00')
                            if data and data[-1] == OW_END_BYTE:
                                return UartPacket(buffer=data)
                    except usb.core.USBError:
                        continue
                last_error = TimeoutError("No response")
            else:
                start_time = time.monotonic()
                while time.monotonic() - start_time < timeout:
                    if self.response_queue.empty():
                        time.sleep(0.0005)
                    else:
                        return self.response_queue.get()
                last_error = TimeoutError(f"No response in async mode, packet id 0x{id:04X}")

        raise last_error


            
    def clear_buffer(self):
        with self._buffer_lock:
            self._read_buffer.clear()

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
                    data_bytes = bytes(data)
                    if len(data_bytes) == 512:
                        data_bytes = data_bytes.rstrip(b'\x00')
                    with self._buffer_condition:
                        self._read_buffer.extend(data_bytes)
                        self._buffer_condition.notify()
                    logger.debug(f"Read {len(data)} bytes.")
            except usb.core.USBError as e:
                if e.errno != 110:
                    logger.error(f"{self.desc} read error: {e}")
    def _process_responses(self):
        while not self.stop_event.is_set():
            with self._buffer_condition:
                if not self._read_buffer:
                    self._buffer_condition.wait(timeout=0.1)
                    continue
                buf = self._read_buffer
                # Align to start of packet: discard leading bytes until OW_START_BYTE
                if buf[0] != OW_START_BYTE:
                    try:
                        start_idx = buf.index(OW_START_BYTE)
                    except ValueError:
                        start_idx = len(buf)
                    del self._read_buffer[:start_idx]
                    if start_idx == len(buf):
                        continue
                    buf = self._read_buffer
                # Need at least 9 bytes to read data_len (bytes 7:9)
                if len(buf) < 9:
                    continue
                data_len = int.from_bytes(buf[7:9], "big")
                if data_len > OW_MAX_PACKET_DATA_LEN:
                    del self._read_buffer[:1]
                    continue
                packet_len = 12 + data_len  # header(11) + data + crc(2) + end(1)
                if len(buf) < packet_len:
                    continue
                if buf[packet_len - 1] != OW_END_BYTE:
                    del self._read_buffer[:1]
                    continue
                packet_bytes = bytes(buf[:packet_len])
                del self._read_buffer[:packet_len]
            try:
                uart_packet = UartPacket(buffer=packet_bytes)
            except ValueError:
                continue
            if uart_packet.id == 0 and uart_packet.packet_type == OW_DATA and uart_packet.command == OW_CMD_ECHO:
                logger.info(f"[MCU] {self.desc}: {uart_packet.data}")
            else:
                self.response_queue.put(uart_packet)
