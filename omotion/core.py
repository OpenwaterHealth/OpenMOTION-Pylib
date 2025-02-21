import json
import logging
import asyncio
import time
import struct
import csv

from .config import *
from .utils import util_crc16
from .async_serial import AsyncSerial  # Assuming async_serial.py contains the AsyncSerial class

# Set up logging
logging.basicConfig(filename="telem.log",
                    filemode='a',
                    level=logging.DEBUG)
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
        
class UART:
    def __init__(self, port: str, baud_rate=2000000, timeout=10, align=0):
        log.info(f"Connecting to COM port at {port} speed {baud_rate}")
        self.port = port
        self.baud_rate = baud_rate
        self.timeout = timeout
        self.align = align
        self.ser = AsyncSerial(port, baud_rate, timeout)
        self.read_buffer = []

        with open('histo_data.csv', mode='w', newline='') as file:
            file.truncate()
            writer = csv.writer(file)
            header = ['id'] + list(range(1024)) + ['total']
            writer.writerow(header)

    async def connect(self):
        # Already connected via AsyncSerial's __init__
        pass

    def close(self):
        self.ser.close()

    async def send_packet(self, id=0, packetType=OW_ACK, command=OW_CMD_NOP, addr=0, reserved=0, data=None, timeout=10, wait_for_response = True):
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
        print("CRC: " + str(crc_value))
        await self._tx(packet)
        if wait_for_response:
            await self._wait_for_response(timeout)
            return self.read_packet()
        else:
            packet = UartPacket(id = 0,
                    packet_type=OW_CODE_SUCCESS,
                    command =0,
                    addr = 0,
                    reserved = 0,
                    data = [] )
            return packet
        
    async def send(self, buffer):
        await self._tx(buffer)

    async def read(self):
        await self._rx()
        return self.read_buffer
    
    def read_packet(self):
        try:
            packet = UartPacket(buffer=self.read_buffer)
        except Exception as e:
            print("Bad packet recieved: " + str(e))
            packet = UartPacket(id = 0,
                                packet_type=OW_BAD_PARSE,
                                command =0,
                                addr = 0,
                                reserved = 0,
                                data = [] )
        return packet
        
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
                    if(data[0] == OW_START_BYTE and len(data) > 9): ## if enough of the packet has come in to determine length
                        data_len = int.from_bytes(data[7:9], 'big')
                        packet_len = len(data)
                        if(packet_len == (data_len + 12)):          ## wait for enough of the packet to come in to determine if its done
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

    async def start_telemetry_listener(self, timeout = 0):
        ''' Continuously listen for telemetry data on a separate loop '''
        self._listening = True
        start_time = time.monotonic()
        while self._listening:
            if ((timeout != 0) & ((time.monotonic() - start_time) > timeout)):
                self._listening = False
                return
            if self.ser.in_waiting() > 0:  # Check if there is any incoming data
                await self._rx()
                try:
                    print("recieved data")
                    telemetry_packet = UartPacket(buffer=self.read_buffer)
                except struct.error as e:
                    print("Failed to parse telemetry data:", e)
                    return

                self.clear_buffer()
                self.telemetry_parser(telemetry_packet)  # Process telemetry data
        await asyncio.sleep(0.01)  # Small delay to prevent busy waiting

    def bytes_to_integers(self,byte_array):
        # Check that the byte array is exactly 4096 bytes
        if len(byte_array) != 4096:
            raise ValueError("Input byte array must be exactly 4096 bytes.")
        
        # Initialize an empty list to store the converted integers
        integers = []
        hidden_figures = []
        # Iterate over the byte array in chunks of 4 bytes
        for i in range(0, len(byte_array), 4):
            bytes = byte_array[i:i+4]
            # Unpack each 4-byte chunk as a single integer (big-endian)
#            integer = struct.unpack_from('<I', byte_array, i)[0]
            # if(bytes[0] + bytes[1] + bytes[2] + bytes[3] > 0):
            #     print(str(i) + " " + str(bytes[0:3]))
            hidden_figures.append(bytes[3])
            integers.append(int.from_bytes(bytes[0:3],byteorder='little'))
        return (integers, hidden_figures)

    def telemetry_parser(self,packet):
        try:
            if(packet.command == OW_HISTO):
                # print("Histo recieved")
                (histo,hidden_figures) = self.bytes_to_integers(packet.data)
                #log.info(msg=str(histo))
                total = sum(histo)
                print("SUM: " + str(total))
                frame_id = hidden_figures[1023]
                with open('histo_data.csv', mode='a', newline='') as file:
                    writer = csv.writer(file)
                    writer.writerow([frame_id] + histo + [total])                  
            # else:
            #     packet.print_packet()
            elif(packet.command == OW_SCAN):
                print("Scan recieved")
                print(packet.data.hex())
        except struct.error as e:
            print("Failed to parse telemetry data:", e)
            return