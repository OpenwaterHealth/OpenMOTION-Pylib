import asyncio
import time
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_console_dfu.py

def main():
    print("Starting MOTION Console Module Test Script...")

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


    # Ask the user for confirmation
    user_input = input("Do you want to Enter DFU Mode? (y/n): ").strip().lower()

    if user_input == 'y':
        print("Enter DFU mode")
        if interface.console_module.enter_dfu():
            print("Successful.")
    elif user_input == 'n':
        if interface.console_module.soft_reset():
            print("Successful.")
    else:
        print("Invalid input. Please enter 'y' or 'n'.")

        
if __name__ == "__main__":
    main()
