import asyncio
from omotion import *
import json
import time

async def main():
    CTRL_BOARD = True  # change to false and specify PORT_NAME for Nucleo Board
    PORT_NAME = "COM16"
    s = None
    trigger_on_time_sec = 1

    verbose = False

    if CTRL_BOARD:
        vid = 0x483  # Example VID for demonstration
        pid = 0xA53E  # Example PID for demonstration
        
        com_port = list_vcp_with_vid_pid(vid, pid)[0]
        if com_port is None:
            print("No device found")
        else:
            print("Device found at port: ", com_port)
            # Select communication port
            s = UART(com_port, timeout=5)
    else:
        s = UART(PORT_NAME, timeout=5)
        
    motion_ctrl = CTRL_IF(s)

    print("Set Sync Trigger")
    json_trigger_data = {
        "TriggerFrequencyHz": 40,
        "TriggerPulseWidthUsec": 12500,
        "LaserPulseDelayUsec": 100,
        "LaserPulseWidthUsec": 200
    }

    print(json_trigger_data)
    r = await motion_ctrl.set_trigger(data=json_trigger_data)
    if(verbose): r.print_packet(full=True)

    print("Trigger Start")
    # Send and Recieve General ping command
    r = await motion_ctrl.start_trigger()
    # Format and print the received data in hex format
    if(verbose): r.print_packet(full=True)

    time.sleep(trigger_on_time_sec)
    print("Trigger Stop")
    # Send and Recieve General ping command
    r = await motion_ctrl.stop_trigger()
    # Format and print the received data in hex format
    if(verbose): r.print_packet(full=True)


    s.close()

asyncio.run(main())