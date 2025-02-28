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
        
        com_port = list_vcp_with_vid_pid(vid, pid)
        if com_port is None:
            print("No device found")
        else:
            print("Device found at port: ", com_port)
            # Select communication port
            s = UART(com_port, timeout=5)
    else:
        s = UART(PORT_NAME, timeout=5)
        
    # Calculate CRC of the specified file
    file_crc = calculate_file_crc(FILE_NAME)
    print(f"CRC16 of file {FILE_NAME}: {hex(file_crc)}")

    motion_ctrl = CTRL_IF(s)

    spi_cameras_to_flash = [2, 6, 7, 8]
    usart_cameras_to_flash = [1, 3, 4, 5]
    cameras_to_flash = [1,3]# spi_cameras_to_flash + usart_cameras_to_flash
    
    for i in cameras_to_flash:
        print(f"Switching to Camera {i}")
        await motion_ctrl.switch_camera(i)
        time.sleep(1)
        print("FPGA Configuration Started")
        r = await motion_ctrl.fpga_reset()
        time.sleep(.1)

        r = await motion_ctrl.fpga_activate()
        time.sleep(.1)
        r = await motion_ctrl.fpga_on()
        time.sleep(1)

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
        print("Camera Configuration Done")

        time.sleep(.25)

        print(f"Finished Camera {i}")

    s.close()

asyncio.run(main())