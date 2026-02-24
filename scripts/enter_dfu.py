#!/usr/bin/env python3
"""
enter_dfu.py

Put a chosen MOTION device (console or left/right sensor) into DFU mode.

Usage
-----
    python enter_dfu.py <device>

    device:  console | left | right
"""

import argparse
import sys

from omotion.Interface import MOTIONInterface


def parse_cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Put a MOTION device (console or sensor) into DFU mode."
    )
    parser.add_argument(
        "device",
        choices=("console", "left", "right"),
        help="Which device to put into DFU mode: console, left sensor, or right sensor.",
    )
    parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="Skip the interactive confirmation before entering DFU mode.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_cli()

    print("[*] Acquiring MOTION interface …")
    interface, console_connected, left_sensor, right_sensor = (
        MOTIONInterface.acquire_motion_interface()
    )

    if args.device == "console":
        if not console_connected:
            print("❌  Console module not connected.")
            return 1
        target = interface.console_module
        label = "console module"
    elif args.device == "left":
        if not left_sensor:
            print("❌  Left sensor not connected.")
            return 1
        target = interface.sensors["left"]
        label = "left sensor"
    else:  # right
        if not right_sensor:
            print("❌  Right sensor not connected.")
            return 1
        target = interface.sensors["right"]
        label = "right sensor"

    if not args.no_confirm:
        answer = input(
            f"Do you want to put the {label} into DFU mode? (y/N): "
        ).strip().lower()
        if answer != "y":
            print("Aborted.")
            return 0

    print(f"[+] Requesting DFU mode from {label} …")
    try:
        ok = target.enter_dfu()
    except Exception as exc:
        print(f"   ❌  Exception: {exc}")
        return 1

    if ok:
        print("   ✅  DFU mode requested successfully. Device should re-enumerate as DFU.")
        return 0
    print("   ❌  Device did not report success.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
