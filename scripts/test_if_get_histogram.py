import asyncio
import logging
import time
import argparse
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_if_get_histogram.py

# Set global logging level
logging.basicConfig(level=logging.INFO)

def parse_args():
    parser = argparse.ArgumentParser(description="MOTION Sensor Get Histogram Test")
    parser.add_argument(
        "--camera-id",
        type=int,
        choices=range(0, 8),  # only allow 0–7
        default=0,
        help="Camera ID (0–7)"
    )
    parser.add_argument(
        "--test-pattern",
        type=int,
        default=0,
        help="Test Pattern ID (0=bars, 1=solid, 2=squares, 3=grad, 4=disabled)"
    )
    parser.add_argument(
        "--side",
        type=str,
        choices=["left", "right"],
        required=True,
        help="Select which sensor side to run histogram on"
    )
    return parser.parse_args()

def run_get_histogram_test(interface, camera_id, test_pattern, side):
    print(f"Running get histogram test on {side} sensor for camera={camera_id}, test_pattern={test_pattern}...")

    # Run histogram test for specific side
    hist_result = interface.get_camera_histogram(
        sensor_side=side,          # <-- pass side to method
        camera_id=camera_id,
        test_pattern_id=test_pattern,
        auto_upload=True
    )

    if hist_result and isinstance(hist_result, tuple) and len(hist_result) == 2:
        bins, hidden = hist_result
        print(f"[{side.capitalize()}] {len(bins)} bins received.")
        print(f"[{side.capitalize()}] Sum of bins: {sum(bins)}")
        print(f"[{side.capitalize()}] Bins: {bins}")
        print(f"[{side.capitalize()}] Frame ID: {hidden[1023]}")
    else:
        print(f"[{side.capitalize()}] No bins received.")

def main():
    args = parse_args()

    # Acquire interface + connection state
    interface, console_connected, left_sensor, right_sensor = MOTIONInterface.acquire_motion_interface()

    if console_connected and left_sensor and right_sensor:
        print("MOTION System fully connected.")
    else:
        print(f'MOTION System NOT Fully Connected. CONSOLE: {console_connected}, SENSOR (LEFT,RIGHT): {left_sensor}, {right_sensor}')

    if not left_sensor and not right_sensor:
        print("Sensor Module not connected.")
        exit(1)

    run_get_histogram_test(interface, args.camera_id, args.test_pattern, args.side)

    print("\nSensor Module Test Completed.")
    
if __name__ == "__main__":
    main()
