#!/usr/bin/env python3
"""
read_thorlabs_powermeter.py

Read live power measurements from a Thorlabs USB power meter (PM160 series,
PM100D/PM100USB/PM100A, etc.) via PyVISA + pyvisa-py, over raw USBTMC/libusb.

This talks straight to the device's USBTMC interface -- it does NOT need
NI-VISA or any Thorlabs software installed. It does need a WinUSB driver
bound to the meter (see Zadig, https://zadig.akeo.ie/): bind WinUSB to the
Thorlabs meter (VID 0x1313), not to any other device on the bus.

Requires:
    pip install pyvisa pyvisa-py

Usage
-----
    python bench/thorlabs-pm100/read_thorlabs_powermeter.py
        List VISA resources and exit (use this first to find your resource
        string / confirm the meter is visible at all).

    python bench/thorlabs-pm100/read_thorlabs_powermeter.py --resource USB0::0x1313::0x807B::250820517::INSTR
        Read 10 samples at 0.5 s intervals (defaults).

    python bench/thorlabs-pm100/read_thorlabs_powermeter.py --wavelength 785 --count 20 --interval 0.2
        Set the wavelength correction to 785 nm first (matters for absolute
        power accuracy -- the sensor's responsivity is wavelength-dependent),
        then take 20 samples.

    Note: SCPI command availability was verified against a real PM160
    (firmware 1.6.2), not just the printed PM100USB manual -- the two
    disagree on the wavelength-correction node. This device accepts
    SENS:CORR:WAV, not the plain SENS:WAV the manual's table implies.

    python bench/thorlabs-pm100/read_thorlabs_powermeter.py --count 0 --csv out.csv
        Free-run until Ctrl+C, appending every reading to out.csv.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import pyvisa

THORLABS_VID = 0x1313


def _prime_vendored_libusb() -> None:
    """Best-effort: point pyusb (used internally by pyvisa-py's USB backend)
    at this SDK's vendored libusb-1.0.dll, so a system-wide libusb install
    isn't required. Safe to skip if unavailable (e.g. run outside the repo).
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
        from omotion.usb_backend import get_libusb1_backend

        get_libusb1_backend()
    except Exception as exc:
        print(f"[*] Could not prime vendored libusb ({exc}); "
              f"falling back to system libusb-1.0.dll if any is on PATH.")


def parse_cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read power from a Thorlabs USB power meter via SCPI/USBTMC."
    )
    parser.add_argument(
        "--resource",
        help="VISA resource string, e.g. USB0::0x1313::0x807B::250820517::INSTR. "
             "Auto-selected if omitted and exactly one Thorlabs meter is found.",
    )
    parser.add_argument(
        "--wavelength", type=float, default=None,
        help="Set SENS:CORR:WAV (wavelength correction, nm) before reading. "
             "Leave unset to use whatever the meter is currently configured to.",
    )
    parser.add_argument(
        "--unit", choices=["W", "DBM"], default=None,
        help="Set SENS:POW:DC:UNIT before reading. Leave unset to use the meter's current unit.",
    )
    parser.add_argument(
        "--count", type=int, default=10,
        help="Number of samples to take. 0 = run until Ctrl+C. Default: 10.",
    )
    parser.add_argument(
        "--interval", type=float, default=0.5,
        help="Seconds between samples. Default: 0.5.",
    )
    parser.add_argument(
        "--csv", type=Path, default=None,
        help="Optional path to append timestamped readings as CSV.",
    )
    return parser.parse_args()


def _resource_vid(resource: str) -> int | None:
    # USB resource strings look like USB0::<vid>::<pid>::<serial>[::<iface>]::INSTR,
    # with <vid>/<pid> as either "0x1313" or plain decimal "4883" depending on backend.
    parts = resource.split("::")
    if len(parts) < 3:
        return None
    try:
        return int(parts[1], 0)
    except ValueError:
        return None


def find_thorlabs_resource(resources: list[str]) -> str | None:
    matches = [r for r in resources if _resource_vid(r) == THORLABS_VID]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print("[!] Multiple Thorlabs resources found -- pass --resource to disambiguate:")
        for m in matches:
            print(f"      {m}")
    return None


def main() -> int:
    args = parse_cli()
    _prime_vendored_libusb()

    rm = pyvisa.ResourceManager("@py")  # force pyvisa-py: no NI-VISA runtime on this machine

    resources = list(rm.list_resources())
    if not resources:
        print("[!] No VISA resources visible at all.")
        print("    - Check Device Manager: the meter should show under 'Universal Serial")
        print("      Bus devices' with no yellow warning icon (i.e. WinUSB is bound).")
        print("    - Unplug/replug the meter after (re)binding the driver in Zadig.")
        return 1

    resource_str = args.resource or find_thorlabs_resource(resources)
    if not resource_str:
        print("[*] Available VISA resources:")
        for r in resources:
            print(f"      {r}")
        print("[!] No single Thorlabs (VID 0x1313) resource auto-detected -- pass --resource.")
        return 1

    print(f"[*] Opening {resource_str} ...")
    inst = rm.open_resource(resource_str)
    inst.timeout = 3000
    inst.read_termination = "\n"
    inst.write_termination = "\n"

    idn = inst.query("*IDN?").strip()
    print(f"[+] IDN: {idn}")

    if args.wavelength is not None:
        inst.write(f"SENS:CORR:WAV {args.wavelength}")
        print(f"[*] Wavelength set to {args.wavelength} nm")
    current_wav = inst.query("SENS:CORR:WAV?").strip()

    if args.unit is not None:
        inst.write(f"SENS:POW:DC:UNIT {args.unit}")
    current_unit = inst.query("SENS:POW:DC:UNIT?").strip()

    print(f"[*] Wavelength: {current_wav} nm | Unit: {current_unit}")
    print("[*] Reading power (Ctrl+C to stop) ...")

    csv_writer = None
    csv_file = None
    if args.csv:
        is_new = not args.csv.exists()
        csv_file = args.csv.open("a", newline="")
        csv_writer = csv.writer(csv_file)
        if is_new:
            csv_writer.writerow(["elapsed_s", "power", "unit"])

    t0 = time.monotonic()
    n = 0
    try:
        while args.count == 0 or n < args.count:
            power = float(inst.query("MEAS:POW?"))
            elapsed = time.monotonic() - t0
            print(f"  t={elapsed:7.2f}s  {power:.6e} {current_unit}")
            if csv_writer:
                csv_writer.writerow([f"{elapsed:.3f}", power, current_unit])
                csv_file.flush()
            n += 1
            if args.count == 0 or n < args.count:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[*] Stopped by user.")
    finally:
        if csv_file:
            csv_file.close()
        inst.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
