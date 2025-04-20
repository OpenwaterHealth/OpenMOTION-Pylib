import asyncio
import time
import numpy as np
import matplotlib.pyplot as plt
from omotion.Interface import MOTIONInterface


# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_receive_frame.py


print("Starting MOTION Sensor Module Test Script...")
BIT_FILE = "bitstream/HistoFPGAFw_impl1_agg.bit"
#BIT_FILE = "bitstream/testcustom_agg.bit"
AUTO_UPLOAD = True
# MANUAL_UPLOAD = True
CAMERA_MASK = 0x40

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
        hist_values = np.frombuffer(histogram_data, dtype='<u4')  # little-endian uint32
        
        # Verify expected length (1024 bins for 10-bit)
        if len(hist_values) != 1024:
            print(f"Warning: Expected 1024 bins, got {len(hist_values)}")
        
        # Plot the histogram
        plt.figure(figsize=(12, 6))
        plt.bar(range(len(hist_values)), hist_values, width=1.0)
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

# Ping Test
print("\n[1] Ping Sensor Module...")
response = interface.sensor_module.ping()
print("Ping successful." if response else "Ping failed.")

# Get Firmware Version
print("\n[2] Reading Firmware Version...")
try:
    version = interface.sensor_module.get_version()
    print(f"Firmware Version: {version}")
except Exception as e:
    print(f"Error reading version: {e}")


# print ("Programming camera sensor registers.")
# if not interface.sensor_module.camera_configure_registers(CAMERA_MASK):
#     print("Failed to configure default registers for camera FPGA.")

# print ("Programming camera sensor set test pattern.")
# if not interface.sensor_module.camera_configure_test_pattern(CAMERA_MASK):
#     print("Failed to set grayscale test pattern for camera FPGA.")

print("Capture histogram frame.")
if not interface.sensor_module.camera_capture_histogram(CAMERA_MASK):
    print("Failed to capture histogram frame.")
else:
    print("Get histogram frame.")
    histogram = interface.sensor_module.camera_get_histogram(CAMERA_MASK)
    if histogram is None:
        print("Histogram frame is None.")
    else:
        print("Histogram frame received successfully.")    
        save_histogram_raw(histogram)    
        plot_10bit_histogram(histogram, title="10-bit Histogram")

# Disconnect and cleanup;'.l/m 1
interface.sensor_module.disconnect()
print("\nSensor Module Test Completed.")

exit(0)