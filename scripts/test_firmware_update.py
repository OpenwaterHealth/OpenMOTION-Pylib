import argparse
import shutil
import subprocess
import sys
import os
import tempfile
from pathlib import Path

DFU_VIDPID = "0483:df11"
FLASH_ADDR = "0x08000000"


# Relative path to exe
os.environ["PATH"] = os.pathsep.join([
    os.environ["PATH"],
    r".\dfu-util\win64"  # relative path
])

def run(cmd, check=True, capture=False):
    return subprocess.run(
        cmd, check=check,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        text=True
    )

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

def flash_bin(dfuutil, bin_path, address=FLASH_ADDR, verify=False):
    # Program and reset/leave DFU
    cmd = [dfuutil, "-a", "0", "-s", f"{address}:leave", "-D", str(bin_path)]
    print("Programming:", " ".join(cmd))
    run(cmd)

    if verify:
        # Some STM32 ROM DFU implementations support upload; many do.
        # Read back same length and compare.
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "readback.bin"
            size = Path(bin_path).stat().st_size
            # Note: dfu-util can't limit size directly here; it uploads full memory region the alt setting exposes.
            # Many STM32s expose all flash; we can still compare the prefix of uploaded data.
            print("Reading back flash (this may take a bit)...")
            run([dfuutil, "-a", "0", "-s", address, "-U", str(out)])
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
    args = p.parse_args()

    bin_path = Path(args.firmware)
    if not bin_path.exists():
        sys.exit(f"ERROR: File not found: {bin_path}")

    dfuutil = ensure_dfuutil()
    list_devices(dfuutil)
    flash_bin(dfuutil, bin_path, address=args.addr, verify=args.verify)
    print("Done.")

if __name__ == "__main__":
    main()
