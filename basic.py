import asyncio
from omotion import *

async def main():
    CTRL_BOARD = True  # change to false and specify PORT_NAME for Nucleo Board
    PORT_NAME = "COM16"
    FILE_NAME = "test.bit"  # Specify your file here
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

    ustx_ctrl = CTRL_IF(s)

    print("Ping Controller")
    # Send and Recieve General ping command
    r = await ustx_ctrl.ping()
    # Format and print the received data in hex format
    format_and_print_hex(r)
    r = await ustx_ctrl.ping()
    # Format and print the received data in hex format
    format_and_print_hex(r)
    
    

    s.close()

asyncio.run(main())
        
