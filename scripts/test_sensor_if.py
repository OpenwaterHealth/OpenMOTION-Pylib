import asyncio
import time
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_sensor_if.py

print("Starting MOTION Sensor Module Test Script...")
BIT_FILE = "bitstream/HistoFPGAFw_impl1_agg.bit"
#BIT_FILE = "bitstream/testcustom_agg.bit"
AUTO_UPLOAD = True
# MANUAL_UPLOAD = True
CAMERA_MASK = 0x03

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

def upload_camera_bitstream(auto_upload: bool = False, camera_position: int =0) -> bool:
    print("FPGA Configuration Started")
    
    if auto_upload:
        # send bitstream to camera FPGA
        #print("sending bitstream to camera FPGA")
        #interface.sensor_module.send_bitstream_fpga(BIT_FILE)

        print("Programming camera FPGA")
        if not interface.sensor_module.program_fpga(camera_position=camera_position, manual_process=False):
            print("Failed to enter sram programming mode for camera FPGA.")
            return False
    
    else:
        # Manual upload process
        if not interface.sensor_module.reset_camera_sensor(camera_position):
            print("Failed to reset camera sensor.")
            return False

        if not interface.sensor_module.activate_camera_fpga(camera_position):
            print("Failed to activate camera FPGA.")
            return False

        if not interface.sensor_module.enable_camera_fpga(camera_position):
            print("Failed to enable camera FPGA.")
            return False

        if not interface.sensor_module.check_camera_fpga(camera_position):
            print("Failed to check id of camera FPGA.")
            return False

        if not interface.sensor_module.enter_sram_prog_fpga(camera_position):
            print("Failed to enter sram programming mode for camera FPGA.")
            return False

        if not interface.sensor_module.erase_sram_fpga(camera_position):
            print("Failed to erase sram for camera FPGA.")
            return False

        # wait for erase
        # send bitstream to camera FPGA
        print("sending bitstream to camera FPGA")
        if not interface.sensor_module.send_bitstream_fpga(BIT_FILE):
            print("Failed to send bitstream to camera FPGA.")
            return False

        if not interface.sensor_module.get_status_fpga(camera_position):
            print("Failed to get status for camera FPGA.")
            return False

        if not interface.sensor_module.program_fpga(camera_position=camera_position, manual_process=True):
            print("Failed to enter sram programming mode for camera FPGA.")
            return False

        if not interface.sensor_module.get_usercode_fpga(camera_position):
            print("Failed to get user code for camera FPGA.")
            return False

        if not interface.sensor_module.get_status_fpga(camera_position):
            print("Failed to get status for camera FPGA.")
            return False
        
    return True

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

# turn camera mask into camera positions
CAMERA_POSITIONS = []
for i in range(8):
    if CAMERA_MASK & (1 << i):
        CAMERA_POSITIONS.append(i)

for camera_position in CAMERA_POSITIONS:
    print(f"\n[3] Programming camera FPGA at position {camera_position+1}...")

    # turn camera position into camera mask
    CAMERA_MASK_SINGLE = 1 << camera_position
    start_time = time.time()
    if not upload_camera_bitstream(AUTO_UPLOAD, CAMERA_MASK_SINGLE):
        print("Failed to upload camera bitstream.")
        exit(1)
    print(f"FPGAs programmed | Time: {(time.time() - start_time)*1000:.2f} ms")

    print ("Programming camera sensor registers.")
    if not interface.sensor_module.camera_configure_registers(CAMERA_MASK_SINGLE):
        print("Failed to configure default registers for camera FPGA.")

    # print ("Programming camera sensor set test pattern.")
    # if not interface.sensor_module.camera_configure_test_pattern(CAMERA_MASK):
    #     print("Failed to set grayscale test pattern for camera FPGA.")

    # print("Capture histogram frame.")
    # if not interface.sensor_module.camera_capture_histogram(CAMERA_MASK):
    #     print("Failed to capture histogram frame.")

# Disconnect and cleanup;'.l/m 1
interface.sensor_module.disconnect()
print("\nSensor Module Test Completed.")

exit(0)