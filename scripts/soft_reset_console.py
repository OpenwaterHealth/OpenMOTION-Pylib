#!/usr/bin/env python3
"""
soft_reset_console.py

Send a soft reset command to the connected MOTION console.

Usage
-----
    python soft_reset_console.py [--no-confirm]
"""

import argparse
import sys

from omotion.Interface import MOTIONInterface


def parse_cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send a soft reset command to the connected MOTION console."
    )
    parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="Skip the interactive confirmation before soft reset.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_cli()

    print("[*] Acquiring MOTION interface …")
    interface, console_connected, _, _ = MOTIONInterface.acquire_motion_interface()

    if not console_connected:
        print("❌  Console module not connected.")
        return 1

    if not args.no_confirm:
        answer = input(
            "Do you want to soft reset the console? (y/N): "
        ).strip().lower()
        if answer != "y":
            print("Aborted.")
            return 0

    print("[+] Sending soft reset to console …")
    try:
        ok = interface.console_module.soft_reset()
    except Exception as exc:
        print(f"   ❌  Exception: {exc}")
        return 1

    if ok:
        print("   ✅  Soft reset sent successfully. Console should reboot.")
        return 0
    print("   ❌  Console did not report success.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
