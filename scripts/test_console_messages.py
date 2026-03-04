import asyncio
import time
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_console_messages.py

def main():
    print("Starting MOTION Console Messages Test Script...")

    # Acquire interface + connection state
    interface, console_connected, left_sensor, right_sensor = MOTIONInterface.acquire_motion_interface()

    if console_connected and left_sensor and right_sensor:
        print("MOTION System fully connected.")
    else:
        print(f'MOTION System NOT Fully Connected. CONSOLE: {console_connected}, SENSOR (LEFT,RIGHT): {left_sensor}, {right_sensor}')
        
    if not console_connected:
        print("Console Module not connected.")
        exit(1)
        
    # Ping Test
    print("\n[1] Ping Sensor Module...")
    response = interface.console_module.ping()
    print("Ping successful." if response else "Ping failed.")

    # Get Messages Test
    print("\n[2] Get Messages from Console Module...")
    try:
        messages = interface.console_module.get_messages()
        print(f"Messages received: '{messages}'")
    except Exception as e:
        print(f"Error getting messages: {e}")
        
        
if __name__ == "__main__":
    main()