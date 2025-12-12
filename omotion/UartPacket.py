import logging

from omotion.config import OW_END_BYTE, OW_START_BYTE
from omotion.utils import util_crc16

# Set up logging
logger = logging.getLogger("UARTPACKET")
logger.setLevel(logging.INFO)  # or INFO depending on what you want to see


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
            logger.error(f"Missing Start or End Byte Packet LEN {str(len(buffer))}")
            logger.debug(buffer)
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
            logger.error(f"Packet CRC: {str(self.crc)}, Calculated CRC: {str(crc_value)}")
            raise ValueError("CRC mismatch")

    def print_packet(self,full=False):
        logger.debug("UartPacket:")
        logger.debug("  Packet ID:", self.id)
        logger.debug("  Packet Type:", hex(self.packet_type))
        logger.debug("  Command:", hex(self.command))
        logger.debug("  Data Length:", self.data_len)
        if(full):
            logger.debug("  Address:", hex(self.addr))
            logger.debug("  Reserved:", hex(self.reserved))
            logger.debug("  Data:", self.data.hex())
            logger.debug("  CRC:", hex(self.crc))
        
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
