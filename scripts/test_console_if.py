import asyncio
import time
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_console_if.py

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

# Get Firmware Version
print("\n[2] Reading Firmware Version...")
try:
    version = interface.console_module.get_version()
    print(f"Firmware Version: {version}")
except Exception as e:
    print(f"Error reading version: {e}")

# Echo Test
print("\n[3] Echo Test...")
try:
    echo_data = b"Hello LIFU!"
    echoed, echoed_len = interface.console_module.echo(echo_data)
    if echoed:
        print(f"Echoed {echoed_len} bytes: {echoed.decode(errors='ignore')}")
    else:
        print("Echo failed.")
except Exception as e:
    print(f"Echo test error: {e}")

# Toggle LED
print("\n[4] Toggle LED...")
try:
    led_result = interface.console_module.toggle_led()
    print("LED toggled." if led_result else "LED toggle failed.")
    time.sleep(1)  # Wait for a second before toggling off
    led_result = interface.console_module.toggle_led()
except Exception as e:
    print(f"LED toggle error: {e}")

# Get HWID
print("\n[5] Read Hardware ID...")
try:
    hwid = interface.console_module.get_hardware_id()
    if hwid:
        print(f"Hardware ID: {hwid}")
    else:
        print("Failed to read HWID.")
except Exception as e:
    print(f"HWID read error: {e}")