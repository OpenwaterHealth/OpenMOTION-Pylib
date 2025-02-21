import asyncio
from omotion import *
import json
import time

# Function to read file and calculate CRC
def calculate_file_crc(file_name):
    with open(file_name, 'rb') as f:
        file_data = f.read()
        crc = util_crc16(file_data)
        return crc

async def main():
    CTRL_BOARD = True  # change to false and specify PORT_NAME for Nucleo Board
    PORT_NAME = "COM16"
    s = None

    if CTRL_BOARD:
        vid = 1155  # Example VID for demonstration
        pid = 23130  # Example PID for demonstration
        
        com_port = list_vcp_with_vid_pid(vid, pid)
        if com_port is None:
            print("No device found")
        else:
            print("Device found at port: ", com_port)
            # Select communication port
            s = UART(com_port, timeout=5)
    else:
        s = UART(PORT_NAME, timeout=5)

    motion_ctrl = CTRL_IF(s)

    print("Pong Controller")
    # Send and Recieve General ping command
    r = await motion_ctrl.pong()
    # Format and print the received data in hex format
    r.print_packet()

    #Send the preamble
    await asyncio.sleep(0.005)
    
    await motion_ctrl.camera_i2c_write(I2C_Packet(id=0,device_address=0x36,register_address=0x0100,data=0x00))
    await asyncio.sleep(0.05)
    await motion_ctrl.camera_i2c_write(I2C_Packet(id=1,device_address=0x36,register_address=0x0107,data=0x01))
    await asyncio.sleep(0.015) 
    
    #Delay for at least 5ms
    await asyncio.sleep(0.005)

    #Send the i2c config
    csv_file_path = 'camera_config_partial.csv'
    i2c_packets = I2C_Packet.read_csv_to_i2c_packets(csv_file_path)
    for packet in i2c_packets:
        await motion_ctrl.camera_i2c_write(packet)
        await asyncio.sleep(0.0001)
    
    s.close()

asyncio.run(main())