#!/usr/bin/env python3
"""Put a MOTION sensor STM32 into DFU mode and flash a .bin file.

Uses the vendored dfu-util via :class:`omotion.DFUProgrammer.DFUProgrammer`.

Usage
-----
    python scripts/test_sensor_program.py <path_to_bin_file> [--sensor left|right]
"""

import argparse
import sys
import time
from pathlib import Path

from omotion import MotionInterface
from omotion.DFUProgrammer import DFUProgrammer, DFUProgress

# Sensors enumerate asynchronously after MotionInterface.start(); how long to
# wait before declaring them absent.
STARTUP_TIMEOUT_S = 20.0
# How long the flashed sensor gets to re-enumerate after the DFU bootloader
# leaves. It occasionally never comes back without a DUT mains power-cycle.
REENUM_TIMEOUT_S = 30.0


class _LiveStatus:
    def __init__(self, *, enabled: bool = True):
        self.enabled = enabled
        self._spinner = "|/-\\"
        self._spinner_index = 0
        self._last_render = 0.0
        self._phase = "working"
        self._percent: int | None = None

    def clear(self) -> None:
        if not self.enabled:
            return
        sys.stderr.write("\r" + (" " * 80) + "\r")
        sys.stderr.flush()

    def update(self, p: DFUProgress) -> None:
        if not self.enabled:
            return

        if p.phase == "erase":
            self._phase = "erasing"
        elif p.phase == "download":
            self._phase = "downloading"

        if p.percent is not None:
            self._percent = p.percent

        now = time.monotonic()
        if now - self._last_render < 0.1:
            return

        ch = self._spinner[self._spinner_index % len(self._spinner)]
        self._spinner_index += 1
        pct = f" {self._percent:3d}%" if self._percent is not None else ""
        sys.stderr.write(f"\r   ... {self._phase} {ch}{pct}  ({p.elapsed_s:0.1f}s)")
        sys.stderr.flush()
        self._last_render = now


def parse_cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Put the MOTION STM32 into DFU mode and flash a .bin file."
    )
    parser.add_argument(
        "bin_file",
        type=Path,
        help="Path to bin file that shall be programmed onto the STM32.",
    )
    parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="Skip the interactive confirmation before entering DFU mode.",
    )
    parser.add_argument(
        "--wait",
        type=float,
        default=5.0,
        help="Seconds to wait after issuing the DFU command before polling for the USB device.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Seconds to wait for DFU device to appear.",
    )
    parser.add_argument(
        "--vidpid",
        default="0483:df11",
        help="VID:PID for the DFU device (default 0483:df11).",
    )
    parser.add_argument(
        "--addr",
        default=DFUProgrammer.DEFAULT_ADDRESS,
        help="Flash start address (default 0x08000000).",
    )
    parser.add_argument(
        "--alt",
        type=int,
        default=0,
        help="DFU alt setting (default 0).",
    )
    parser.add_argument(
        "--dfu-verbose",
        action="count",
        default=0,
        help="Pass -v to dfu-util (repeat for more verbosity).",
    )
    parser.add_argument(
        "--no-spinner",
        action="store_true",
        help="Disable live status line.",
    )
    parser.add_argument(
        "--sensor",
        choices=("left", "right"),
        default=None,
        help="Select which sensor to target (left or right). If omitted, the first present sensor is used.",
    )
    return parser.parse_args()


def _wait_for_sensor(interface, side: str | None, timeout_s: float) -> bool:
    """Block until the requested sensor side is connected, or ``timeout_s``.

    ``side`` is "left", "right", or None for either side. Unlike
    ``MotionInterface.wait_for_ready(sensors=1)``, a specific side is not
    satisfied by the other module being present.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        _console, left, right = interface.is_device_connected()
        if side == "left":
            if left:
                return True
        elif side == "right":
            if right:
                return True
        elif left or right:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)


def main() -> int:
    args = parse_cli()

    print("[*] Starting MOTION interface ...")
    interface = MotionInterface()
    interface.start()

    try:
        # Enumeration is asynchronous - checking straight after start() races
        # the connection monitor and reports healthy sensors as absent.
        want = f"the {args.sensor.upper()} sensor" if args.sensor else "a sensor module"
        print(f"[*] Waiting up to {STARTUP_TIMEOUT_S:.0f}s for {want} to connect ...")
        _wait_for_sensor(interface, args.sensor, STARTUP_TIMEOUT_S)

        _console_connected, left_connected, right_connected = interface.is_device_connected()

        # Ensure at least one sensor module is present.
        if not (left_connected or right_connected):
            print("[FAIL]  No sensor modules connected - cannot continue.")
            return 1

        selected_sensor = None
        selected_side = None
        # If the user requested a specific side, honor it (fail if not present).
        if args.sensor == "left":
            if not left_connected:
                print("[FAIL]  LEFT sensor not connected - cannot continue.")
                return 1
            print("Running firmware update on LEFT sensor")
            selected_sensor, selected_side = interface.left, "left"
        elif args.sensor == "right":
            if not right_connected:
                print("[FAIL]  RIGHT sensor not connected - cannot continue.")
                return 1
            print("Running firmware update on RIGHT sensor")
            selected_sensor, selected_side = interface.right, "right"
        else:
            # Auto-select: prefer left if present, otherwise right.
            if left_connected:
                print("Running firmware update on LEFT sensor (auto-selected)")
                selected_sensor, selected_side = interface.left, "left"
            elif right_connected:
                print("Running firmware update on RIGHT sensor (auto-selected)")
                selected_sensor, selected_side = interface.right, "right"

        if selected_sensor is None:
            print("[FAIL]  Sensor module not connected - cannot continue.")
            return 1

        dfu = DFUProgrammer(vidpid=args.vidpid)
        status = _LiveStatus(enabled=not args.no_spinner)

        if not args.no_confirm:
            answer = input("Do you really want to put the board into DFU mode? (y/N): ").strip().lower()
            if answer != "y":
                print("Aborted by user.")
                return 0

        print("\n[+] Requesting DFU mode from the Sensor module ...")
        try:
            ok = selected_sensor.enter_dfu()
        except Exception as exc:  # pragma: no cover
            print(f"   [FAIL]  Exception while calling enter_dfu(): {exc}")
            ok = False

        if ok:
            print("   [OK]  Sensor module reported success.")
        else:
            print("   [FAIL]  Sensor module reported failure.")
            print("[FAIL]  Failed to request DFU mode - aborting.")
            return 1

        print(f"\n[*] Sleeping {args.wait:.1f}s to give the bootloader time to re-enumerate ...")
        time.sleep(args.wait)

        print(f"[+] Waiting up to {args.timeout:.0f}s for DFU device ...")
        if not dfu.wait_for_dfu_device(timeout_s=args.timeout):
            print("[FAIL]  DFU device never appeared - aborting.")
            return 1
        print("   [OK]  DFU device detected.")

        def on_line(line: str) -> None:
            # Ensure the status line doesn't collide with printed output.
            status.clear()
            print("   |", line)
            status._last_render = 0.0

        print("\n[+] Flashing with dfu-util ...")
        result = dfu.flash_bin(
            args.bin_file,
            address=args.addr,
            alt=args.alt,
            verbose=args.dfu_verbose,
            normalize_dfu_suffix=True,
            progress=status.update,
            line_callback=on_line,
            echo_output=False,
            echo_progress_lines=False,
        )

        status.clear()
        if not result.success:
            print(f"[FAIL]  Flash failed (exit code {result.returncode}).")
            # Print any non-progress lines from captured stdout for debugging.
            for ln in (result.stdout or "").splitlines():
                t = ln.strip()
                if (t.startswith("Erase") or t.startswith("Download")) and "%" in t:
                    continue
                print("   |", ln)
            return 1

        print("   [OK]  Flash successful.")
        print("   [i]  DFU bootloader already left - device should be running now.")

        side_label = selected_side.upper()
        print(f"\n[+] Waiting up to {REENUM_TIMEOUT_S:.0f}s for the {side_label} sensor to re-enumerate ...")
        if _wait_for_sensor(interface, selected_side, REENUM_TIMEOUT_S):
            try:
                version = selected_sensor.get_version()
                print(f"   [OK]  {side_label} sensor is back on USB, firmware version {version}.")
            except Exception as exc:
                print(f"   [WARN]  {side_label} sensor re-enumerated but get_version() failed: {exc}")
        else:
            print(f"   [WARN]  {side_label} sensor did not re-enumerate within {REENUM_TIMEOUT_S:.0f}s.")
            print("           If it stays missing from USB, power-cycle the DUT mains supply")
            print("           (known STM32 DFU-leave quirk), then check the device re-appears.")

        print("\n[OK]  All done! The STM32 should now be running the newly-flashed firmware.\n")
        return 0
    finally:
        interface.stop()


if __name__ == "__main__":
    sys.exit(main())
