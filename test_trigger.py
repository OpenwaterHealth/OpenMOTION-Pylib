import asyncio
from omotion import *
import json
import time

async def main():
    CTRL_BOARD = True  # change to false and specify PORT_NAME for Nucleo Board
    PORT_NAME = "COM16"
    FILE_NAME = "test.bit"  # Specify your file here
    s = None

    if CTRL_BOARD:
        vid = 0x483  # Example VID for demonstration
        pid = 0xA53E  # Example PID for demonstration
        
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

    print("Ping Controller")
    # Send and Recieve General ping command
    r = await motion_ctrl.ping()
    # Format and print the received data in hex format
    format_and_print_hex(r)

    print("Get Current Trigger")
    r = await motion_ctrl.get_trigger()
    print(r)

    print("Trigger Start")
    # Send and Recieve General ping command
    r = await motion_ctrl.start_trigger()
    # Format and print the received data in hex format
    format_and_print_hex(r)

    print("Trigger Stop")
    # Send and Recieve General ping command
    r = await motion_ctrl.stop_trigger()
    # Format and print the received data in hex format
    format_and_print_hex(r)


    print("Set Sync Trigger")
    json_trigger_data = {
        "TriggerFrequencyHz": 20,
        "TriggerPulseWidthUsec": 500,
        "LaserPulseDelayUsec": 100,
        "LaserPulseWidthUsec": 200
    }

    print(json_trigger_data)
    r = await motion_ctrl.set_trigger(data=json_trigger_data)
    format_and_print_hex(r)

    print("Trigger Start")
    # Send and Recieve General ping command
    r = await motion_ctrl.start_trigger()
    # Format and print the received data in hex format
    format_and_print_hex(r)

    print("Trigger Stop")
    # Send and Recieve General ping command
    r = await motion_ctrl.stop_trigger()
    # Format and print the received data in hex format
    format_and_print_hex(r)


    s.close()

asyncio.run(main())