import asyncio
import time
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_fpga_if.py

print("Starting MOTION Console FPGA Test Script...")

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

# Read Firmware Version
print("\n[2] Reading Firmware Version...")
try:
    version = interface.console_module.get_version()
    print(f"Firmware Version: {version}")
except Exception as e:
    print(f"Error reading version: {e}")

# Read FPGA Test
print("\n[3] Read data from FPGA register...")
try:
    fpga_data, fpga_data_len = interface.console_module.read_i2c_packet(mux_index=1, channel=5, device_addr=0x41, reg_addr=0x00, read_len=2)
    if fpga_data is None:
        print(f"Read FPGA Failed")
    else:
        print(f"Read FPGA Success")
        print(f"Raw bytes: {fpga_data.hex(' ')}")  # Print as hex bytes separated by spaces

except Exception as e:
    print(f"Error writing FPGA register: {e}")

# Write FPGA Test
print("\n[4] Write data to FPGA register...")
try:
    if interface.console_module.write_i2c_packet(mux_index=1, channel=5, device_addr=0x41, reg_addr=0x00, data=b'\x03\x21'):
        print(f"Write FPGA Success")
    else:
        print(f"Write FPGA Failed")
except Exception as e:
    print(f"Error writing FPGA register: {e}")
