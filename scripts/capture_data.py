
import argparse
import time
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\capture_frames.py

MAX_DURATION = 120  # seconds

def parse_args():
    parser = argparse.ArgumentParser(description="Capture MOTION camera data")
    parser.add_argument(
        "--camera-mask",
        type=lambda x: int(x, 0),  # allows 0x01 or 1
        required=True,
        help="Camera bitmask (e.g., 0x01 = camera 0, 0xFF = all 8 cameras)"
    )
    parser.add_argument(
        "--duration",
        type=int,
        required=True,
        help=f"Duration in seconds (max {MAX_DURATION})"
    )
    return parser.parse_args()


def main():
    print("Starting MOTION Capture Data Script...")

    args = parse_args()

    if args.duration > MAX_DURATION:
        print(f"Error: Duration cannot exceed {MAX_DURATION} seconds.")
        exit(1)

    print("Starting MOTION Capture Data Script...")
    print(f"Camera Mask: 0x{args.camera_mask:02X}")
    print(f"Duration: {args.duration} seconds")

    # Acquire interface + connection state
    interface, console_connected, left_sensor, right_sensor = MOTIONInterface.acquire_motion_interface()

    if console_connected and left_sensor and right_sensor:
        print("MOTION System fully connected.")
    else:
        print(f'MOTION System NOT Fully Connected. CONSOLE: {console_connected}, SENSOR (LEFT,RIGHT): {left_sensor}, {right_sensor}')
        exit(1)


    results = interface.run_on_sensors("enable_camera", args.camera_mask)
    for side, success in results.items():
        if not success:
            print(f"Failed to enable camera on {side}.")
            exit(1)
            
    # Activate Laser
    print("\nStart trigger...")
    if not interface.console_module.start_trigger():
        print("Failed to start trigger.")

    # Start capture loop
    start_time = time.time()
    elapsed = 0

    try:
        while elapsed < args.duration:
            elapsed = time.time() - start_time
            # in case we wnat to do something here
            time.sleep(1)  # capture every 1 second; adjust as needed

    except KeyboardInterrupt:
        print("\n🛑 Capture interrupted by user.")

    print("\nStop trigger...")
    if not interface.console_module.stop_trigger():
        print("Failed to stop trigger.")

    results = interface.run_on_sensors("disable_camera", args.camera_mask)
    for side, success in results.items():
        if not success:
            print(f"Failed to disable camera on {side}.")
            exit(1)
            
    print("Capture session complete.")

if __name__ == "__main__":
    main()