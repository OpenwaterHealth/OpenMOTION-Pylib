import asyncio
from omotion import *
import json
import time
import sys

async def main():
    CTRL_BOARD = True  # change to false and specify PORT_NAME for Nucleo Board
    PORT_NAME = "COM16"

    s = None

    if CTRL_BOARD:
        vid = 0x483  # Example VID for demonstration
        pid = 0x5A5A  # Example PID for demonstration
        
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

    motion_ctrl = CTRL_IF(s)

    r = await motion_ctrl.version()   
    print("FW Version: " + r.data.hex())

    await motion_ctrl.switch_camera(camera_id)
    time.sleep(1)

    print("FPGA Reset")
    r = await motion_ctrl.fpga_reset()       # Take cresetb hi for 250ms then low for 1sec
    r.print_packet()
    s.close()

asyncio.run(main())