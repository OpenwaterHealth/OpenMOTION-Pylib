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

# Read IMU Accelerometer
print("\n[3] Reading IMU Accelerometer...")
accel = interface.sensor_module.imu_get_accelerometer()
print(f"Accel (raw): X={accel[0]}, Y={accel[1]}, Z={accel[2]}")

# Read IMU Gyroscope
print("\n[4] Reading IMU Gyroscope...")
gyro  = interface.sensor_module.imu_get_gyroscope()
print(f"Gyro  (raw): X={gyro[0]}, Y={gyro[1]}, Z={gyro[2]}")


# disconnect from the sensor module
interface.sensor_module.disconnect()
print("\nSensor Module Test Completed.")

exit(0)
