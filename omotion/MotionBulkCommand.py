import time
from omotion.MotionBulkBase import MOTIONBulkBase
from omotion.config import OW_ACK, OW_CMD_NOP, OW_END_BYTE
from omotion.UartPacket import UartPacket
from omotion.utils import util_crc16
import usb.core


class MOTIONBulkCommand(MOTIONBulkBase):
    def __init__(self, vid, pid, timeout=100):
        super().__init__(vid, pid, timeout)
        self.interface = 0  # Bulk Command Interface
        self.packet_count = 0
        self.running = False
        self.packet_count = 0

    def send_packet(self, id=None, packetType=OW_ACK, command=OW_CMD_NOP, addr=0, reserved=0, data=None, timeout=1) -> UartPacket:        

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

        uart_packet = UartPacket(
            id=id,
            packet_type=packetType,
            command=command,
            addr=addr,
            reserved=reserved,
            data=payload
)
        tx_bytes = uart_packet.to_bytes()
        self.send(tx_bytes)

        # Wait for response
        start = time.monotonic()
        data = bytearray()

        while time.monotonic() - start < timeout:
            try:
                resp = self.receive()
                if resp:
                    data.extend(resp)
                    if data and data[-1] == OW_END_BYTE:
                        break
            except usb.core.USBError:
                continue

        if not data:
            raise TimeoutError("No response")

        return UartPacket(buffer=data)

