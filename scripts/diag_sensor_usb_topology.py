"""Read-only USB topology dump for the left/right sensor mislabel bug
(bloodflow-app: 'sensor plugged into Left port shows as Right').

MotionSensor._find_dev() identifies a sensor's side purely from
``port_numbers[-1]`` (2 => left, 3 => right, see omotion/MotionSensor.py).
This script performs the identical VID/PID enumeration via the SDK's own
libusb backend, but prints the FULL port_numbers path (plus bus number)
for every matching device instead of collapsing it to that one bit — so we
can see exactly what the OS reports for a sensor in each physical jack,
rather than guessing from the SDK's derived left/right label.

Usage: run it TWICE, replugging between runs:
  1. Only one sensor connected, in the jack you believe is "Left".
  2. Only one sensor connected, in the jack you believe is "Right".
Compare the two port_numbers outputs (not just [-1] — the whole tuple).

No writes, no claims of any interface, no PING sent to the device.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import usb.core

from omotion.usb_backend import get_libusb1_backend
from omotion.config import SENSOR_MODULE_PID

VID = 0x0483
PID = SENSOR_MODULE_PID


def main():
    backend = get_libusb1_backend()
    devices = list(
        usb.core.find(find_all=True, idVendor=VID, idProduct=PID, backend=backend)
    )

    if not devices:
        print(f"No sensor devices found (VID=0x{VID:04X} PID=0x{PID:04X}).")
        print("Is a sensor connected and enumerated?")
        return

    print(f"Found {len(devices)} sensor device(s):\n")
    for i, dev in enumerate(devices):
        ports = tuple(getattr(dev, "port_numbers", []) or [])
        bus = getattr(dev, "bus", None)
        addr = getattr(dev, "address", None)
        suffix = ports[-1] if ports else None
        sdk_label = (
            "left" if suffix == 2 else "right" if suffix == 3 else "NEITHER (unmatched by SDK)"
        )
        print(f"  Device #{i}:")
        print(f"    bus={bus} address={addr}")
        print(f"    port_numbers={ports}  (full path from root hub)")
        print(f"    port_numbers[-1]={suffix}  -> SDK would call this: {sdk_label}")
        print()


if __name__ == "__main__":
    main()
