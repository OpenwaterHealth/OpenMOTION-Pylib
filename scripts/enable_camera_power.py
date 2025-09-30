import argparse
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\enable_camera_power.py --mask 0xFF


def parse_args():
    parser = argparse.ArgumentParser(description="Enable power to selected cameras on all connected sensors")
    parser.add_argument(
        "--mask",
        type=lambda x: int(x, 0),  # supports hex (e.g., 0xFF) or decimal
        default=0xFF,
        help="Camera bitmask to power on (default 0xFF for all cameras)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Acquire interface + connection state
    interface, console_connected, left_sensor, right_sensor = MOTIONInterface.acquire_motion_interface()

    if console_connected and left_sensor and right_sensor:
        print("MOTION System fully connected.")
    else:
        print(
            f"MOTION System NOT Fully Connected. CONSOLE: {console_connected}, SENSOR (LEFT,RIGHT): {left_sensor}, {right_sensor}"
        )

    if not left_sensor and not right_sensor:
        print("Sensor Module not connected.")
        exit(1)

    print(f"Enabling camera power with mask {args.mask:#04x} on all connected sensors...")
    results = interface.run_on_sensors("enable_camera_power", args.mask)

    any_success = False
    for side, success in results.items():
        if success is True:
            any_success = True
            print(f"{side.capitalize()}: ✅ Power enabled")
        elif success is False:
            print(f"{side.capitalize()}: ❌ Failed to enable power")
        else:
            print(f"{side.capitalize()}: ⚠️ No result (possibly disconnected)")

    if not any_success:
        exit(1)


if __name__ == "__main__":
    main()


