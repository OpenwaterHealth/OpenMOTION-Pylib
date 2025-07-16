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
        print("\n[1] Ping Sensor Module...")
        response = interface.sensor_module.ping()
        print("Ping successful." if response else "Ping failed.")
        if not response:
            return False

        # Get Firmware Version
        print("\n[2] Reading Firmware Version...")    
        version = interface.sensor_module.get_version()
        print(f"Firmware Version: {version}")
        
        # Perform the version check
        if version == "v1.1.0":
            print(f"Firmware Version: {version}")
        else:
            print(f"Warning: Expected firmware v1.1.0, found {version}")
            return False                    

        # Echo Test
        print("\n[3] Echo Test...")    
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
        print("\n[4] Toggle LED...")
        led_result = interface.sensor_module.toggle_led()
        print("LED toggled." if led_result else "LED toggle failed.")
        if not led_result:  
            return False
        
        # Get HWID
        print("\n[5] Read Hardware ID...")
        try:
            hwid = interface.sensor_module.get_hardware_id()
            if hwid:
                print(f"Hardware ID: {hwid}")
            else:
                print("Failed to read HWID.")
                return False
        except Exception as e:
            print(f"HWID read error: {e}")

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