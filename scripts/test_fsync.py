import asyncio, time
import argparse
import sys
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_fsync.py

def parse_args():
    parser = argparse.ArgumentParser(description="Starting MOTION Sensor Module Test Script")
    parser.add_argument('--dur', type=int, default=1, help='Duration for framsinc to be active (default: 1)')

    args = parser.parse_args()

    return args

def run(dur=1) -> bool:
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


        fsin_result = interface.sensor_module.enable_aggregator_fsin()
        print("FSIN activated." if fsin_result else "FSIN activation failed.")
        if not fsin_result:
            return False
        
        # Wait for a moment to ensure FSIN is activated
        time.sleep(dur)

        fsin_result = interface.sensor_module.disable_aggregator_fsin()
        print("FSIN deactivated." if fsin_result else "FSIN deactivation failed.")
        if not fsin_result:
            return False
        
    except Exception as e:
        print(f"Exception caught: {e}")
        return False

def main():
    args = parse_args()

    print(f"Duration: {args.dur} seconds")

    run(args.dur)
    
if __name__ == "__main__":
    main()