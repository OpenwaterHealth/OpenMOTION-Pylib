import asyncio
import time
import numpy as np
import csv
import matplotlib.pyplot as plt
from omotion.Interface import MOTIONInterface


# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\camera_tester.py

print("Starting MOTION Sensor Module Test Script...")

CAMERA_MASK = 0x0F

## Test Patterns
# 0 Gradient bars
# 1 Solid color
# 2 Squares
# 3 Continuous Gradient
# 4 disabled
ENABLE_TEST_PATTERN = False
TEST_PATTERN_ID = 0x04

save_histo = False
show_histo = False
clean_printouts = False

gain = 16
exposure = 600

def plot_10bit_histogram(histogram_data, title="10-bit Histogram"):
    """
    Plots a 10-bit histogram (0-1023) from raw byte data.
    
    Args:
        histogram_data (bytearray): Raw histogram data from the sensor.
        title (str): Title for the plot (default: "10-bit Histogram").
    """
    try:
        # Convert bytearray to a numpy array of 16-bit integers (assuming little-endian)
        # Each histogram bin is 4 bytes (uint16)
        # hist_values = np.frombuffer(histogram_data, dtype='<u4')  # little-endian uint32
        
        # Verify expected length (1024 bins for 10-bit)
        if len(histogram_data) != 1024:
            print(f"Warning: Expected 1024 bins, got {len(histogram_data)}")
        
        # Plot the histogram
        plt.figure(figsize=(12, 6))
        plt.bar(range(len(histogram_data)), histogram_data, width=1.0)
        plt.title(title)
        plt.xlabel("Pixel Value (0-1023)")
        plt.ylabel("Count")
        plt.grid(True, linestyle='--', alpha=0.6)
        plt.show()
        
    except Exception as e:
        print(f"Error plotting histogram: {e}")

def print_weighted_average(histogram):
    if len(histogram) != 1024:
        raise ValueError("Histogram must have 1024 bins.")
    
    weighted_sum = sum(i * count for i, count in enumerate(histogram))
    total_count = sum(histogram)
    
    if total_count == 0:
        print("Weighted average is undefined (total count is zero).")
    else:
        average = weighted_sum / total_count
        if(not clean_printouts): print(f"Image Mean: {average:.2f}")
        else: print(f"{average:.2f}")

def save_histogram_raw(histogram_data: bytearray, filename: str = "histogram.bin"):
    """Saves raw histogram bytes to a binary file."""
    try:
        with open(filename, "wb") as f:  # 'wb' = write binary
            f.write(histogram_data)
        print(f"Successfully saved raw histogram to {filename}")
    except Exception as e:
        print(f"Error saving histogram: {e}")

def save_histogram_csv(histogram_data, filename: str = "histogram.csv"):
    if len(histogram_data) != 1024:
        raise ValueError("histogram_data must be exactly 1024 elements long.")

    with open(filename, mode='w', newline='') as f:
        writer = csv.writer(f)
        # Write header
        writer.writerow([str(i) for i in range(1024)])
        # Write the data row
        writer.writerow(histogram_data)

def bytes_to_integers(byte_array):
        # Check that the byte array is exactly 4096 bytes
        if len(byte_array) != 4096:
            raise ValueError("Input byte array must be exactly 4096 bytes.")
        # Initialize an empty list to store the converted integers
        integers = []
        hidden_figures = []
        # Iterate over the byte array in chunks of 4 bytes
        for i in range(0, len(byte_array), 4):
            bytes = byte_array[i:i+4]
            # Unpack each 4-byte chunk as a single integer (big-endian)
            # integer = struct.unpack_from('<I', byte_array, i)[0]
            # if(bytes[0] + bytes[1] + bytes[2] + bytes[3] > 0):
            #     print(str(i) + " " + str(bytes[0:3]))
            hidden_figures.append(bytes[3])
            integers.append(int.from_bytes(bytes[0:3],byteorder='little'))
        return (integers, hidden_figures)

# Check if console and sensor are connected
interface, console_connected, left_sensor, right_sensor = MOTIONInterface.acquire_motion_interface()

sensor_connected = left_sensor #default to left sensor

if console_connected and sensor_connected:
    print("MOTION System fully connected.")
else:
    print(f'MOTION System NOT Fully Connected. CONSOLE: {console_connected}, SENSOR: {sensor_connected}')
    
if not sensor_connected:
    print("Sensor Module not connected.")
    exit(1)


user_inputs = []
if(save_histo):
    for i in range(8):
        value = input(f"Enter the SN for camera #{i + 1}: ")
        user_inputs.append(value)

    print("You entered:")
    print(user_inputs)

# turn camera mask into camera positions
CAMERA_POSITIONS = []
for i in range(8):
    if CAMERA_MASK & (1 << i):
        CAMERA_POSITIONS.append(i)

for camera_position in CAMERA_POSITIONS:
    if(not clean_printouts): print(f"Capturing camera at position {camera_position +1}...")

    # turn camera position into camera mask
    CAMERA_MASK_SINGLE = 1 << camera_position
    target = "left"
    if(ENABLE_TEST_PATTERN):
        # print ("Programming camera sensor set test pattern.")
        interface.run_on_sensors("camera_configure_test_pattern", CAMERA_MASK_SINGLE, TEST_PATTERN_ID, target=target)
    else:
        # print ("Programming camera sensor registers.")
        interface.run_on_sensors("camera_configure_registers", CAMERA_MASK_SINGLE, target=target)

    interface.run_on_sensors("switch_camera", camera_position, target=target)
    interface.run_on_sensors("camera_set_gain", gain, target=target)
    interface.run_on_sensors("camera_set_exposure", 0, us=exposure, target=target)
    interface.run_on_sensors("camera_capture_histogram", CAMERA_MASK_SINGLE, target=target)
    histogram = interface.run_on_sensors("camera_get_histogram", CAMERA_MASK_SINGLE, target=target) # returns a list of histograms, one per sensor
    histogram = histogram[target]
    print(histogram)
    if histogram is None:
        print("Histogram frame is None.")
    else:
        histogram = histogram[0:4096]
        (bins, hidden_numbers) = bytes_to_integers(histogram)
        if(save_histo): save_histogram_csv(bins, filename=("histo_cam_"+user_inputs[camera_position]+".csv"))    
        
        if(bins[1023] != 0): print("Saturated Pixels: " + str(bins[1023]))
        bins[1023]=0
        print_weighted_average(bins)
        if(show_histo): plot_10bit_histogram(bins, title="10-bit Histogram")

# Disconnect and cleanup;'.l/m 1
interface.sensors["left"].disconnect()
print("\nSensor Module Test Completed.")

exit(0)