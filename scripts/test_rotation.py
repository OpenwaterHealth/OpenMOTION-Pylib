from vpython import box, vector, rate, scene
import usb.core
import usb.util
import json
import numpy as np
import time
from omotion.usb_backend import get_libusb1_backend

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_rotation.py

backend = get_libusb1_backend()

VID = 0x0483
PID = 0x5750
EP_IN = 0x83
EP_SIZE = 64
TIMEOUT = 50

def read_usb_stream(dev, endpoint=EP_IN, timeout=TIMEOUT):
    data = bytearray()
    while True:
        try:
            chunk = dev.read(endpoint, EP_SIZE, timeout=timeout)
            data.extend(chunk)
            # If packet is shorter than max size, it's the end
            if len(chunk) < EP_SIZE:
                break
        except usb.core.USBError as e:
            print(f"USB read error: {e}")
            break
    return data.decode(errors='ignore')  # or return raw if needed


# ---- Initialize 3D Cube ----
scene.title = "IMU Orientation Demo"
scene.background = vector(0.2, 0.2, 0.2)
scene.range = 1.5
imu_cube = box(length=1, height=0.2, width=0.5, color=vector(0,1,0))

# ---- USB IMU ----
dev = usb.core.find(idVendor=VID, idProduct=PID, backend=backend)
dev.set_configuration()
usb.util.claim_interface(dev, 2)

print("Starting IMU visualization. Ctrl+C to stop.\n")

# Initialize orientation
orientation = np.identity(3)
prev_time = time.time()

try:
    while True:
        json_str = read_usb_stream(dev)
        if json_str:
            data = json.loads(json_str)
            gx, gy, gz = data["G"]

            # time delta
            now = time.time()
            dt = now - prev_time
            prev_time = now

            # convert to radians/sec if needed (assuming raw deg/sec)
            gx_rad = gx * np.pi / 180
            gy_rad = gy * np.pi / 180
            gz_rad = gz * np.pi / 180

            # rotation matrix (small angle approx)
            omega = np.array([
                [0, -gz_rad*dt, gy_rad*dt],
                [gz_rad*dt, 0, -gx_rad*dt],
                [-gy_rad*dt, gx_rad*dt, 0]
            ])
            orientation += orientation @ omega  # integrate rotation
            imu_cube.axis = vector(*orientation[:, 0])
            imu_cube.up = vector(*orientation[:, 2])
        rate(40)
except KeyboardInterrupt:
    print("Stopped.")
    usb.util.release_interface(dev, 2)
    usb.util.dispose_resources(dev)
