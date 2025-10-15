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

# Create an instance of the Sensor interface
interface = MOTIONInterface()

# Check if console and sensor are connected
console_connected, left_connected, right_connected = interface.is_device_connected()
sensor_connected = left_connected or right_connected
if console_connected and sensor_connected:
    if left_connected:
        target = "left"
    if right_connected:
        target = "right"
    print("MOTION System fully connected.")
else:
    print(f'MOTION System NOT Fully Connected. CONSOLE: {console_connected}, SENSOR: {sensor_connected}')
    exit(1)


# Ping Test
print("\n[1] Ping Sensor Module...")
response = interface.sensors[target].ping()
print("Ping successful." if response else "Ping failed.")

print("\n[2] Ping Console...")
response = interface.console_module.ping()
print("Ping successful." if response else "Ping failed.")

# Get Firmware Version
print("\n[3] Reading Firmware Version...")
try:
    version = interface.sensors[target].get_version()
    print(f"Sensor Firmware Version: {version}")
except Exception as e:
    print(f"Error reading version: {e}")

# Get Firmware Version
print("\n[4] Reading Firmware Version...")
try:
    version = interface.console_module.get_version()
    print(f"Console Firmware Version: {version}")
except Exception as e:
    print(f"Error reading version: {e}")

# Start Threads
printer_thread = threading.Thread(target=file_writer, args=(file_writer_queue, stop_event))
printer_thread.start()
interface._dual_composite.left.histo.start_streaming(histo_queue,32833)

interface.sensors[target].enable_camera_fsin_ext() # Enable cameras with FSIN ext from console

json_trigger_data = {
    "TriggerFrequencyHz": 40,
    "TriggerPulseWidthUsec": 500,
    "LaserPulseDelayUsec": 100,
    "LaserPulseWidthUsec": 500,
    "LaserPulseSkipInterval": 600,
    # "LaserPulseSkipDelayUsec": 1200,
    # "LaserPulseDemodInterval": 600,
    # "LaserPulseDemodDelayUsec": 300,
    "EnableSyncOut": True,
    "EnableTaTrigger": True
}
print("\n[5] Setting trigger...")
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
print("\n[6] Set laser power...")

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

print("\n[7] Enable Cameras")
if not interface.sensors[target].enable_camera(CAMERA_MASK):
    print("Failed to enable cameras.")

# Activate Laser
print("\n[8] Start trigger...")
if not interface.console_module.start_trigger():
    print("Failed to start trigger.")
    
time.sleep(SCAN_TIME) # Wait for a moment to ensure FSIN is activated

print("\n[9] Stop trigger...")
if not interface.console_module.stop_trigger():
    print("Failed to stop trigger.")

time.sleep(1) # wait a few frames to ensure all frames are received

# step 5 disable cameras, cancel reception etc
print("\n[10] Deactivate Cameras...")
if not interface.sensors[target].disable_camera(CAMERA_MASK):
    print("Failed to disable cameras.")

time.sleep(1) # wait a few frames for the camera to exhaust itself before disabling the camera

interface._dual_composite.left.histo.stop_streaming()


stop_event.set()
printer_thread.join()


time.sleep(1)
# Disconnect and cleanup;'.l/m 1
interface.sensors[target].disconnect()
interface.console_module.disconnect()
print("\nSensor Module Test Completed.")

exit(0)
