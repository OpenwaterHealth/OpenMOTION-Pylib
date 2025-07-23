import asyncio, time
import argparse
import sys
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_sensor_module_basics.py

def parse_args():
    parser = argparse.ArgumentParser(description="Starting MOTION Sensor Module Test Script")
    parser.add_argument('--iter', type=int, default=1, help='Iterations to run (default: 1)')

    args = parser.parse_args()

    return args

def run() -> bool:
    try:
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
            interface.sensor_module.disconnect()
            return False

        # Ping Test
        response = interface.sensor_module.ping()
        print("Ping successful." if response else "Ping failed.")
        if not response:
            return False

        # Get Firmware Version
        version = interface.sensor_module.get_version()                
        # Perform the version check
        if version.startswith("v"):
            print(f"Firmware Version: {version}")
        else:
            print(f"Warning: Expected firmware version vX.X.X found {version}")
            return False                    

        # Echo Test
        echo_data = b"Hello LIFU!"
        echoed, echoed_len = interface.sensor_module.echo(echo_data)
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
        led_result = interface.sensor_module.toggle_led()
        print("LED toggled." if led_result else "LED toggle failed.")
        if not led_result:  
            return False
        
        time.sleep(.5)

        # Toggle LED
        led_result = interface.sensor_module.toggle_led()
        print("LED toggled." if led_result else "LED toggle failed.")
        if not led_result:  
            return False
        
        # Get HWID
        try:
            hwid = interface.sensor_module.get_hardware_id()
            if hwid:
                print(f"Hardware ID: {hwid}")
            else:
                print("Failed to read HWID.")
                return False
        except Exception as e:
            print(f"HWID read error: {e}")

        # Query status of camera 0, 3, and 7 (bitmask 0b10001001 = 0x89)
        mask = 0xFF

        try:
            status_map = interface.sensor_module.get_camera_status(mask)

            if status_map is None:
                print("Failed to get camera status.")
            else:
                for cam_id, status in status_map.items():
                    readable = interface.sensor_module.decode_camera_status(status)
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

    failed = 0

    for iteration in range(args.iter):
        visualize = (iteration == args.iter - 1)
        print(f"\nIteration {iteration + 1}/{args.iter}")
        if not run():
            failed = failed + 1
        
    print(f"failed {failed} times out of {args.iter} iterations.")
        
if __name__ == "__main__":
    main()