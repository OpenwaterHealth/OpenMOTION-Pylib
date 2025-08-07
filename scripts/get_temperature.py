import asyncio
import time
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\get_temperature.py


def main():

    print("Starting MOTION Sensor Module Test Script...")

    # Acquire interface + connection state
    interface, console_connected, left_sensor, right_sensor = MOTIONInterface.acquire_motion_interface()

    if console_connected and left_sensor and right_sensor:
        print("MOTION System fully connected.")
    else:
        print(f'MOTION System NOT Fully Connected. CONSOLE: {console_connected}, SENSOR (LEFT,RIGHT): {left_sensor}, {right_sensor}')

    if not left_sensor and not right_sensor:
        print("Sensor Module not connected.")
        exit(1)

    # Ping Test
    print("\n[1] Ping Sensor Modules...")
    ping_results = interface.run_on_sensors("ping")
    for side, result in ping_results.items():
        print(f"{side.capitalize()} ping: {'✅ Success' if result else '❌ Failed'}")

    # Read IMU Temperature
    print("\n[2] Reading IMU Temperature...")
    temp_results = interface.run_on_sensors("imu_get_temperature")
    for side, temp in temp_results.items():
        if temp is not None:
            print(f"{side.capitalize()} temperature: {temp} °C")
        else:
            print(f"{side.capitalize()} temperature: ❌ Failed to read")

    # Read IMU Accelerometer
    print("\n[3] Reading IMU Accelerometer...")
    accel_results = interface.run_on_sensors("imu_get_accelerometer")
    for side, accel in accel_results.items():
        if accel:
            print(f"{side.capitalize()} accel (raw): X={accel[0]}, Y={accel[1]}, Z={accel[2]}")
        else:
            print(f"{side.capitalize()} accel: ❌ Failed to read")

    # Read IMU Gyroscope
    print("\n[4] Reading IMU Gyroscope...")
    gyro_results = interface.run_on_sensors("imu_get_gyroscope")
    for side, gyro in gyro_results.items():
        if gyro:
            print(f"{side.capitalize()} gyro (raw): X={gyro[0]}, Y={gyro[1]}, Z={gyro[2]}")
        else:
            print(f"{side.capitalize()} gyro: ❌ Failed to read")


    print("\nSensor Module Test Completed.")

    
if __name__ == "__main__":
    main()
