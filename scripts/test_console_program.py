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
# python scripts\test_console_program.py

# --------------------------------------------------------------------------- #
#   Standard library imports
# --------------------------------------------------------------------------- #
import argparse
import asyncio
import os
import platform
import queue
import re
import subprocess
import sys
import threading
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
    @staticmethod
    def _has_dfu_suffix(path: Path) -> bool:
        """Best-effort detection of a DFU suffix (16 bytes ending with 'UFD')."""
        try:
            data = path.read_bytes()
        except OSError:
            return False

        suffix_len = 16
        if len(data) < suffix_len:
            return False

        suffix = data[-suffix_len:]
        # DFU suffix layout includes signature 'UFD' and bLength=16.
        return suffix[8:11] == b"UFD" and suffix[11] == suffix_len

    def add_dfu_suffix(self, bin_path: Path) -> Path:
        # If the file already has a DFU suffix, do not attempt to add another.
        if self._has_dfu_suffix(bin_path):
            print("[i] DFU suffix already present; skipping dfu-suffix step.")
            return bin_path

        # Use the bundled dfu-suffix tool if present.
        system = platform.system().lower()
        dfu_suffix_exe = (
            self.dfu_dir / ("dfu-suffix.exe" if system.startswith("windows") else "dfu-suffix")
        )
        if not dfu_suffix_exe.is_file():
            raise FileNotFoundError(
                f"dfu-suffix not found next to dfu-util: {dfu_suffix_exe}"
            )

        dfu_suffix_cmd = [
            str(dfu_suffix_exe),
            "-v", "0x0483",
            "-p", "0xdf11",
            "-a",                 # auto-detect DFU version
            str(bin_path),
        ]

        subprocess.run(dfu_suffix_cmd, check=True)
        # dfu-suffix modifies the file in-place.
        return bin_path
    
    # --------------------------------------------------------------- #
    # 2️⃣  Wait for the USB DFU device to appear
    # --------------------------------------------------------------- #
    @staticmethod
    async def _wait_for_dfu_device(timeout: float = 30.0) -> bool:
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

    def wait_for_dfu(self, timeout: float = 30.0) -> bool:
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

        # Run with a live status line so users see continuous feedback even if
        # dfu-util output is buffered when piped.
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as exc:  # pragma: no cover – defensive
            print(f"   ❌  Failed to launch dfu-util: {exc}")
            return False

        output_lines: list[str] = []
        q: "queue.Queue[str]" = queue.Queue()

        def _reader() -> None:
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    q.put(line)
            finally:
                q.put("")  # sentinel

        t = threading.Thread(target=_reader, daemon=True)
        t.start()

        spinner = "|/-\\"
        spinner_index = 0
        start = time.monotonic()
        last_render = 0.0
        latest_percent: Optional[int] = None
        latest_phase: str = "flashing"

        def _clear_status_line() -> None:
            sys.stderr.write("\r" + (" " * 80) + "\r")
            sys.stderr.flush()

        def _render_status_line(now: float) -> None:
            nonlocal spinner_index, last_render
            elapsed = now - start
            ch = spinner[spinner_index % len(spinner)]
            spinner_index += 1
            pct = f" {latest_percent:3d}%" if latest_percent is not None else ""
            sys.stderr.write(f"\r   … {latest_phase} {ch}{pct}  ({elapsed:0.1f}s)")
            sys.stderr.flush()
            last_render = now

        try:
            while True:
                rc = proc.poll()

                # Drain any available output.
                drained_any = False
                while True:
                    try:
                        line = q.get_nowait()
                    except queue.Empty:
                        break

                    if line == "":
                        # reader finished
                        break

                    drained_any = True
                    output_lines.append(line)

                    trimmed = line.strip()
                    if trimmed.startswith("Erase"):
                        latest_phase = "erasing"
                    elif trimmed.startswith("Download"):
                        latest_phase = "downloading"

                    # Best-effort percent extraction from dfu-util output.
                    m = re.search(r"(\d{1,3})%", line)
                    if m:
                        try:
                            p = int(m.group(1))
                            if 0 <= p <= 100:
                                latest_percent = p
                        except ValueError:
                            pass

                    # dfu-util prints a lot of progress-bar lines. We still parse
                    # percentages from them for the live status line, but we don't
                    # echo them by default to keep output readable.
                    is_download_progress = trimmed.startswith("Download") and "%" in trimmed
                    is_erase_progress = trimmed.startswith("Erase") and "%" in trimmed
                    if not (is_download_progress or is_erase_progress):
                        _clear_status_line()
                        print("   |", line.rstrip("\n"))
                        # Force a status re-render soon after output.
                        last_render = 0.0

                # Render status line periodically.
                now = time.monotonic()
                if now - last_render >= 0.1 and rc is None:
                    _render_status_line(now)

                if rc is not None:
                    break

                if not drained_any:
                    time.sleep(0.05)
        finally:
            # Clear the status line.
            _clear_status_line()

        stdout = "".join(output_lines)
        returncode = proc.returncode if proc.returncode is not None else 1

        # ✅ Primary success indicator for STM32 ROM DFU
        if "File downloaded successfully" in stdout:
            if returncode != 0:
                print(
                    "   ⚠️  dfu-util reported a non-zero exit code, but flashing "
                    "completed successfully (expected STM32 DFU behavior)."
                )
            print("   ✅  Flash successful.")
            return True

        # ✅ Clean exit (no leave/reset race)
        if returncode == 0:
            print("   ✅  Flash successful.")
            return True

        # ❌ Genuine failure
        print(f"   ❌  dfu-util failed with exit code {returncode}.")
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

    # Bin‑File add DFU‑Suffix (optional; proceed even if it fails)
    bin_to_program = args.bin_file
    try:
        bin_to_program = programmer.add_dfu_suffix(args.bin_file)
        time.sleep(2)
    except Exception as exc:
        print(f"⚠️  DFU suffix step failed, continuing with original file: {exc}")
    
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
    if not programmer.flash_bin(bin_to_program):
        print("❌  Flash failed – aborting.")
        sys.exit(1)

    # --------------------------------------------------------------- #
    # 5️⃣  Reboot / leave DFU (normally already done by :leave)
    # --------------------------------------------------------------- #
    programmer.reboot_device()

    print("\n🎉  All done! The STM32 should now be running the newly‑flashed firmware.\n")


if __name__ == "__main__":
    main()