import asyncio
import time
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_console_trigger.py

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

print("\n[1] Get trigger...")
trigger_setting = interface.console_module.get_trigger_json()
if trigger_setting:
    print(f"Trigger Setting: {trigger_setting}")
else:
    print("Failed to get trigger setting.")

print("\n[2] Start trigger...")
if not interface.console_module.start_trigger():
    print("Failed to start trigger.")
else:
    print("Press [ENTER] to stop trigger...")
    input()
    interface.console_module.stop_trigger()

print("\n[3] Set trigger...")
json_trigger_data = {
    "TriggerFrequencyHz": 25,
    "TriggerPulseWidthUsec": 500,
    "LaserPulseDelayUsec": 100,
    "LaserPulseWidthUsec": 500,
    "EnableSyncOut": True,
    "EnableTaTrigger": True
}

new_setting = interface.console_module.set_trigger_json(data=json_trigger_data)
if new_setting:
    print(f"Trigger Setting: {new_setting}")
else:
    print("Failed to get trigger setting.")

print("\n[4] Start trigger...")
if not interface.console_module.start_trigger():
    print("Failed to start trigger.")
else:    
    trigger_setting = interface.console_module.get_trigger_json()
    if trigger_setting:
        print(f"Trigger Setting: {trigger_setting}")
    else:
        print("Failed to get trigger setting.")

    print("Press [ENTER] to stop trigger...")
    input()
    interface.console_module.stop_trigger()