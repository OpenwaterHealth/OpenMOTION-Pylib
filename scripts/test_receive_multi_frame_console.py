import asyncio
import queue
import sys
import threading
import time
import numpy as np
import matplotlib.pyplot as plt
from omotion.Interface import MOTIONInterface


# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_receive_multi_frame_console.py


print("Starting MOTION Sensor Module Test Script...")
BIT_FILE = "bitstream/HistoFPGAFw_impl1_agg.bit"
#BIT_FILE = "bitstream/testcustom_agg.bit"
AUTO_UPLOAD = True
# MANUAL_UPLOAD = True
CAMERA_MASK = 0xFF
SCAN_TIME = 10  # seconds

file_writer_queue = queue.Queue()
histo_queue = queue.Queue()
stop_event = threading.Event()

def file_writer(file_writer_queue, stop_event, filename = 'histogram.bin'):
    with open(filename, 'wb') as f:
        while not stop_event.is_set() or not file_writer_queue.empty():
            try:
                msg = file_writer_queue.get(timeout=0.1)
                if msg:
                    f.write(msg)
                file_writer_queue.task_done()
            except queue.Empty:
                continue

#if there is a camera mask argued in to the program, replace CAMERA_MASK with that after checking that it is less than 0xFF
if len(sys.argv) > 1:
    try:
        CAMERA_MASK = int(sys.argv[1], 16)
        if CAMERA_MASK > 0xFF:
            raise ValueError("Camera mask must be less than 0xFF")
    except ValueError as e:
        print(f"Invalid camera mask argument: {e}")
        sys.exit(1)
if len(sys.argv) > 2:
    try:
        SCAN_TIME = int(sys.argv[2])
        if SCAN_TIME < 1:
            raise ValueError("Scan time must be a positive integer")
    except ValueError as e:
        print(f"Invalid scan time argument: {e}")
        sys.exit(1)

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

# Create an instance of the Sensor interface
interface = MOTIONInterface()

# Check if console and sensor are connected
console_connected, sensor_connected = interface.is_device_connected()

if console_connected and sensor_connected:
    print("MOTION System fully connected.")
else:
    print(f'MOTION System NOT Fully Connected. CONSOLE: {console_connected}, SENSOR: {sensor_connected}')
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

# Start Threads
printer_thread = threading.Thread(target=file_writer, args=(file_writer_queue, stop_event))
printer_thread.start()
#step 1 enable cameras - this means turn on streaming mode and start the reception

interface._sensor_uart.start_histo_thread(expected_frame_size=32833, histo_queue=file_writer_queue)

interface.sensor_module.enable_camera_fsin_ext() # Enable cameras with FSIN ext from console
interface.sensor_module.camera_set_gain(16) 

# Ping Test
print("\n[1] Ping Sensor Module...")
response = interface.console_module.ping()
print("Ping successful." if response else "Ping failed.")

print("\n[0] Set trigger...")
json_trigger_data = {
    "TriggerFrequencyHz": 40,
    "TriggerPulseWidthUsec": 500,
    "LaserPulseDelayUsec": 100,
    "LaserPulseWidthUsec": 500,
    "LaserPulseSkipInterval": 600,
    "EnableSyncOut": True,
    "EnableTaTrigger": True
}

new_setting = interface.console_module.set_trigger_json(data=json_trigger_data)
if new_setting:
    print(f"Trigger Setting: {new_setting}")
else:
    print("Failed to get trigger setting.")

print("\n[1] Get trigger...")
trigger_setting = interface.console_module.get_trigger_json()
if trigger_setting:
    print(f"Trigger Setting: {trigger_setting}")
else:
    print("Failed to get trigger setting.")



#Set laser power
print("\n[2] Set laser power...")

laser_params = []
#TA params
#2a config
# laser_params.append({"muxIdx": 1,   
#                      "channel": 4, 
#                      "i2cAddr": 0x41, 
#                      "offset": 0x00,
#                      "dataToSend": bytearray([0x28, 0x09, 0x00])}) 
# laser_params.append({"muxIdx": 1,   
#                      "channel": 4, 
#                      "i2cAddr": 0x41, 
#                      "offset": 0x06,
#                      "dataToSend": bytearray([0xD9, 0x30])}) 
# 5a config
laser_params.append({"muxIdx": 1,   
                     "channel": 4, 
                     "i2cAddr": 0x41, 
                     "offset": 0x00,
                     "dataToSend": bytearray([0x28, 0x09, 0x00])}) 
laser_params.append({"muxIdx": 1,   
                     "channel": 4, 
                     "i2cAddr": 0x41, 
                     "offset": 0x06,
                     "dataToSend": bytearray([0x17, 0x7a])}) 


#Seed Params
laser_params.append({"muxIdx": 1,   
                     "channel": 5, 
                     "i2cAddr": 0x41, 
                     "offset": 0x02,
                     "dataToSend": bytearray([0x00, 0x00])}) 
laser_params.append({"muxIdx": 1,   
                     "channel": 5, 
                     "i2cAddr": 0x41, 
                     "offset": 0x06,
                     "dataToSend": bytearray([0xAE, 0x3d])}) 
laser_params.append({"muxIdx": 1,   
                     "channel": 5, 
                     "i2cAddr": 0x41, 
                     "offset": 0x04,
                     "dataToSend": bytearray([0x53, 0x07])}) 
laser_params.append({"muxIdx": 1,   
                     "channel": 5, 
                     "i2cAddr": 0x41, 
                     "offset": 0x08,
                     "dataToSend": bytearray([0xd7, 0x1e])}) 

for laser_param in laser_params:
    muxIdx = laser_param["muxIdx"]
    channel = laser_param["channel"]
    i2cAddr = laser_param["i2cAddr"]
    offset = laser_param["offset"]
    dataToSend = laser_param["dataToSend"]

    if not interface.console_module.write_i2c_packet(mux_index=muxIdx, channel=channel, device_addr=i2cAddr, reg_addr=offset, data=dataToSend):
        print("Failed to set laser power.")

print("\n[3] Enable Cameras")
if not interface.sensor_module.enable_camera(CAMERA_MASK):
    print("Failed to enable cameras.")

# Activate Laser
print("\n[2] Start trigger...")
if not interface.console_module.start_trigger():
    print("Failed to start trigger.")
    
time.sleep(SCAN_TIME) # Wait for a moment to ensure FSIN is activated
interface.console_module.stop_trigger()

# step 5 disable cameras, cancel reception etc
print("\n[7] Deactivate Cameras...")
if not interface.sensor_module.disable_camera(CAMERA_MASK):
    print("Failed to enable cameras.")
time.sleep(1) # wait a few frames for the camera to exhaust itself before disabling the camera


stop_event.set()
printer_thread.join()


time.sleep(1)
# Disconnect and cleanup;'.l/m 1
interface.sensor_module.disconnect()
interface.console_module.disconnect()
print("\nSensor Module Test Completed.")

exit(0)