import asyncio
from omotion import *
import json
import time


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
        

    ctrl_interface = CTRL_IF(s)

    print("Camera Stream on")
    # Send and Recieve General ping command
    r = await ctrl_interface.camera_stream_on()
    # Format and print the received data in hex format
    r.print_packet()
    
    time.sleep(0.01)

    print("FSIN On")
    r = await ctrl_interface.camera_fsin_on()
    # Format and print the received data in hex format

    r.print_packet()
    

    await s.start_telemetry_listener()

    print("Version Controller")
    # Send and Recieve General ping command
    r = await ctrl_interface.version()    
    # Format and print the received data in hex format
    r.print_packet()
    
    await asyncio.sleep(3600)  # Run for 1 hour, adjust as needed


    s.close()

asyncio.run(main())