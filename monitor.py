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

    print("FPGA Soft Reset")
    await motion_ctrl.fpga_soft_reset()
    
    time.sleep(delay_time)

    print("Camera Stream on")
    r = await motion_ctrl.camera_stream_on()
    r.print_packet()
    
    time.sleep(delay_time)

    print("FSIN On")
    r = await motion_ctrl.camera_fsin_on()
    r.print_packet()
    

    print("Version Controller")
    r = await motion_ctrl.version()    
    r.print_packet()

    try:
        await s.start_telemetry_listener(timeout=5)
    finally:
        
        time.sleep(delay_time)
        print("FSIN Off")
        await motion_ctrl.camera_fsin_off()
        
        time.sleep(delay_time)
        print("Stream Off")
        await motion_ctrl.camera_stream_off()
        s.close()
        print("Exiting the program.")
    s.close()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("App was interrupted")
    finally:
        print("App was finished gracefully")