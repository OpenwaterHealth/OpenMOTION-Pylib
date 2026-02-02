import asyncio
import time
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_get_senosr_fw.py

def main():

    print("Starting MOTION Sensor Module Test Script...")

    # Acquire interface + connection state
    interface, console_connected, left_sensor, right_sensor = MOTIONInterface.acquire_motion_interface()

    if console_connected and left_sensor and right_sensor:
        print("MOTION System fully connected.")
    else:
        print(f'MOTION System NOT Fully Connected. CONSOLE: {console_connected}, SENSOR (LEFT,RIGHT): {left_sensor}, {right_sensor}')
    
    
    # Get Firmare version info
    print("\n[1] Get Firmare version info...")
    response = interface.sensors.get("left").get_latest_version_info()
    print(response)

if __name__ == "__main__":
    main()