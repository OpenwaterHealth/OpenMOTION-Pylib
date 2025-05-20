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
    FILE_NAME = "test_cam.bit"  # Specify your file here
    FILE_NAME= "HistoFPGAFw_impl1_agg.bit"
    s = None
    delay_time = .01

    if CTRL_BOARD:
        vid = 1155  # Example VID for demonstration
        pid = 23130  # Example PID for demonstration
        
        devices = list_vcp_with_vid_pid(vid, pid)
        if devices is None:
            exit()
        else:
            com_port = devices[1]
            print("Device found at port: ", com_port)
            s = UART(com_port, timeout=5)
    else:
        s = UART(PORT_NAME, timeout=5)
        
    # Calculate CRC of the specified file
    file_crc = calculate_file_crc(FILE_NAME)
    print(f"CRC16 of file {FILE_NAME}: {hex(file_crc)}")

    motion_ctrl = CTRL_IF(s)

    await motion_ctrl.enable_i2c_broadcast()
    time.sleep(delay_time)

    print("Camera Stream on")
    r = await motion_ctrl.camera_stream_on()    
    time.sleep(delay_time)

    print("FSIN On")
    r = await motion_ctrl.camera_fsin_on()

    try:
        # await s.start_telemetry_listener(timeout=5)
        time.sleep(5)
    
    finally:
        
        time.sleep(delay_time)
        print("FSIN Off")
        await motion_ctrl.camera_fsin_off()
        
        time.sleep(delay_time*3)
        print("Stream Off")
        await motion_ctrl.camera_stream_off()
        s.close()
        print("Exiting the program.")
    s.close()

asyncio.run(main())