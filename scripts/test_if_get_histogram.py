import asyncio
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_if_get_histogram.py

print("Starting get histogram using Interface...")

# Create an instance of the Sensor interface
interface = MOTIONInterface()

# Check if console and sensor are connected
console_connected, sensor_connected = interface.is_device_connected()

if console_connected and sensor_connected:
    print("MOTION System fully connected.")
else:
    print(f'MOTION System NOT Fully Connected. CONSOLE: {console_connected}, SENSOR: {sensor_connected}')
    
if not sensor_connected:
    print("Sensor Module not connected.")
    exit(1)

histogram = interface.get_camera_histogram(
    camera_id=1,
    test_pattern_id=0x00,
    auto_upload=True
)

if histogram:
    histogram = histogram[0:4096]
    bins, hidden = interface.bytes_to_integers(histogram)
    print(f"{len(bins)} bins received.")
    print("Sum of bins: " + str(sum(bins)))
    print("Bins: " + str(bins))
    print("Frame ID: " + str(hidden[1023]))