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
    FILE_NAME= "HistoFPGAFw_impl1.bit"
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
        
    # Calculate CRC of the specified file
    file_crc = calculate_file_crc(FILE_NAME)
    print(f"CRC16 of file {FILE_NAME}: {hex(file_crc)}")

    motion_ctrl = CTRL_IF(s)

    print("Pong Controller")
    # Send and Recieve General ping command
    r = await motion_ctrl.pong()
    # Format and print the received data in hex format
    r.print_packet()
        
    print("Version Controller")
    # Send and Recieve General ping command
    r = await motion_ctrl.version()    
    # Format and print the received data in hex format
    r.print_packet()

    print("Echo Controller")
    # Send and Recieve General ping command
    r = await motion_ctrl.echo(data=b'Hello World')    
    # Format and print the received data in hex format
    r.print_packet()

    print("Toggle LED Controller")
    # Send and Recieve General ping command
    r = await motion_ctrl.toggle_led()    
    # Format and print the received data in hex format
    r.print_packet()

    print("CHIP ID Controller")
    # Send and Recieve General ping command
    r = await motion_ctrl.chipid()    
    # Format and print the received data in hex format
    r.print_packet()
    if True:
        print("FPGA Configuration Started")
        r = await motion_ctrl.fpga_reset()
        format_and_print_hex(r)

        r = await motion_ctrl.fpga_activate()
        format_and_print_hex(r)

        r = await motion_ctrl.fpga_on()
        format_and_print_hex(r)

        r = await motion_ctrl.fpga_id()
        format_and_print_hex(r)
        
        r = await motion_ctrl.fpga_enter_sram_prog()
        format_and_print_hex(r)
        
        r = await motion_ctrl.fpga_erase_sram()
        format_and_print_hex(r)

        r = await motion_ctrl.fpga_status()
        format_and_print_hex(r)
        
        if False:
            r = await motion_ctrl.fpga_program_sram()
            format_and_print_hex(r)
        else:
            r = await motion_ctrl.send_bitstream(filename=FILE_NAME)
            for resp in r:
                format_and_print_hex(resp)

        r = await motion_ctrl.fpga_usercode()
        format_and_print_hex(r)

        r = await motion_ctrl.fpga_status()
        format_and_print_hex(r)

        r = await motion_ctrl.fpga_exit_sram_prog()
        format_and_print_hex(r)
        print("FPGA Configuration Completed")

    print("Camera Configuration Started")

    #r = await motion_ctrl.camera_configure_registers()
    #format_and_print_hex(r)

    s.close()

asyncio.run(main())