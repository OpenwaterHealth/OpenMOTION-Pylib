import logging
import omotion.config as config
from omotion.UartPacket import UartPacket
from omotion.config import (
    OW_ACK,
    OW_CMD_NOP,
    OW_END_BYTE,
    OW_START_BYTE,
    OW_DATA,
    OW_CMD_ECHO,
)
import usb.core
import usb.util
import time
import threading
import queue
from omotion.USBInterfaceBase import USBInterfaceBase
from omotion import _log_root

# Max data_len we accept (sanity check to avoid runaway buffer)
OW_MAX_PACKET_DATA_LEN = 4096 * 2

logger = logging.getLogger(
    f"{_log_root}.CommInterface" if _log_root else "CommInterface"
)

# Max data_len we accept (sanity check to avoid runaway buffer)
OW_MAX_PACKET_DATA_LEN = 4096 * 2

_PACKET_TYPE_NAMES = {
    value: name
    for name, value in vars(config).items()
    if name.startswith("OW_") and name.isupper() and isinstance(value, int)
}
_CMD_NAMES = {
    "OW_CMD": {
        value: name
        for name, value in vars(config).items()
        if name.startswith("OW_CMD_")
    },
    "OW_CONTROLLER": {
        value: name
        for name, value in vars(config).items()
        if name.startswith("OW_CTRL_")
    },
    "OW_FPGA": {
        value: name
        for name, value in vars(config).items()
        if name.startswith("OW_FPGA_")
    },
    "OW_CAMERA": {
        value: name
        for name, value in vars(config).items()
        if name.startswith("OW_CAMERA_")
    },
    "OW_IMU": {
        value: name
        for name, value in vars(config).items()
        if name.startswith("OW_IMU_")
    },
}


def _format_named(value: int, name_map: dict[int, str], width: int = 2) -> str:
    name = name_map.get(value)
    if name:
        return f"{name}(0x{value:0{width}X})"
    return f"0x{value:0{width}X}"


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
        self.on_disconnect = None
        self._disconnect_notified = False
        self._io_lock = threading.RLock()
        if self.async_mode:
            self.response_queue = queue.Queue()
            self.response_thread = threading.Thread(
                target=self._process_responses, daemon=True
            )
            self.response_thread.start()

    def claim(self):
        super().claim()
        intf = self.dev.get_active_configuration()[(self.interface_index, 0)]
        self.ep_out = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: (
                usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
            ),
        )
        if not self.ep_out:
            raise RuntimeError(f"{self.desc}: No OUT endpoint found")

    def send_packet(
        self,
        id=None,
        packetType=OW_ACK,
        command=OW_CMD_NOP,
        addr=0,
        reserved=0,
        data=None,
        timeout=10.0,
        max_retries=0,
    ) -> UartPacket:
        if id is None:
            self.packet_count = (self.packet_count + 1) & 0xFFFF or 1
            id = self.packet_count

        if data:
            if not isinstance(data, (bytes, bytearray)):
                raise ValueError("Data must be bytes or bytearray")
            payload = data
        else:
            payload = b""

        uart_packet = UartPacket(
            id=id,
            packet_type=packetType,
            command=command,
            addr=addr,
            reserved=reserved,
            data=payload,
        )

        tx_bytes = uart_packet.to_bytes()
        packet_type_name = _PACKET_TYPE_NAMES.get(packetType)
        cmd_names = _CMD_NAMES.get(packet_type_name, {})
        logger.debug(
            f"{self.desc}: TX id=0x{id:04X} "
            f"type={_format_named(packetType, _PACKET_TYPE_NAMES)} "
            f"cmd={_format_named(command, cmd_names)} "
            f"addr=0x{addr:02X} reserved=0x{reserved:02X} len={len(payload)} data={tx_bytes.hex()}"
        )

        self.write(tx_bytes)
        time.sleep(0.0005)

        if not self.async_mode:
            start = time.monotonic()
            data = bytearray()
            with self._io_lock:
                while time.monotonic() - start < timeout:
                    try:
                        resp = self.receive()
                        time.sleep(0.005)
                        if resp:
                            data.extend(resp)
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
                    time.sleep(
                        0.001
                    )  # wait for a moment to let the MCU finish processing that it has finished sending the packet
                    # this delay could be placed anywhere at the end of the send_packet function just to give the MCU time to finish processing
                    return self.response_queue.get()
            raise TimeoutError(f"No response in async mode, packet id 0x{id:04X}")

    def clear_buffer(self):
        with self._buffer_lock:
            self._read_buffer.clear()

    def write(self, data, timeout=100, _retries=5):
        with self._io_lock:
            for attempt in range(1 + _retries):
                try:
                    return self.dev.write(self.ep_out.bEndpointAddress, data, timeout=timeout)
                except usb.core.USBError as e:
                    # Firmware back-pressure: the device's OUT FIFO is temporarily
                    # full.  Back off briefly and retry so callers don't have to
                    # care about transient busy periods (e.g. after program_fpga).
                    if e.errno in (110, 10060):  # ETIMEDOUT / WSAETIMEDOUT
                        if attempt < _retries:
                            delay = 0.05 * (attempt + 1)  # 50 ms, 100 ms, 150 ms …
                            logger.warning(
                                "%s: write timeout (attempt %d/%d), retrying in %.0f ms",
                                self.desc, attempt + 1, 1 + _retries, delay * 1000,
                            )
                            time.sleep(delay)
                            continue
                        logger.error("%s: write timed out after %d attempts", self.desc, 1 + _retries)
                        raise
                    # A stalled endpoint (EPIPE / broken-pipe) can be recovered by
                    # issuing a CLEAR_HALT control transfer.  Try once; if it works
                    # re-send the original data.  Any other USB error is re-raised
                    # so callers and _read_loop disconnect logic see it normally.
                    if e.errno in (32, -9):  # EPIPE on Linux; LIBUSB_ERROR_PIPE cross-platform
                        logger.warning("%s: OUT endpoint stalled, attempting clear_halt", self.desc)
                        try:
                            usb.util.clear_halt(self.dev, self.ep_out)
                            return self.dev.write(self.ep_out.bEndpointAddress, data, timeout=timeout)
                        except Exception as recovery_err:
                            logger.error("%s: clear_halt recovery failed: %s", self.desc, recovery_err)
                    raise

    def receive(self, length=512, timeout=100):
        with self._io_lock:
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
        # We're intentionally shutting down; prevent the read loop from
        # triggering disconnect callbacks/logging if the device disappears.
        self._disconnect_notified = True
        if self.read_thread:
            # Safety net: _trigger_disconnect now dispatches on_disconnect to a
            # separate daemon thread, so this guard should never fire in normal
            # operation.  It stays here to prevent a hang if anyone calls
            # stop_read_thread() from a context where the read thread is on the
            # call stack (joining the current thread raises RuntimeError).
            if threading.current_thread() is not self.read_thread:
                self.read_thread.join(timeout=2.0)
        logger.info(f"{self.desc}: Read thread stopped")

    def _trigger_disconnect(self, error):
        # During an intentional shutdown, ignore disconnect triggers.
        if self.stop_event.is_set():
            return
        if self._disconnect_notified:
            return
        self._disconnect_notified = True
        logger.error(f"{self.desc}: triggering disconnect due to USB error: {error}")
        self.stop_event.set()
        if callable(self.on_disconnect):
            # Dispatch the disconnect callback to a new daemon thread rather than
            # calling it directly from the read thread.  on_disconnect typically
            # calls MotionComposite.disconnect() → stop_read_thread() → join(),
            # which would deadlock if run on the read thread itself.  By handing
            # off to a separate thread the read loop exits naturally (stop_event
            # is already set) and the join in stop_read_thread() completes quickly.
            t = threading.Thread(
                target=self.on_disconnect,
                args=(self.desc, error),
                daemon=True,
                name=f"{self.desc}-disconnect",
            )
            t.start()

    def _read_loop(self):
        while not self.stop_event.is_set():
            try:
                data = self.dev.read(
                    self.ep_in.bEndpointAddress, self.ep_in.wMaxPacketSize, timeout=100
                )
                if data:
                    data_bytes = bytes(data)
                    with self._buffer_condition:
                        self._read_buffer.extend(data_bytes)
                        self._buffer_condition.notify()
                    logger.debug(f"Read {len(data)} bytes.")
                time.sleep(0.001)
            except usb.core.USBError as e:
                # If we're shutting down, USB errors here are expected and should not be noisy.
                if self.stop_event.is_set():
                    break
                if e.errno == 110:
                    pass
                elif e.errno == 10060:
                    pass
                elif e.errno == 32:
                    # Only log at ERROR when this is unexpected (not a clean shutdown).
                    if not self._disconnect_notified:
                        logger.error(f"{self.desc} read error: DISCONNECT{e}")
                    self._trigger_disconnect(e)
                    break

                elif e.errno == 19 or e.errno == 5:
                    # errno 19 = ENODEV (device unplugged or GC'd during shutdown).
                    # errno  5 = EIO   (device I/O error).
                    # Both are expected when the app is tearing down — only log at
                    # ERROR when the disconnect is genuinely unintentional.
                    if not self._disconnect_notified:
                        logger.error(f"{self.desc} read error: IO Error{e}")
                    self._trigger_disconnect(e)
                    break
                else:
                    if not self._disconnect_notified:
                        logger.error(f"{self.desc} read error: Unknown Error{e}")
                    self._trigger_disconnect(e)
                    break

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
            if (
                uart_packet.id == 0
                and uart_packet.packet_type == OW_DATA
                and uart_packet.command == OW_CMD_ECHO
            ):
                _raw = bytes(uart_packet.data[:uart_packet.data_len]) if uart_packet.data_len > 0 else b""
                try:
                    _text = _raw.decode("utf-8", errors="replace").rstrip("\x00").strip()
                except Exception:
                    _text = ""
                if _text:
                    logger.warning("[%s PRINTF] %s", self.desc, _text)
                else:
                    logger.warning("[%s] MCU echo: data=%s", self.desc, _raw.hex() if _raw else "")
            else:
                self.response_queue.put(uart_packet)
