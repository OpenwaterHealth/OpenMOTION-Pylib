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

# --------------------------------------------------------------------------- #
#   Standard library imports
# --------------------------------------------------------------------------- #
import argparse
import asyncio
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# --------------------------------------------------------------------------- #
# Python SDK
# --------------------------------------------------------------------------- #
from omotion.Interface import MOTIONInterface

# --------------------------------------------------------------------------- #
#   Helper: locate the bundled dfu‑util binary
# --------------------------------------------------------------------------- #
def locate_dfu_util() -> Path:
    """
    Returns the absolute Path to the dfu‑util executable that matches the
    current host platform.

    The expected directory layout is:

        DFU-UTIL/
            darwin-x86_64/
            linux-amd64/
            win32/
            win64/
    """
    # 1️⃣  Determine which sub‑folder we need
    system = platform.system().lower()
    machine = platform.machine().lower()

    # Normalise the names used by the repo
    if system.startswith("darwin"):
        subdir = "darwin-x86_64"          # the repo only ships a single macOS binary
    elif system.startswith("linux"):
        # The repo only has one linux binary (amd64).  If you ever ship arm64,
        # add a new folder and extend this mapping.
        subdir = "linux-amd64"
    elif system.startswith("windows"):
        # Distinguish 32‑ vs 64‑bit builds
        if "64" in machine:
            subdir = "win64"
        else:
            subdir = "win32"
    else:
        raise RuntimeError(f"Unsupported OS: {platform.system()}")

    # 2️⃣  Build the absolute path
    script_dir = Path(__file__).resolve().parent.parent               # folder that holds this script
    dfu_dir = script_dir / "dfu-util" / subdir
    exe_name = "dfu-util.exe" if system.startswith("windows") else "dfu-util"
    dfu_path = dfu_dir / exe_name

    if not dfu_path.is_file():
        raise FileNotFoundError(
            f"dfu‑util binary not found at expected location: {dfu_path}"
        )

    # 3️⃣  Make sure the folder is on PATH for the *child* processes we spawn.
    #    We do **not** modify the user's global environment.
    os.environ["PATH"] = str(dfu_dir) + os.pathsep + os.environ.get("PATH", "")

    return dfu_path


# --------------------------------------------------------------------------- #
#   Core class – unchanged except that it now calls the located binary
# --------------------------------------------------------------------------- #
class STM32DFUProgrammer:
    """
    High‑level wrapper that knows how to:
        * request DFU mode from the console module,
        * wait for the USB DFU device to appear,
        * flash a .bin file with dfu‑util,
        * and finally tell the bootloader to leave (reset).
    """

    def __init__(self, interface):
        self.interface = interface
        self.console = interface.console_module
        # Resolve the binary once; will raise early if something is missing.
        self.dfu_path = locate_dfu_util()
        # Store the directory for later PATH‑adjustment (helpful for debugging)
        self.dfu_dir = self.dfu_path.parent

    # --------------------------------------------------------------- #
    # 1️⃣  Ask the console to jump to the built‑in DFU bootloader
    # --------------------------------------------------------------- #
    def enter_dfu_mode(self) -> bool:
        print("\n[+] Requesting DFU mode from the console module …")
        try:
            ok = self.console.enter_dfu()
        except Exception as exc:                 # pragma: no cover – defensive
            print(f"   ❌  Exception while calling enter_dfu(): {exc}")
            return False

        if ok:
            print("   ✅  Console module reported success.")
        else:
            print("   ❌  Console module reported failure.")
        return ok
    
    # -----------------------------------------------------------
    #   Add DFU‑Suffix
    # -----------------------------------------------------------
    def add_dfu_suffix(self, bin_path: Path) -> Path:
        suffixed_path = bin_path.with_name(bin_path.stem + ".dfu.bin")

        dfu_suffix_cmd = [
            "dfu-suffix",
            "-v", "0x0483",
            "-p", "0xdf11",
            "-a",                 # auto-detect DFU version
            str(bin_path),
            str(suffixed_path),
        ]

        subprocess.run(dfu_suffix_cmd, check=True)
        return suffixed_path
    
    # --------------------------------------------------------------- #
    # 2️⃣  Wait for the USB DFU device to appear
    # --------------------------------------------------------------- #
    @staticmethod
    async def _wait_for_dfu_device(timeout: float = 10.0) -> bool:
        print(f"[+] Waiting up to {timeout:.0f}s for DFU device …")
        deadline = time.time() + timeout
        while time.time() < deadline:
            proc = await asyncio.create_subprocess_exec(
                "dfu-util", "-l",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if b"Found DFU" in stdout:
                print("   ✅  DFU device detected.")
                return True
            await asyncio.sleep(0.5)
        print("   ❌  Timeout – DFU device never appeared.")
        return False

    def wait_for_dfu(self, timeout: float = 10.0) -> bool:
        return asyncio.run(self._wait_for_dfu_device(timeout))

    # --------------------------------------------------------------- #
    # 3️⃣  Flash the supplied .bin file
    # --------------------------------------------------------------- #
    def _run_dfu_util(self, binary_path: Path) -> bool:
        """
        Run the *bundled* dfu-util binary and determine success based on
        dfu-util output rather than exit code alone.

        STM32 ROM DFU often resets immediately after :leave, which causes
        dfu-util to exit with a non-zero status even though flashing succeeded.
        """
        cmd = [
            str(self.dfu_path),
            "-a", "0",
            "-s", "0x08000000:leave",
            "-D", str(binary_path),
            "-R",
        ]

        print("\n[+] Flashing with dfu-util …")
        print("   $", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                text=True,
            )
        except Exception as exc:  # pragma: no cover – defensive
            print(f"   ❌  Failed to launch dfu-util: {exc}")
            return False

        # Echo output
        for line in result.stdout.splitlines():
            print("   |", line)

        stdout = result.stdout or ""

        # ✅ Primary success indicator for STM32 ROM DFU
        if "File downloaded successfully" in stdout:
            if result.returncode != 0:
                print(
                    "   ⚠️  dfu-util reported a non-zero exit code, but flashing "
                    "completed successfully (expected STM32 DFU behavior)."
                )
            print("   ✅  Flash successful.")
            return True

        # ✅ Clean exit (no leave/reset race)
        if result.returncode == 0:
            print("   ✅  Flash successful.")
            return True

        # ❌ Genuine failure
        print(f"   ❌  dfu-util failed with exit code {result.returncode}.")
        return False

    def flash_bin(self, bin_file: Path) -> bool:
        if not bin_file.is_file():
            print(f"   ❌  bin file not found: {bin_file}")
            return False
        return self._run_dfu_util(bin_file)

    # --------------------------------------------------------------- #
    # 4️⃣  Reboot – most DFU bootloaders already reset after :leave
    # --------------------------------------------------------------- #
    def reboot_device(self) -> None:
        print("   ℹ️  DFU bootloader already left – device should be running now.")


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

    if not console_connected:
        print("❌  Console module not connected – cannot continue.")
        sys.exit(1)

    print("✅  Console module is connected.\n")

    programmer = STM32DFUProgrammer(interface)

    # --------------------------------------------------------------- #
    # 1️⃣  (Optional) user confirmation
    # --------------------------------------------------------------- #
    if not args.no_confirm:
        answer = input("Do you really want to put the board into DFU mode? (y/N): ").strip().lower()
        if answer != "y":
            print("Aborted by user.")
            sys.exit(0)

    # Bin‑File add DFU‑Suffix 
    try: 
        _ = programmer.add_dfu_suffix(args.bin_file) 
    except Exception as exc: 
        print(f"❌ Failed to create DFU‑suffix: {exc}") 
        return
    
    # --------------------------------------------------------------- #
    # 2️⃣  Enter DFU mode
    # --------------------------------------------------------------- #
    if not programmer.enter_dfu_mode():
        print("❌  Failed to request DFU mode – aborting.")
        sys.exit(1)

    # --------------------------------------------------------------- #
    # 3️⃣  Short deterministic sleep, then poll for the USB device
    # --------------------------------------------------------------- #
    print(f"\n[*] Sleeping {args.wait:.1f}s to give the bootloader time to re‑enumerate …")
    time.sleep(args.wait)

    if not programmer.wait_for_dfu():
        print("❌  DFU device never appeared – aborting.")
        sys.exit(1)

    # --------------------------------------------------------------- #
    # 4️⃣  Flash the .bin file
    # --------------------------------------------------------------- #
    if not programmer.flash_bin(args.bin_file):
        print("❌  Flash failed – aborting.")
        sys.exit(1)

    # --------------------------------------------------------------- #
    # 5️⃣  Reboot / leave DFU (normally already done by :leave)
    # --------------------------------------------------------------- #
    programmer.reboot_device()

    print("\n🎉  All done! The STM32 should now be running the newly‑flashed firmware.\n")


if __name__ == "__main__":
    main()