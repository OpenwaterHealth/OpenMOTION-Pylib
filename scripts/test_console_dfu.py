import asyncio
import time
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_console_dfu.py

print("Starting MOTION Console Module Test Script...")

# Create an instance of the Sensor interface
interface = MOTIONInterface()

# Check if console and sensor are connected
console_connected, sensor_connected = interface.is_device_connected()

if console_connected and sensor_connected:
    print("MOTION System fully connected.")
else:
    print(f'MOTION System NOT Fully Connected. CONSOLE: {console_connected}, SENSOR: {sensor_connected}')
    
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
    pass
else:
    print("Invalid input. Please enter 'y' or 'n'.")

