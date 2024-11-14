from .core import *
from .config import *
from .utils import *
import asyncio
import struct
from typing import List
import json

class CTRL_IF:

    def __init__(self, uart: UART):
        self.uart = uart
        self.packet_count = 0
        self._afe_instances = []

    async def ping(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count

        #
        response = await self.uart.send_packet(id=packet_id, packetType=OW_CMD, command=OW_CMD_PING)
        self.uart.clear_buffer()

        return response

    async def pong(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count

        #
        response = await self.uart.send_packet(id=packet_id, packetType=OW_CMD, command=OW_CMD_PONG)
        self.uart.clear_buffer()

        return response

    async def echo(self, data=None, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        #
        response = await self.uart.send_packet(id=packet_id, packetType=OW_CMD, command=OW_CMD_ECHO, data=data)
        self.uart.clear_buffer()
        return response

    async def toggle_led(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_CMD, command=OW_CMD_TOGGLE_LED)
        self.uart.clear_buffer()
        return response

    async def version(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_CMD, command=OW_CMD_VERSION)
        self.uart.clear_buffer()
        return response

    async def chipid(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_CMD, command=OW_CMD_HWID)
        self.uart.clear_buffer()
        return response

    async def reset(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_CMD, command=OW_CMD_RESET)
        self.uart.clear_buffer()
        return response

    async def fpga_scan(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_FPGA, command=OW_FPGA_SCAN)
        self.uart.clear_buffer()
        return response
    
    async def fpga_on(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_FPGA, command=OW_FPGA_ON)
        self.uart.clear_buffer()
        return response
    
    async def fpga_off(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_FPGA, command=OW_FPGA_OFF)
        self.uart.clear_buffer()
        return response
    
    async def fpga_id(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_FPGA, command=OW_FPGA_ID)
        self.uart.clear_buffer()
        return response
    
    async def fpga_enter_sram_prog(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_FPGA, command=OW_FPGA_ENTER_SRAM_PROG)
        self.uart.clear_buffer()
        return response
    
    async def fpga_exit_sram_prog(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_FPGA, command=OW_FPGA_EXIT_SRAM_PROG)
        self.uart.clear_buffer()
        return response
    
    async def fpga_erase_sram(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_FPGA, command=OW_FPGA_ERASE_SRAM)
        self.uart.clear_buffer()
        return response
    
    async def fpga_program_sram(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_FPGA, command=OW_FPGA_PROG_SRAM)
        self.uart.clear_buffer()
        return response
        
    async def fpga_status(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_FPGA, command=OW_FPGA_STATUS)
        self.uart.clear_buffer()
        return response
        
    async def fpga_usercode(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_FPGA, command=OW_FPGA_USERCODE)
        self.uart.clear_buffer()
        return response
    
    async def fpga_reset(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_FPGA, command=OW_FPGA_RESET)
        self.uart.clear_buffer()
        return response

    async def fpga_soft_reset(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_FPGA, command=OW_FPGA_SOFT_RESET)
        self.uart.clear_buffer()
        return response

    
    async def fpga_activate(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_FPGA, command=OW_FPGA_ACTIVATE)
        self.uart.clear_buffer()
        return response

    async def camera_scan_id(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_CAMERA, command=OW_CAMERA_SCAN)
        self.uart.clear_buffer()
        return response
    
    
    async def camera_stream_on(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_CAMERA, command=OW_CAMERA_ON)
        self.uart.clear_buffer()
        return response
        
    async def camera_stream_off(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_CAMERA, command=OW_CAMERA_OFF)
        self.uart.clear_buffer()
        return response
    
    async def camera_configure_registers(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_CAMERA, command=OW_CAMERA_SET_CONFIG)
        self.uart.clear_buffer()
        return response
    
    async def camera_fsin_on(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_CAMERA, command=OW_CAMERA_FSIN_ON)
        self.uart.clear_buffer()
        return response
    
    async def camera_fsin_off(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_CAMERA, command=OW_CAMERA_FSIN_OFF)
        self.uart.clear_buffer()
        return response
    
    async def camera_i2c_write(self, packet, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        data = packet
        response = await self.uart.send_packet(id=packet_id, packetType=OW_CAMERA, command=OW_I2C_PASSTHRU, data=DeprecationWarning)
        self.uart.clear_buffer()
        return response
    
    async def send_bitstream(self, filename="test.bit", packet_id=None):

        max_bytes_per_block = 1024
        block_count = 0
        responses = []

        try:
            # Open the file in binary mode
            with open(filename, "rb") as f:
                address = 0

                # Read file in chunks of max_bytes_per_block
                while True:
                    data = f.read(max_bytes_per_block)
                    if not data:
                        response = await self.uart.send_packet(id=packet_id, packetType=OW_FPGA, command=OW_FPGA_BITSTREAM,addr=block_count, reserved=1, data=data)                                        
                        self.uart.clear_buffer()
                        responses.append(response)
                        break  # End of file
                    
                    # You can customize packet ID creation if needed
                    if packet_id is None:
                        self.packet_count += 1
                        packet_id = self.packet_count
                    
                    # Send the data chunk (packet) asynchronously
                    response = await self.uart.send_packet(id=packet_id, packetType=OW_FPGA, command=OW_FPGA_BITSTREAM,addr=block_count, reserved=0, data=data)            
                    self.uart.clear_buffer()
                    responses.append(response)

                    # Update the address and packet_id for the next block
                    address += len(data)
                    packet_id += 1
                    block_count += 1

        except FileNotFoundError:
            print(f"File {filename} not found.")
        except Exception as e:
            print(f"An error occurred: {e}")
        
        return responses   