import argparse
import time
import numpy as np
import matplotlib.pyplot as plt
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_receive_frame.py


def plot_10bit_histogram(histogram_data, title="10-bit Histogram"):
    plt.figure(figsize=(12, 6))
    plt.bar(range(len(histogram_data)), histogram_data, width=1.0)
    plt.title(title)
    plt.xlabel("Pixel Value (0-1023)")
    plt.ylabel("Count")
    plt.grid(True, linestyle='--', alpha=0.6)
    plt.show()


def bytes_to_integers(byte_array):
    if len(byte_array) != 4096:
        raise ValueError("Input byte array must be exactly 4096 bytes.")
    integers = []
    hidden_figures = []
    for i in range(0, len(byte_array), 4):
        bytes_ = byte_array[i:i+4]
        hidden_figures.append(bytes_[3])
        integers.append(int.from_bytes(bytes_[0:3], byteorder='little'))
    return (integers, hidden_figures)


def capture_and_process(interface, camera_mask, show_plot=False):
    if not interface.sensor_module.camera_capture_histogram(camera_mask):
        print("Failed to capture histogram frame.")
        return
    histogram = interface.sensor_module.camera_get_histogram(camera_mask)
    if histogram is None:
        print("Histogram frame is None.")
        return

    histogram = histogram[:4096]
    bins, hidden_numbers = bytes_to_integers(histogram)

    print(f"Sum of bins: {sum(bins)}")
    print(f"Frame ID: {hidden_numbers[1023]}")
    if show_plot:
        plot_10bit_histogram(bins, title=f"10-bit Histogram Frame {hidden_numbers[1023]}")


def main():
    parser = argparse.ArgumentParser(description="MOTION Sensor Histogram Capture Tool")
    parser.add_argument("--iter", type=int, default=1, help="Number of histogram captures to run")
    parser.add_argument("--mask", type=lambda x: int(x, 0), default=0xFF, help="Camera mask (e.g., 0x01, 0xFF)")
    parser.add_argument("--pattern", type=lambda x: int(x, 0), default=0x04, help="Camera test pattern ID")
    args = parser.parse_args()

    interface = MOTIONInterface()
    console_connected, sensor_connected = interface.is_device_connected()
    if not (console_connected and sensor_connected):
        print(f'MOTION System NOT Fully Connected. CONSOLE: {console_connected}, SENSOR: {sensor_connected}')
        return

    if not interface.sensor_module.ping():
        print("Ping failed.")
        return

    version = interface.sensor_module.get_version()
    print(f"Firmware Version: {version}")

    print(f"Setting camera test pattern {args.pattern:#04x} with mask {args.mask:#04x}")
    interface.sensor_module.camera_configure_test_pattern(args.mask, args.pattern)

    try:
        for i in range(args.iter):
            print(f"\nIteration {i+1}/{args.iter}")
            capture_and_process(interface, args.mask, show_plot=(args.iter == 1))
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nInterrupted by user.")

    interface.sensor_module.disconnect()
    print("Sensor Module Test Completed.")


if __name__ == "__main__":
    main()
