import asyncio
import time
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_receive_frame.py

print("Starting MOTION Sensor Module Test Script...")
BIT_FILE = "bitstream/HistoFPGAFw_impl1_agg.bit"
#BIT_FILE = "bitstream/testcustom_agg.bit"
AUTO_UPLOAD = True
# MANUAL_UPLOAD = True
CAMERA_MASK = 0x40

# Create an instance of the Sensor interface
interface = MOTIONInterface()

# Check if console and sensor are connected
console_connected, sensor_connected = interface.is_device_connected()

if console_connected and sensor_connected:
    print("MOTION System fully connected.")
else:
    print(f'MOTION System NOT Fully Connected. CONSOLE: {console_connected}, SENSOR: {sensor_connected}')
    
if not sensor_connected:
    print("Sensor Module not connected.")
    exit(1)

# Ping Test
print("\n[1] Ping Sensor Module...")
response = interface.sensor_module.ping()
print("Ping successful." if response else "Ping failed.")

# Get Firmware Version
print("\n[2] Reading Firmware Version...")
try:
    version = interface.sensor_module.get_version()
    print(f"Firmware Version: {version}")
except Exception as e:
    print(f"Error reading version: {e}")


# print ("Programming camera sensor registers.")
# if not interface.sensor_module.camera_configure_registers(CAMERA_MASK):
#     print("Failed to configure default registers for camera FPGA.")

# print ("Programming camera sensor set test pattern.")
# if not interface.sensor_module.camera_configure_test_pattern(CAMERA_MASK):
#      print("Failed to set grayscale test pattern for camera FPGA.")

print("Capture histogram frame.")
if not interface.sensor_module.camera_capture_histogram(CAMERA_MASK):
    print("Failed to capture histogram frame.")

# Disconnect and cleanup;'.l/m 1
interface.sensor_module.disconnect()
print("\nSensor Module Test Completed.")

exit(0)