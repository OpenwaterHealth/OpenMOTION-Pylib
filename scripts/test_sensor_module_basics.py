import asyncio, time
import argparse
import sys

from omotion.Interface import MOTIONInterface


# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_sensor_module_basics.py

def parse_args():
    parser = argparse.ArgumentParser(description="Starting MOTION Sensor Module Test Script")
    parser.add_argument('--iter', type=int, default=100, help='Iterations to run (default: 1)')

    args = parser.parse_args()

    return args

def run(sensor_module) -> bool:
    try:
        # Ping Test
        response = sensor_module.ping()
        print("Ping successful." if response else "Ping failed.")
        if not response:
            return False

        # Get Firmware Version
        version = sensor_module.get_version()                
        # Perform the version check
        if version.startswith("v"):
            print(f"Firmware Version: {version}")
        else:
            print(f"Warning: Expected firmware version vX.X.X found {version}")
            return False              

        # Echo Test
        echo_data = b"Hello MOTION!"
        echoed, echoed_len = sensor_module.echo(echo_data)
        if echoed:
            echoed_str = echoed.decode(errors='ignore')
            print(f"Echoed {echoed_len} bytes: {echoed_str}")
            if echo_data != echoed:
                print("Echo failed.")
                return False
        else:
            print("Echo failed.")
            return False

        # Toggle LED
        led_result = sensor_module.toggle_led()
        print("LED toggled." if led_result else "LED toggle failed.")
        if not led_result:  
            return False
        
        # Toggle LED
        led_result = sensor_module.toggle_led()
        print("LED toggled." if led_result else "LED toggle failed.")
        if not led_result:  
            return False
        
        # Get HWID
        try:
            hwid = sensor_module.get_hardware_id()
            if hwid:
                print(f"Hardware ID: {hwid}")
            else:
                print("Failed to read HWID.")
                return False
        except Exception as e:
            print(f"HWID read error: {e}")

        imu_temp = sensor_module.imu_get_temperature()  
        print(f"Temperature Data - IMU Temp: {imu_temp}")

        accel = sensor_module.imu_get_accelerometer()
        print(f"Accel (raw): X={accel[0]}, Y={accel[1]}, Z={accel[2]}")

        # Query status of camera 0, 3, and 7 (bitmask 0b10001001 = 0x89)
        mask = 0xFF
        try:
            status_map = sensor_module.get_camera_status(mask)

            if status_map is None:
                print("Failed to get camera status.")
            else:
                for cam_id, status in status_map.items():
                    readable = sensor_module.decode_camera_status(status)
                    print(f"Camera {cam_id} Status: 0x{status:02X} -> {readable}")

        except Exception as e:
            print(f"Error reading camera status: {e}")
            return False
        return True
    except Exception as e:
        print(f"Exception caught: {e}")
        return False

def main():
    args = parse_args()

    print(f"Iterations: {args.iter}")

    left_failed = 0
    right_failed = 0

    # Create an instance of the Sensor interface
    interface = MOTIONInterface()
    
    # Check if console and sensor are connected
    console_connected, left_sensor, right_sensor = interface.is_device_connected()

    if console_connected and left_sensor and right_sensor:
        print("MOTION System fully connected.")
    else:
        print(f'MOTION System NOT Fully Connected. CONSOLE: {console_connected}, SENSOR (LEFT,RIGHT): {left_sensor}, {right_sensor}')
        
    if not left_sensor and not right_sensor:
        print("Sensor Modules not connected.")
        exit(1)

    start_time = time.time()  # Start timer

    for iteration in range(args.iter):
        print(f"\nIteration {iteration + 1}/{args.iter}")
                
        if left_sensor:
            print("Running tests on LEFT sensor")
            if not run(interface.sensors["left"]):
                left_failed = left_failed + 1
        if right_sensor:
            print("Running tests on RIGHT sensor")
            if not run(interface.sensors["right"]):
                right_failed = right_failed + 1

    end_time = time.time()  # End timer
        
    elapsed_time = end_time - start_time
    print(f"\nCompleted {args.iter} iterations in {elapsed_time:.2f} seconds.")
    print(f"Failed {left_failed}, {right_failed} times out of {args.iter} iterations.")
        
   # time.sleep(.5)
    
   # metrics = interface.command_status(reset_status=True)
   # if metrics:
   #     MOTIONSensorModule.print_command_status_report(metrics)
   # else:
   #     print("Failed to retrieve camera status.")
    
   # interface.disconnect()
    
if __name__ == "__main__":
    main()