import argparse
import shutil
import subprocess
import sys
import os
import tempfile
import time
from pathlib import Path

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_firmware_update.py

DFU_VIDPID = "0483:df11"
FLASH_ADDR = "0x08000000"


# Relative path to exe
os.environ["PATH"] = os.pathsep.join([
    os.environ["PATH"],
    r".\dfu-util\win64"  # relative path
])

def run(cmd, check=True, capture=False):
    return subprocess.run(
        cmd,
        check=check,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        text=True,
    )


def run_with_status(cmd, *, check=True, status_label=None, show_spinner=True):
    """Run a subprocess inheriting the terminal, while showing a spinner/timer.

    This provides continuous feedback even when the child process only prints
    output at the end (e.g., buffered stdout).
    """
    start = time.monotonic()
    proc = subprocess.Popen(cmd)

    spinner = "|/-\\"
    spinner_index = 0
    last_render = 0.0
    try:
        while True:
            rc = proc.poll()
            if rc is not None:
                break

            if show_spinner:
                now = time.monotonic()
                if now - last_render >= 0.1:
                    elapsed = now - start
                    prefix = status_label or "Running"
                    ch = spinner[spinner_index % len(spinner)]
                    spinner_index += 1
                    # Write to stderr to reduce interference with dfu-util stdout.
                    sys.stderr.write(f"\r{prefix} {ch}  ({elapsed:0.1f}s)")
                    sys.stderr.flush()
                    last_render = now

            time.sleep(0.05)
    finally:
        if show_spinner:
            # Clear the status line.
            sys.stderr.write("\r" + (" " * 80) + "\r")
            sys.stderr.flush()

    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd)

    return proc.returncode

def ensure_dfuutil():
    exe = shutil.which("dfu-util")
    if not exe:
        sys.exit("ERROR: dfu-util not found in PATH.")
    return exe

def list_devices(dfuutil):
    r = run([dfuutil, "-l"], capture=True)
    print(r.stdout)
    if DFU_VIDPID.lower() not in r.stdout.lower():
        sys.exit("ERROR: STM32 DFU device (0483:df11) not found. Is the board in DFU mode?")

def flash_bin(dfuutil, bin_path, address=FLASH_ADDR, verify=False, *, dfu_verbose=0, show_spinner=True):
    # Program and reset/leave DFU
    cmd = [dfuutil]
    if dfu_verbose:
        cmd += ["-v"] * dfu_verbose
    cmd += ["-a", "0", "-s", f"{address}:leave", "-D", str(bin_path)]
    print("Programming:", " ".join(cmd), flush=True)
    run_with_status(cmd, status_label="Flashing", show_spinner=show_spinner)

    if verify:
        # Some STM32 ROM DFU implementations support upload; many do.
        # Read back same length and compare.
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "readback.bin"
            size = Path(bin_path).stat().st_size
            # Note: dfu-util can't limit size directly here; it uploads full memory region the alt setting exposes.
            # Many STM32s expose all flash; we can still compare the prefix of uploaded data.
            print("Reading back flash (this may take a bit)...")
            cmd = [dfuutil]
            if dfu_verbose:
                cmd += ["-v"] * dfu_verbose
            cmd += ["-a", "0", "-s", address, "-U", str(out)]
            run_with_status(cmd, status_label="Readback", show_spinner=show_spinner)
            rb = out.read_bytes()[:size]
            src = Path(bin_path).read_bytes()
            if rb != src:
                sys.exit("ERROR: Verify failed (readback does not match).")
            print("Verify OK.")

def main():
    p = argparse.ArgumentParser(description="Flash STM32 via DFU using dfu-util")
    p.add_argument("firmware", help="Path to firmware .bin")
    p.add_argument("--addr", default=FLASH_ADDR, help="Flash start address (default 0x08000000)")
    p.add_argument("--verify", action="store_true", help="Read back and compare (if supported)")
    p.add_argument(
        "--dfu-verbose",
        action="count",
        default=0,
        help="Pass -v to dfu-util (repeat for more verbosity)",
    )
    p.add_argument(
        "--no-spinner",
        action="store_true",
        help="Disable the live spinner/timer status line",
    )
    args = p.parse_args()

    bin_path = Path(args.firmware)
    if not bin_path.exists():
        sys.exit(f"ERROR: File not found: {bin_path}")

    dfuutil = ensure_dfuutil()
    list_devices(dfuutil)
    flash_bin(
        dfuutil,
        bin_path,
        address=args.addr,
        verify=args.verify,
        dfu_verbose=args.dfu_verbose,
        show_spinner=not args.no_spinner,
    )
    print("Done.")

if __name__ == "__main__":
    main()
