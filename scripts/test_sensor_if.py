import asyncio
import time
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_sensor_if.py

print("Starting MOTION Sensor Module Test Script...")
BIT_FILE = "bitstream\HistoFPGAFw_impl1_agg.bit"

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
    interface.sensor_module.disconnect()
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

# Echo Test
print("\n[3] Echo Test...")
try:
    echo_data = b"Hello LIFU!"
    echoed, echoed_len = interface.sensor_module.echo(echo_data)
    if echoed:
        print(f"Echoed {echoed_len} bytes: {echoed.decode(errors='ignore')}")
    else:
        print("Echo failed.")
except Exception as e:
    print(f"Echo test error: {e}")

# Toggle LED
print("\n[4] Toggle LED...")
try:
    led_result = interface.sensor_module.toggle_led()
    print("LED toggled." if led_result else "LED toggle failed.")
    time.sleep(1)  # Wait for a second before toggling off
    led_result = interface.sensor_module.toggle_led()
except Exception as e:
    print(f"LED toggle error: {e}")

# Get HWID
print("\n[5] Read Hardware ID...")
try:
    hwid = interface.sensor_module.get_hardware_id()
    if hwid:
        print(f"Hardware ID: {hwid}")
    else:
        print("Failed to read HWID.")
except Exception as e:
    print(f"HWID read error: {e}")

start_time = time.time()
print("FPGA Configuration Started")
if not interface.sensor_module.reset_camera_sensor(0x01):
    print("Failed to reset camera sensor.")

if not interface.sensor_module.activate_camera_fpga(0x01):
    print("Failed to activate camera FPGA.")

if not interface.sensor_module.enable_camera_fpga(0x01):
    print("Failed to enable camera FPGA.")

if not interface.sensor_module.check_camera_fpga(0x01):
    print("Failed to check id of camera FPGA.")

if not interface.sensor_module.enter_sram_prog_fpga(0x01):
    print("Failed to enter sram programming mode for camera FPGA.")

if not interface.sensor_module.erase_sram_fpga(0x01):
    print("Failed to erase sram for camera FPGA.")

# wait for erase
# send bitstream to camera FPGA
print("sending bitstream to camera FPGA")
interface.sensor_module.send_bitstream_fpga(BIT_FILE)

if not interface.sensor_module.get_status_fpga(0x01):
    print("Failed to get status for camera FPGA.")

if not interface.sensor_module.program_fpga(0x01):
    print("Failed to get user code for camera FPGA.")

if not interface.sensor_module.get_usercode_fpga(0x01):
    print("Failed to get user code for camera FPGA.")

if not interface.sensor_module.get_status_fpga(0x01):
    print("Failed to get status for camera FPGA.")

if not interface.sensor_module.exit_sram_prog_fpga(0x01):
    print("Failed to enter sram programming mode for camera FPGA.")

print(f"FPGAs programmed | Time: {(time.time() - start_time)*1000:.2f} ms")

# Disconnect and cleanup;'.l/m 1
interface.sensor_module.disconnect()
print("\nSensor Module Test Completed.")

exit(0)