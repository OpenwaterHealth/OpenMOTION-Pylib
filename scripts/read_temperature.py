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
        
    motion_ctrl = CTRL_IF(s)

    delay_time = .01
    time.sleep(1)

    await motion_ctrl.switch_camera(5)
    time.sleep(delay_time*3)
    
    print("camera read temp")    
    temp = await motion_ctrl.read_camera_temp()
    print("Camera temperature: ", temp," C")
    time.sleep(delay_time)

    s.close()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("App was interrupted")
    finally:
        print("App was finished gracefully")