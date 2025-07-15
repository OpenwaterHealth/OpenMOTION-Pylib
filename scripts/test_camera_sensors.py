import asyncio
import time
import argparse
import sys

import numpy as np
import matplotlib.pyplot as plt
from omotion.Interface import MOTIONInterface


# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_camera_sensors.py


print("Starting MOTION Sensor Module Test Script...")
BIT_FILE = "bitstream/HistoFPGAFw_impl1_agg.bit"
#BIT_FILE = "bitstream/testcustom_agg.bit"
AUTO_UPLOAD = True
# MANUAL_UPLOAD = True
CAMERA_MASK = 0x01

ENABLE_TEST_PATTERN = True
TEST_PATTERN_ID = 0x04

## Test Patterns
# 0 Gradient bars
# 1 Solid color
# 2 Squares
# 3 Continuous Gradient
# 4 disabled

ENABLE_TEST_PATTERN = True
TEST_PATTERN_ID = 0x04

## Test Patterns
# 0 Gradient bars
# 1 Solid color
# 2 Squares
# 3 Continuous Gradient
# 4 disabled

def parse_args():
    parser = argparse.ArgumentParser(description="Camera Firmware Loader and Test Runner")

    parser.add_argument('--auto', action='store_true', help='Use firmware stored in camera')
    parser.add_argument('--bitfile', type=str, help='Path to bitstream firmware file')
    parser.add_argument('--mask', type=lambda x: int(x, 0), default=0xff,
                        help='Camera selection mask (bitmap), e.g., 0xff for all, 0x01 for camera 1, etc.')
    parser.add_argument('--pattern', type=int, default=4, help='Test pattern to run (default: 4 for live camera)')
    parser.add_argument('--iter', type=int, default=1, help='Iterations to run (default: 1)')

    args = parser.parse_args()

    # If not --auto, bitfile is required
    if not args.auto and not args.bitfile:
        parser.error("the following arguments are required when not using --auto: --bitfile")

    return args

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

def save_histogram_raw(histogram_data: bytearray, filename: str = "histogram.bin"):
    """Saves raw histogram bytes to a binary file."""
    try:
        with open(filename, "wb") as f:  # 'wb' = write binary
            f.write(histogram_data)
        print(f"Successfully saved raw histogram to {filename}")
    except Exception as e:
        print(f"Error saving histogram: {e}")

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
#            integer = struct.unpack_from('<I', byte_array, i)[0]
            # if(bytes[0] + bytes[1] + bytes[2] + bytes[3] > 0):
            #     print(str(i) + " " + str(bytes[0:3]))
            hidden_figures.append(bytes[3])
            integers.append(int.from_bytes(bytes[0:3],byteorder='little'))
        return (integers, hidden_figures)

def run(args):
    interface = MOTIONInterface()
    console_connected, sensor_connected = interface.is_device_connected()

    if not sensor_connected:
        print(f'MOTION System NOT Fully Connected. CONSOLE: {console_connected}, SENSOR: {sensor_connected}')
        exit(1)

    print("\n[1] Ping Sensor Module...")
    if interface.sensor_module.ping():
        print("Ping successful.")
    else:
        print("Ping failed.")

    print(f"Programming camera FPGA on mask 0x{args.mask:02X}.")
    if not interface.sensor_module.program_fpga(camera_position=args.mask, manual_process=False):
        print("Failed to enter sram programming mode for camera FPGA.")
        return False
    
    if args.pattern != 4:
        print(f"Programming camera test pattern {args.pattern} on mask 0x{args.mask:02X}.")
        if not interface.sensor_module.camera_configure_test_pattern(args.mask, args.pattern):
            print("Failed to set grayscale test pattern for camera FPGA.")
    else:
        print(f"Configuring camera registers on mask 0x{args.mask:02X}.")
        if not interface.sensor_module.camera_configure_registers(args.mask):
            print("Failed to configure default registers for camera FPGA.")

    print("\nCapture histogram frame.")
    if not interface.sensor_module.camera_capture_histogram(args.mask):
        print("Failed to capture histogram frame.")
        return

    histogram = interface.sensor_module.camera_get_histogram(args.mask)
    if histogram is None:
        print("Histogram frame is None.")
        return

    print(f"Histogram frame received successfully. Length: {len(histogram)} bytes.")
    histogram = histogram[0:4096]
    bins, hidden_numbers = bytes_to_integers(histogram)
    print(f"Sum of bins: {sum(bins)}")
    print(f"Frame ID: {hidden_numbers[1023]}")
    plot_10bit_histogram(bins, title="10-bit Histogram")
    interface.sensor_module.disconnect()

def main():
    args = parse_args()

    print(f"Auto: {args.auto}")
    print(f"Bitfile: {args.bitfile}")
    print(f"Mask: 0x{args.mask:02X}")
    print(f"Pattern: {args.pattern}")
    print(f"Iterations: {args.iter}")

    # Example of how you would proceed
    if args.auto:
        print("Using firmware stored in the camera.")
    else:
        print(f"Loading firmware from: {args.bitfile}")

    for iteration in range(args.iter):
        print(f"Iteration {iteration + 1}/{args.iter}: Running pattern {args.pattern} on mask 0x{args.mask:02X}")
        run(args)

if __name__ == "__main__":
    main()