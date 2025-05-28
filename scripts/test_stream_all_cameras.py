import time
import numpy as np
import matplotlib.pyplot as plt
from omotion.Interface import MOTIONInterface
from typing import Tuple, List

# Configuration
CAMERA_MASK = 0x01
HISTOGRAM_BINS = 1024
HISTOGRAM_BYTES = 4096  # 1024 bins * 4 bytes per bin

def plot_10bit_histogram(histogram_data: List[int], title: str = "10-bit Histogram") -> None:
    """Plots a 10-bit histogram (0-1023) from histogram data.
    
    Args:
        histogram_data: List of histogram bin values
        title: Title for the plot
    """
    try:
        if len(histogram_data) != HISTOGRAM_BINS:
            print(f"Warning: Expected {HISTOGRAM_BINS} bins, got {len(histogram_data)}")
        
        plt.figure(figsize=(12, 6))
        plt.bar(range(len(histogram_data)), histogram_data, width=1.0)
        plt.title(title)
        plt.xlabel("Pixel Value (0-1023)")
        plt.ylabel("Count")
        plt.grid(True, linestyle='--', alpha=0.6)
        plt.show()
    except Exception as e:
        print(f"Error plotting histogram: {e}")

def bytes_to_integers(byte_array: bytes) -> Tuple[List[int], List[int]]:
    """Convert byte array to histogram bins and hidden figures.
    
    Args:
        byte_array: Input bytes (expected length 4096)
    
    Returns:
        Tuple of (histogram_bins, hidden_figures)
    """
    if len(byte_array) != HISTOGRAM_BYTES:
        raise ValueError(f"Input byte array must be exactly {HISTOGRAM_BYTES} bytes.")
    
    integers = []
    hidden_figures = []
    
    for i in range(0, len(byte_array), 4):
        chunk = byte_array[i:i+4]
        hidden_figures.append(chunk[3])
        integers.append(int.from_bytes(chunk[0:3], byteorder='little'))
    
    return integers, hidden_figures

def main():
    print("Starting MOTION Sensor Module Test Script...")
    
    # Initialize interface
    interface = MOTIONInterface()
    
    # Check connections
    console_connected, sensor_connected = interface.is_device_connected()
    if not all([console_connected, sensor_connected]):
        print(f'MOTION System NOT Fully Connected. CONSOLE: {console_connected}, SENSOR: {sensor_connected}')
        if not sensor_connected:
            print("Sensor Module not connected.")
            exit(1)
    else:
        print("MOTION System fully connected.")
    
    # Ping Test
    print("\n[1] Ping Sensor Module...")
    if not interface.sensor_module.ping():
        print("Ping failed.")
        exit(1)
    print("Ping successful.")
    
    # Get Firmware Version
    print("\n[2] Reading Firmware Version...")
    try:
        version = interface.sensor_module.get_version()
        print(f"Firmware Version: {version}")
    except Exception as e:
        print(f"Error reading version: {e}")
    
    # FPGA Configuration
    start_time = time.time()
    print("\n[3] FPGA Configuration Started")
    
    print("Programming camera FPGA")
    if not interface.sensor_module.program_fpga(camera_position=CAMERA_MASK, manual_process=False):
        print("Failed to enter SRAM programming mode for camera FPGA.")
        exit(1)
    
    print("Programming camera sensor registers.")
    if not interface.sensor_module.camera_configure_registers(CAMERA_MASK):
        print("Failed to configure default registers for camera FPGA.")
        exit(1)
    
    print(f"FPGAs programmed | Time: {(time.time() - start_time)*1000:.2f} ms")
    
    # Histogram Capture
    print("\n[4] Capturing histogram frame...")
    if not interface.sensor_module.camera_capture_histogram(CAMERA_MASK):
        print("Failed to capture histogram frame.")
    else:
        print("Getting histogram frame...")
        histogram = interface.sensor_module.camera_get_histogram(CAMERA_MASK)
        
        if histogram is None:
            print("Histogram frame is None.")
        else:
            print(f"Histogram frame received (length: {len(histogram)} bytes)")
            histogram = histogram[:HISTOGRAM_BYTES]
            bins, hidden_numbers = bytes_to_integers(histogram)
            
            print(f"Sum of bins: {sum(bins)}")
            print(f"Frame ID: {hidden_numbers[HISTOGRAM_BINS-1]}")
            plot_10bit_histogram(bins, title="10-bit Histogram")
    
    # Cleanup
    interface.sensor_module.disconnect()
    print("\nSensor Module Test Completed.")

if __name__ == "__main__":
    main()