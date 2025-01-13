import asyncio
from omotion import *
import json
import time
import sys

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
    FILE_NAME= "HistoFPGAFw_impl1_test.bit" # data out = bin
    FILE_NAME= "HistoFPGAFw_impl1_test3.bit" # 
    FILE_NAME= "HistoFPGAFw_impl1.bit" # data out = bin

    FILE_NAME= "C:/Users/ethanhead/Desktop/gen3-cam-fw/HistoFPGAFw/impl1/HistoFPGAFw_impl1.bit"      #working
    FILE_NAME= "HistoFPGAFw_impl1_agg.bit" #

    s = None

    if CTRL_BOARD:
        vid = 1155  # Example VID for demonstration
        pid = 23130  # Example PID for demonstration
        
        com_port = list_vcp_with_vid_pid(vid, pid)
        if com_port is None:
            exit()
        else:
            print("Using device at port: ", com_port)
            # Select communication port
            s = UART(com_port, timeout=5)
    else:
        s = UART(PORT_NAME, timeout=5)
    
    if len(sys.argv) < 2:
        camera_id = 1
    try:
        # Get the first command line argument and convert it to an integer
        camera_id = int(sys.argv[1])
    except ValueError:
        print("Error: <camera_value> must be an integer.")
        sys.exit(1)

    # Calculate CRC of the specified file
    file_crc = calculate_file_crc(FILE_NAME)
    # print(f"CRC16 of file {FILE_NAME}: {hex(file_crc)}")

    motion_ctrl = CTRL_IF(s)

    r = await motion_ctrl.version()   
    print("FW Version: " + r.data.hex())

    await motion_ctrl.switch_camera(camera_id)
    time.sleep(1)

    print("FPGA Configuration Started")
    r = await motion_ctrl.fpga_reset()       # Take cresetb hi for 250ms then low for 1sec
    r = await motion_ctrl.fpga_activate()    # send activation key
    time.sleep(.1)
    r = await motion_ctrl.fpga_on()          # set cresetb hi again (10ms delay)

    r = await motion_ctrl.fpga_id()
    r = await motion_ctrl.fpga_enter_sram_prog()
    r = await motion_ctrl.fpga_erase_sram()
    r = await motion_ctrl.fpga_status()

    r = await motion_ctrl.send_bitstream(filename=FILE_NAME)

    r = await motion_ctrl.fpga_usercode()
    r = await motion_ctrl.fpga_status()
    r = await motion_ctrl.fpga_exit_sram_prog()

    print("FPGA Configuration Completed")

    print("Camera Configuration Started")
    r = await motion_ctrl.camera_configure_registers()
    r = await motion_ctrl.fpga_soft_reset()
    print("Camera Configuration Completed")

    s.close()

asyncio.run(main())