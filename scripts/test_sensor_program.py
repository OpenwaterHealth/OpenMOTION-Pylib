#!/usr/bin/env python3
"""
stm32_dfu_programmer.py

Put a MOTION STM32 board into DFU mode and flash a .bin file.
The script automatically selects the appropriate dfu‑util binary from the
local DFU-UTIL/ sub‑folders (darwin‑x86_64, linux‑amd64, win32, win64).

Usage
-----
    python stm32_dfu_programmer.py <path_to_bin_file>
"""

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_sensor_program.py

# --------------------------------------------------------------------------- #
#   Standard library imports
# --------------------------------------------------------------------------- #
import argparse
import sys
import time
from pathlib import Path

# --------------------------------------------------------------------------- #
# Python SDK
# --------------------------------------------------------------------------- #
from omotion.Interface import MOTIONInterface
from omotion.DFUProgrammer import DFUProgrammer, DFUProgress

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
        sys.stderr.write(f"\r   … {self._phase} {ch}{pct}  ({p.elapsed_s:0.1f}s)")
        sys.stderr.flush()
        self._last_render = now


# --------------------------------------------------------------------------- #
#   CLI handling
# --------------------------------------------------------------------------- #
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
    return parser.parse_args()


def main() -> None:
    args = parse_cli()

    # --------------------------------------------------------------- #
    # 0️⃣  Acquire the MOTION interface – exactly as you already do
    # --------------------------------------------------------------- #
    print("[*] Acquiring MOTION interface …")
    interface, console_connected, left_sensor, right_sensor = (
        MOTIONInterface.acquire_motion_interface()
    )

    if not left_sensor or not right_sensor:
        print("❌  Sensor module not connected – cannot continue.")
        sys.exit(1)

    print("✅  Senosr module is connected.\n")
    selected_sensor = None
    if left_sensor:
        print("Running Firmware update on LEFT sensor")
        selected_sensor = interface.sensors["left"]
    
    if right_sensor:
        print("Running firmware update on RIGHT sensor")
        selected_sensor = interface.sensors["right"]

    if selected_sensor is None:
        print("❌  Sensor module not connected – cannot continue.")
        sys.exit(1)

    dfu = DFUProgrammer(vidpid=args.vidpid)
    status = _LiveStatus(enabled=not args.no_spinner)

    # --------------------------------------------------------------- #
    # 1️⃣  (Optional) user confirmation
    # --------------------------------------------------------------- #
    if not args.no_confirm:
        answer = input("Do you really want to put the board into DFU mode? (y/N): ").strip().lower()
        if answer != "y":
            print("Aborted by user.")
            sys.exit(0)
    
    # --------------------------------------------------------------- #
    # 2️⃣  Enter DFU mode
    # --------------------------------------------------------------- #
    print("\n[+] Requesting DFU mode from the Sensor module …")
    try:
        ok = selected_sensor.enter_dfu()
    except Exception as exc:  # pragma: no cover
        print(f"   ❌  Exception while calling enter_dfu(): {exc}")
        ok = False

    if ok:
        print("   ✅  Sensor module reported success.")
    else:
        print("   ❌  Sensor module reported failure.")

    if not ok:
        print("❌  Failed to request DFU mode – aborting.")
        sys.exit(1)

    # --------------------------------------------------------------- #
    # 3️⃣  Short deterministic sleep, then poll for the USB device
    # --------------------------------------------------------------- #
    print(f"\n[*] Sleeping {args.wait:.1f}s to give the bootloader time to re‑enumerate …")
    time.sleep(args.wait)

    print(f"[+] Waiting up to {args.timeout:.0f}s for DFU device …")
    if not dfu.wait_for_dfu_device(timeout_s=args.timeout):
        print("❌  DFU device never appeared – aborting.")
        sys.exit(1)
    print("   ✅  DFU device detected.")

    # --------------------------------------------------------------- #
    # 4️⃣  Flash the .bin file
    # --------------------------------------------------------------- #
    def on_line(line: str) -> None:
        # Ensure the status line doesn't collide with printed output.
        status.clear()
        print("   |", line)
        status._last_render = 0.0

    print("\n[+] Flashing with dfu-util …")
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
        print(f"❌  Flash failed (exit code {result.returncode}).")
        # Print any non-progress lines from captured stdout for debugging.
        for ln in (result.stdout or "").splitlines():
            t = ln.strip()
            if (t.startswith("Erase") or t.startswith("Download")) and "%" in t:
                continue
            print("   |", ln)
        sys.exit(1)

    print("   ✅  Flash successful.")

    # --------------------------------------------------------------- #
    # 5️⃣  Reboot / leave DFU (normally already done by :leave)
    # --------------------------------------------------------------- #
    print("   ℹ️  DFU bootloader already left – device should be running now.")

    print("\n🎉  All done! The STM32 should now be running the newly‑flashed firmware.\n")


if __name__ == "__main__":
    main()