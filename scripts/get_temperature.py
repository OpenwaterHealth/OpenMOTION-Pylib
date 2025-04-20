import asyncio
import time
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\get_temperature.py


print("Starting MOTION Sensor Module Test Script...")

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

# Read IMU Temperature
print("\n[2] Reading IMU Temperature...")
temperature = interface.sensor_module.imu_get_temperature()
print(f"Temperature: {temperature} °C")

# disconnect from the sensor module
interface.sensor_module.disconnect()
print("\nSensor Module Test Completed.")

exit(0)
