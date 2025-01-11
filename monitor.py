import asyncio
from omotion import *
import json
import time
import sys

async def main():
    # Set all the constants/configs
    CTRL_BOARD = True  # change to false and specify PORT_NAME for Nucleo Board
    PORT_NAME = "COM16"

    delay_time = .01
    gain = 8
    exposure = 2
    monitor_time = 5
    test_pattern = -1

    s = None
    if CTRL_BOARD:
        vid = 1155  # Example VID for demonstration
        pid = 23130  # Example PID for demonstration
        
        com_port = list_vcp_with_vid_pid(vid, pid)
        if com_port is None:
            exit()
        else:
            print("Device found at port: ", com_port)
            # Select communication port
            s = UART(com_port, timeout=5)
    else:
        s = UART(PORT_NAME, timeout=5)
    motion_ctrl = CTRL_IF(s)

    time.sleep(delay_time)

    if len(sys.argv) < 2:
        camera_id = 1
    try:
        # Get the first command line argument and convert it to an integer
        camera_id = int(sys.argv[1])
    except ValueError:
        print("Error: <camera_value> must be an integer.")
        sys.exit(1)

    r = await motion_ctrl.version()   
    print("FW Version: " + r.data.hex())

    await motion_ctrl.switch_camera(camera_id)
    time.sleep(1)
    
    temp = await motion_ctrl.read_camera_temp()
    print("Camera temperature: ", temp," C")
    time.sleep(delay_time)

    print("Gain: " + str(gain))    
    await motion_ctrl.camera_set_gain(gain)
    time.sleep(delay_time)

    print("camera set exposure to setting " + str(exposure))    
    await motion_ctrl.camera_set_exposure(exposure)
    time.sleep(delay_time)

    # print("camera set rgbir")    
    # await motion_ctrl.camera_set_rgbir(2)
    # time.sleep(delay_time)

    if(test_pattern != -1):
        print("Set test pattern to " + str(test_pattern))    
        await motion_ctrl.camera_enable_test_pattern(test_pattern)
    else:  
        print("Disable test pattern")    
        await motion_ctrl.camera_disable_test_pattern()
    time.sleep(delay_time)

    # print("FPGA Soft Reset")
    # await motion_ctrl.fpga_soft_reset()
    # time.sleep(delay_time)
    
    print("Camera Stream on")
    r = await motion_ctrl.camera_stream_on()    
    time.sleep(delay_time)

    print("FSIN On")
    r = await motion_ctrl.camera_fsin_on()

    try:
        await s.start_telemetry_listener(timeout=monitor_time)
    finally:
        time.sleep(delay_time)
        print("FSIN Off")
        await motion_ctrl.camera_fsin_off()
        
        time.sleep(delay_time*3)
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