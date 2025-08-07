
import time
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\set_trigger_laser.py


def main():
    
    print("Starting MOTION Console Trigger Laser Script...")

    # Acquire interface + connection state
    interface, console_connected, left_sensor, right_sensor = MOTIONInterface.acquire_motion_interface()

    if console_connected and left_sensor and right_sensor:
        print("MOTION System fully connected.")
    else:
        print(f'MOTION System NOT Fully Connected. CONSOLE: {console_connected}, SENSOR (LEFT,RIGHT): {left_sensor}, {right_sensor}')

    if not console_connected:
        print("Console Module not connected.")
        exit(1)
    
    json_trigger_data = {
        "TriggerFrequencyHz": 40,
        "TriggerPulseWidthUsec": 500,
        "LaserPulseDelayUsec": 100,
        "LaserPulseWidthUsec": 500,
        "LaserPulseSkipInterval": 600,
        "EnableSyncOut": True,
        "EnableTaTrigger": True
    }

    print("\n[0] Set trigger...")
    new_setting = interface.console_module.set_trigger_json(data=json_trigger_data)
    if new_setting:
        print(f"Trigger Setting: {new_setting}")
    else:
        print("Failed to set trigger setting.")
        exit(1)

    #Set laser power
    print("\n[2] Set laser power...")

    laser_params = []
    #TA params
    #2a config
    # laser_params.append({"muxIdx": 1,   
    #                      "channel": 4, 
    #                      "i2cAddr": 0x41, 
    #                      "offset": 0x00,
    #                      "dataToSend": bytearray([0x28, 0x09, 0x00])}) 
    # laser_params.append({"muxIdx": 1,   
    #                      "channel": 4, 
    #                      "i2cAddr": 0x41, 
    #                      "offset": 0x06,
    #                      "dataToSend": bytearray([0xD9, 0x30])}) 
    # 5a config
    laser_params.append({"muxIdx": 1,   
                        "channel": 4, 
                        "i2cAddr": 0x41, 
                        "offset": 0x00,
                        "dataToSend": bytearray([0x28, 0x09, 0x00])}) 
    laser_params.append({"muxIdx": 1,   
                        "channel": 4, 
                        "i2cAddr": 0x41, 
                        "offset": 0x06,
                        "dataToSend": bytearray([0x17, 0x7a])}) 


    #Seed Params
    laser_params.append({"muxIdx": 1,   
                        "channel": 5, 
                        "i2cAddr": 0x41, 
                        "offset": 0x02,
                        "dataToSend": bytearray([0x00, 0x00])}) 
    laser_params.append({"muxIdx": 1,   
                        "channel": 5, 
                        "i2cAddr": 0x41, 
                        "offset": 0x06,
                        "dataToSend": bytearray([0xAE, 0x3d])}) 
    laser_params.append({"muxIdx": 1,   
                        "channel": 5, 
                        "i2cAddr": 0x41, 
                        "offset": 0x04,
                        "dataToSend": bytearray([0x53, 0x07])}) 
    laser_params.append({"muxIdx": 1,   
                        "channel": 5, 
                        "i2cAddr": 0x41, 
                        "offset": 0x08,
                        "dataToSend": bytearray([0xd7, 0x1e])}) 

    for laser_param in laser_params:
        muxIdx = laser_param["muxIdx"]
        channel = laser_param["channel"]
        i2cAddr = laser_param["i2cAddr"]
        offset = laser_param["offset"]
        dataToSend = laser_param["dataToSend"]

        if not interface.console_module.write_i2c_packet(mux_index=muxIdx, channel=channel, device_addr=i2cAddr, reg_addr=offset, data=dataToSend):
            print("Failed to set laser power.")
            exit(1)

    print("Laser power set successfully.")

if __name__ == "__main__":
    main()