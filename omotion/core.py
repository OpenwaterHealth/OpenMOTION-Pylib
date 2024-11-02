import json
import logging
import asyncio
import time
import struct

from .config import *
from .utils import util_crc16
from .async_serial import AsyncSerial  # Assuming async_serial.py contains the AsyncSerial class

# Set up logging
logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger("UART")

class UartPacket:
    def __init__(self, id=None, packet_type=None, command=None, addr=None, reserved=None, data=None, buffer=None):
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
            raise ValueError("CRC mismatch")

    def print_packet(self):
        print("UartPacket:")
        print("  Packet ID:", self.id)
        print("  Packet Type:", hex(self.packet_type))
        print("  Command:", hex(self.command))
        print("  Address:", hex(self.addr))
        print("  Reserved:", hex(self.reserved))
        print("  Data Length:", self.data_len)
        print("  Data:", self.data.hex())
        print("  CRC:", hex(self.crc))

class UART:
    def __init__(self, port: str, baud_rate=921600, timeout=10, align=0):
        log.info(f"Connecting to COM port at {port} speed {baud_rate}")
        self.port = port
        self.baud_rate = baud_rate
        self.timeout = timeout
        self.align = align
        self.ser = AsyncSerial(port, baud_rate, timeout)
        self.read_buffer = []

    async def connect(self):
        # Already connected via AsyncSerial's __init__
        pass

    def close(self):
        self.ser.close()

    async def send_ustx(self, id=0, packetType=OW_ACK, command=OW_CMD_NOP, addr=0, reserved=0, data=None, timeout=10):
        if data:
            if packetType == OW_JSON:
                payload = json.dumps(data).encode('utf-8')
            else:
                payload = data
            payload_length = len(payload)
        else:
            payload_length = 0

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
        crc_value = util_crc16(packet[1:])
        packet.extend(crc_value.to_bytes(2, 'big'))
        packet.append(OW_END_BYTE)

        await self._tx(packet)
        await self._wait_for_response(timeout)
        return self.read_buffer

    async def send(self, buffer):
        await self._tx(buffer)

    async def read(self):
        await self._rx()
        return self.read_buffer

    async def _tx(self, data: bytes):
        try:
            if self.align > 0:
                while len(data) % self.align != 0:
                    data += bytes([OW_END_BYTE])
            await self.ser.write(data)
        except Exception as e:
            log.error(f"Error during transmission: {e}")

    async def _rx(self):
        try:
            while True:
                data = await self.ser.read_all()
                if data:
                    self.read_buffer.extend(data)
                    if OW_END_BYTE in data:
                        break
        except Exception as e:
            log.error(f"Error during reception: {e}")

    async def _wait_for_response(self, timeout):
        start_time = time.monotonic()
        while (time.monotonic() - start_time) < timeout:
            await self._rx()
            if self.read_buffer and OW_END_BYTE in self.read_buffer:
                return
            await asyncio.sleep(0.1)
        log.error("Timeout waiting for response")

    def clear_buffer(self):
        self.read_buffer = []

    def print(self):
        print("    Serial Port: ", self.port)
        print("    Serial Baud: ", self.baud_rate)

    
    async def start_telemetry_listener(self):
        ''' Continuously listen for telemetry data on a separate loop '''
        self._listening = True
        while self._listening:
            if self.ser.in_waiting() > 0:  # Check if there is any incoming data
                telemetry_data = await self.ser.read_all()
                if self.telemetry_parser:
                    self.telemetry_parser(telemetry_data)  # Process telemetry data
        await asyncio.sleep(0.01)  # Small delay to prevent busy waiting

    def bytes_to_integers(self,byte_array):
        # Check that the byte array is exactly 4096 bytes
        if len(byte_array) != 4096:
            raise ValueError("Input byte array must be exactly 4096 bytes.")

        # Initialize an empty list to store the converted integers
        integers = []

        # Iterate over the byte array in chunks of 4 bytes
        for i in range(0, len(byte_array), 4):
            # Unpack each 4-byte chunk as a single integer (big-endian)
            integer = struct.unpack_from('<I', byte_array, i)[0]
            integers.append(integer)
        total_sum = sum(integers)
        print("Total Sum:", total_sum)
        return integers

    def telemetry_parser(self,data):
        try:
            # Ensure minimum length for Packet ID, Status Code, Timestamp
            # Define the format for the fixed part of the structure (up to data_len)
            fixed_part_format = '>B H B B B B H'
            fixed_part_size = struct.calcsize(fixed_part_format)

            # Check if data has enough bytes for the fixed part
            if len(data) < fixed_part_size:
                raise ValueError("Data is too short to contain UartPacket fixed fields.")

            # Unpack the fixed-length fields
            protocol_type, id, packet_type, command, addr, reserved, data_len = struct.unpack_from(fixed_part_format, data, 0)

            # Calculate the total expected length including variable-length data
            total_length = fixed_part_size + data_len + 2  # +2 for the CRC at the end
            print("Packet len" + str(len(data)))
            print("Data len" + str(data_len))
            print("Total length" + str(total_length))
            # Check if data has enough bytes for the entire packet
            if len(data) < total_length:
                raise ValueError("Data is too short to contain the entire UartPacket with variable data length.")

            # Extract the variable-length `data` field and the `crc`
            data_start = fixed_part_size
            data_end = data_start + data_len
            data_field = data[data_start:data_end]

            # Unpack the CRC
            crc_format = '>H'
            crc = struct.unpack_from(crc_format, data, data_end)[0]

            if(command == 0x1b):
                print(self.bytes_to_integers(data_field))
            # Return the unpacked values in a dictionary for easier access
            return {
                "id": id,
                "packet_type": packet_type,
                "command": command,
                "addr": addr,
                "reserved": reserved,
                "data_len": data_len,
                "data": data_field,
                "crc": crc
            }

        except struct.error as e:
            print("Failed to parse telemetry data:", e)
            return

    # Example parsers for specific status codes
    def parse_warning(additional_data):
        # Assuming warning payload contains a warning code and a message
        warning_code, = struct.unpack('>H', additional_data[:2])
        warning_message = additional_data[2:].decode('utf-8')
        print(f"Warning {warning_code}: {warning_message}")

    def parse_error(additional_data):
        # Assuming error payload contains an error code and details
        error_code, = struct.unpack('>H', additional_data[:2])
        error_details = additional_data[2:].decode('utf-8')
        print(f"Error {error_code}: {error_details}")
