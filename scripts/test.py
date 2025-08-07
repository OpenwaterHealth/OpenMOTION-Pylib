import asyncio
import time
import argparse
from omotion.Interface import MOTIONInterface
from omotion.Sensor import MOTIONSensor

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test.py 

print("Starting MOTION Sensor Test Script...")

def run_sensor_tests(interface) -> bool:
    # Ping Test
    print("\n[1] Ping Sensor Module...")
    ping_results = interface.run_on_sensors("ping")
    print(ping_results)  

    # Get Firmware Version
    print("\n[2] Reading Firmware Version...")
    version_results = interface.run_on_sensors("get_version")
    print(version_results)  

    # Get HWID
    print("\n[5] Read Hardware ID...")
    hwid_results = interface.run_on_sensors("get_hardware_id")
    print(hwid_results)  

    mask = 0xFF
    status_results = interface.run_on_sensors("get_camera_status", mask)

    for side, status_map in status_results.items():
        if status_map is None:
            print(f"[{side.capitalize()}] Failed to get camera status.")
            continue

        print(f"\n[{side.capitalize()}] Camera Status:")
        for cam_id, status in status_map.items():
            readable = MOTIONSensor.decode_camera_status(status)
            print(f"  Camera {cam_id} Status: 0x{status:02X} -> {readable}")
        
    
    return True

def main():

    # Acquire interface + connection state
    interface, console_connected, left_sensor, right_sensor = MOTIONInterface.acquire_motion_interface()

    if console_connected and left_sensor and right_sensor:
        print("MOTION System fully connected.")
    else:
        print(f'MOTION System NOT Fully Connected. CONSOLE: {console_connected}, SENSOR (LEFT,RIGHT): {left_sensor}, {right_sensor}')

    if not left_sensor and not right_sensor:
        print("Sensor Module not connected.")
        exit(1)

    run_sensor_tests(interface)

    print("\nSensor Module Test Completed.")
    
if __name__ == "__main__":
    main()
