import asyncio
from omotion import *
import json
import time

async def main():
    CTRL_BOARD = True  # change to false and specify PORT_NAME for Nucleo Board
    PORT_NAME = "COM16"
    FILE_NAME = "HistoFPGAFw.bit"  # Specify your file here
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

    print("FSIN Off")
    r = await motion_ctrl.camera_fsin_off()
    # Format and print the received data in hex format
    r.print_packet()
    

    print("Camera Stream off")
    # Send and Recieve General ping command
    r = await motion_ctrl.camera_stream_off()
    # Format and print the received data in hex format
    r.print_packet()
    
    s.close()

asyncio.run(main())