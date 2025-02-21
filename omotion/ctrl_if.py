from .core import *
from .config import *
from .utils import *
import asyncio
import struct
from typing import List
from .i2c_data_packet import I2C_DATA_Packet
from .i2c_packet import I2C_Packet
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

    async def set_trigger(self, data=None, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count

        if data:
            try:
                json_string = json.dumps(data)
            except json.JSONDecodeError as e:
                print(f"Data must be valid JSON: {e}")
                return  

            payload = json_string.encode('utf-8')
        else:
            payload = None

        response = await self.uart.send_packet(id=packet_id, packetType=OW_CMD, command=OW_CTRL_SET_TRIG, data=payload)
        self.uart.clear_buffer()

        return response

    async def get_trigger(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_CMD, command=OW_CTRL_GET_TRIG, data=None)
        self.uart.clear_buffer()
        data_object = None
        try:
            # parsedResp = UartPacket(buffer=response)
            data_object = json.loads(response.data.decode('utf-8'))
        except json.JSONDecodeError as e:
            print("Error decoding JSON:", e)
        return data_object
    
    async def start_trigger(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_CMD, command=OW_CTRL_START_TRIG, data=None)
        self.uart.clear_buffer()
        return response


    async def stop_trigger(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        response= await self.uart.send_packet(id=packet_id, packetType=OW_CMD, command=OW_CTRL_STOP_TRIG, data=None)
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
    async def camera_fsin_ext_on(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_CAMERA, command=OW_CAMERA_FSIN_EXT_ON)
        self.uart.clear_buffer()
        return response
    async def camera_fsin_ext_off(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_CAMERA, command=OW_CAMERA_FSIN_EXT_OFF)
        self.uart.clear_buffer()
        return response
    
    async def camera_set_gain(self,gain,packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        gain_bytes = gain.to_bytes(2,'big')
        
        await self.camera_i2c_write(I2C_Packet(id=self.packet_count,device_address=0x36,register_address=0x3508,data=gain_bytes[1]))
        await asyncio.sleep(0.05)
        self.packet_count += 1
        await self.camera_i2c_write(I2C_Packet(id=self.packet_count,device_address=0x36,register_address=0x3509,data=gain_bytes[0]))
        await asyncio.sleep(0.05)
        return 0

    async def camera_set_exposure(self,exposure_selection,packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        exposures = [0x1F,0x20,0x2C,0x2D]
        exposure_byte = exposures[exposure_selection]
        # ;; exp=242.83us --> {0x3501,0x3502} = 0x001F
        # ;; exp=250.67us --> {0x3501,0x3502} = 0x0020
        # ;; exp=344.67us --> {0x3501,0x3502} = 0x002C
        # ;; exp=352.50us --> {0x3501,0x3502} = 0x002D

        await self.camera_i2c_write(I2C_Packet(id=self.packet_count,device_address=0x36,register_address=0x3501,data=0x00))
        await asyncio.sleep(0.05)
        self.packet_count += 1
        await self.camera_i2c_write(I2C_Packet(id=self.packet_count,device_address=0x36,register_address=0x3502,data=exposure_byte))
        await asyncio.sleep(0.05)
        return 0
    
    async def camera_enable_test_pattern(self,pattern_id,packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        if(pattern_id == 0):
            # {0x5000, 0x3f},  X02C1B_test_gradient_bar
            # {0x5100, 0x80},
            # {0x5102, 0x20},
            # {0x5103, 0x04},
            pattern_bytes = {0x5000:0x3f,0x5100:0x80,0x5102:0x20,0x5103:0x04}
        elif(pattern_id == 1):
            # {0x5000, 0x3f},X02C1B_test_solid_a
            # {0x5100, 0x80},
            # {0x5102, 0x00},
            # {0x5103, 0x01},
            pattern_bytes = {0x5000:0x3f,0x5100:0x80,0x5102:0x00,0x5103:0x01}
        elif(pattern_id == 2):
            # {0x5000, 0x3f},X02C1B_test_square
            # {0x5100, 0x82},
            # {0x5103, 0x04},
            pattern_bytes = {0x5000:0x3f,0x5100:0x82,0x5103:0x04}
        elif(pattern_id == 3):
            # {0x5000, 0x3f},X02C1B_test_gradient_cont
            # {0x5100, 0x80},
            # {0x5102, 0x30},
            # {0x5103, 0x04},
            pattern_bytes = {0x5000:0x3f,0x5100:0x80,0x5102:0x30,0x5103:0x04}
        else:
            pattern_bytes = {0x5000:0x3f,0x5100:0x80,0x5102:0x20,0x5103:0x04}
        
        for(register_address,data) in pattern_bytes.items():
            await self.camera_i2c_write(I2C_Packet(id=self.packet_count,device_address=0x36,register_address=register_address,data=data))
            await asyncio.sleep(0.05)
            self.packet_count += 1

        return 0

    async def camera_disable_test_pattern(self,packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        # {0x5000, 0x3e},
        # {0x5100, 0x00},
        pattern_bytes = {0x5000:0x3e,0x5100:0x00}
        
        for(register_address,data) in pattern_bytes.items():
            await self.camera_i2c_write(I2C_Packet(id=self.packet_count,device_address=0x36,register_address=register_address,data=data))
            await asyncio.sleep(0.05)
            self.packet_count += 1

        return 0
    
    async def camera_set_rgbir(self,packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        # @@ RGB-IR
        # 6c 3840 00
        # 6c 3712 02
        # 6c 5103 00
        # 6c 5265 04
        # 6c 4508 80
        pattern_bytes = {0x3840:0x00,
                         0x3712:0x02,
                         0x5103:0x00,
                         0x5265:0x04,
                         0x4508:0x80}
        
        for(register_address,data) in pattern_bytes.items():
            await self.camera_i2c_write(I2C_Packet(id=self.packet_count,device_address=0x36,register_address=register_address,data=data))
            await asyncio.sleep(0.05)
            self.packet_count += 1

        return 0
    async def camera_set_mono(self,packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        # @@ BW
        # 6c 3840 40 same
        # 6c 3712 c2
        # 6c 5103 02
        # 6c 5265 14
        # 6c 4508 00
        pattern_bytes = {0x3840:0x40,
                        0x3712:0xc2,
                        0x5103:0x02,
                        0x5265:0x14,
                        0x4508:0x00}
        for(register_address,data) in pattern_bytes.items():
            await self.camera_i2c_write(I2C_Packet(id=self.packet_count,device_address=0x36,register_address=register_address,data=data))
            await asyncio.sleep(0.05)
            self.packet_count += 1

        return 0
    

    

    async def camera_i2c_write(self, packet, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        data = packet.register_address.to_bytes(2,'big') + packet.data.to_bytes(1,'big')
        response = await self.uart.send_packet(id=packet_id, packetType=OW_I2C_PASSTHRU, command=packet.device_address, data=data,wait_for_response=False)
        self.uart.clear_buffer()
        return response
    
    async def switch_camera(self, camera_id, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        bytes_val = camera_id.to_bytes(1, 'big')
        response = await self.uart.send_packet(id=packet_id, packetType=OW_CAMERA, command=OW_CAMERA_SWITCH, data=bytes_val)
        self.uart.clear_buffer()
        return response
    
    async def read_camera_temp(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        self.uart.clear_buffer()

        response = await self.uart.send_packet(id=packet_id, packetType=OW_CAMERA, command=OW_CAMERA_READ_TEMP)
        self.uart.clear_buffer()
        temp = struct.unpack('f', response.data)[0]
        return temp
    
    async def toggle_camera_stream(self, camera_id, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count
        
        response = await self.uart.send_packet(id=packet_id, packetType=OW_CMD, command=OW_TOGGLE_CAMERA_STREAM, data=camera_id.to_bytes(1, 'big'))
        self.uart.clear_buffer()
        return response
    
    async def enable_i2c_broadcast(self, packet_id=None):
        if packet_id is None:
            self.packet_count += 1
            packet_id = self.packet_count

        response = await self.uart.send_packet(id=packet_id, packetType=OW_CMD, command=OW_CMD_I2C_BROADCAST)
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
        #Print total bytes sent
        print(f"Total bytes sent: {address}")
        return responses   