import asyncio
import time
import argparse
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_sensor_if.py --camera-mask 0x01

print("Starting MOTION Sensor Module Test Script...")
BIT_FILE = "bitstream/HistoFPGAFw_impl1_agg.bit"
#BIT_FILE = "bitstream/testcustom_agg.bit"
AUTO_UPLOAD = True
# MANUAL_UPLOAD = True
CAMERA_MASK = 0xFF

def parse_args():
    parser = argparse.ArgumentParser(description="MOTION Sensor FPGA Test")
    parser.add_argument(
        "--camera-mask",
        type=lambda x: int(x, 0),  # allows 0xFF or decimal
        default=0xFF,
        help="Bitmask for cameras (default 0xFF)"
    )
    parser.add_argument(
        "--auto-upload",
        action="store_true",
        default=True,
        help="Enable auto-upload of FPGA bitstream (default True)"
    )
    parser.add_argument(
        "--manual-upload",
        action="store_true",
        help="Force manual upload (overrides --auto-upload)"
    )
    parser.add_argument(
        "--bit-file",
        type=str,
        help="Path to FPGA bitstream file (required if manual upload)"
    )
    return parser.parse_args()

def program_all_sensors(interface, camera_position, bit_file):
    steps = [
        ("reset_camera_sensor", "Failed to reset camera sensor."),
        ("activate_camera_fpga", "Failed to activate camera FPGA."),
        ("enable_camera_fpga", "Failed to enable camera FPGA."),
        ("check_camera_fpga", "Failed to check ID of camera FPGA."),
        ("enter_sram_prog_fpga", "Failed to enter SRAM programming mode for camera FPGA."),
        ("erase_sram_fpga", "Failed to erase SRAM for camera FPGA."),
    ]

    # Run the initial steps
    for method, error_msg in steps:
        results = interface.run_on_sensors(method, camera_position)
        for side, success in results.items():
            if not success:
                print(f"{error_msg} ({side})")
                return False

    # Send bitstream
    print("Sending bitstream to camera FPGA")
    results = interface.run_on_sensors("send_bitstream_fpga", filename=bit_file)
    for side, success in results.items():
        if not success:
            print(f"Failed to send bitstream to camera FPGA ({side})")
            return False

    # Status after bitstream
    results = interface.run_on_sensors("get_status_fpga", camera_position)
    for side, success in results.items():
        if not success:
            print(f"Failed to get status for camera FPGA ({side})")
            return False

    # Program FPGA
    results = interface.run_on_sensors("program_fpga", camera_position=camera_position, manual_process=True)
    for side, success in results.items():
        if not success:
            print(f"Failed to program FPGA ({side})")
            return False

    # Get usercode
    results = interface.run_on_sensors("get_usercode_fpga", camera_position)
    for side, success in results.items():
        if not success:
            print(f"Failed to get usercode for camera FPGA ({side})")
            return False

    # Final status
    results = interface.run_on_sensors("get_status_fpga", camera_position)
    for side, success in results.items():
        if not success:
            print(f"Failed to get status for camera FPGA ({side})")
            return False

    print("✅ Camera FPGA programming complete for all connected sensors.")
    return True

def upload_camera_bitstream(interface, auto_upload: bool, camera_position: int, bit_file: str) -> bool:
 
    print("FPGA Configuration Started")
    
    if auto_upload:
        # send bitstream to camera FPGA
        #print("sending bitstream to camera FPGA")
        #interface.sensor_module.send_bitstream_fpga(BIT_FILE)
        print("Programming camera FPGA")
        results = interface.run_on_sensors(
            "program_fpga", 
            camera_position=camera_position, 
            manual_process=False
        )
        
        for side, success in results.items():
            if not success:
                print(f"❌ Failed to program FPGA on {side} sensor.")
                return False
    else:
        # Manual upload process
        if not program_all_sensors(interface, camera_position, BIT_FILE):
            return False
        
    return True

def run_sensor_tests(interface, camera_mask, auto_upload, bit_file) -> bool:
    # Ping Test
    print("\n[1] Ping Sensor Module...")
    ping_results = interface.run_on_sensors("ping")
    print(ping_results)  

    # Get Firmware Version
    print("\n[2] Reading Firmware Version...")
    version_results = interface.run_on_sensors("get_version")
    print(version_results)  

    # Get HWID
    print("\n[5] Read Hardware ID...")
    hwid_results = interface.run_on_sensors("get_hardware_id")
    print(hwid_results)  

    # turn camera mask into camera positions
    camera_positions = [i for i in range(8) if camera_mask & (1 << i)]

    for pos in camera_positions:
        print(f"\nProgramming camera FPGA at position {pos + 1}...")
        cam_mask_single = 1 << pos
        start_time = time.time()
        if not upload_camera_bitstream(interface, auto_upload, cam_mask_single, bit_file):
            print("Failed to upload camera bitstream.")
            exit(1)
        print(f"FPGAs programmed | Time: {(time.time() - start_time)*1000:.2f} ms")

        print ("Programming camera sensor registers.")
        
        print("Programming camera sensor registers.")
        print(interface.run_on_sensors("camera_configure_registers", cam_mask_single))

        # print ("Programming camera sensor set test pattern.")
        # if not interface.sensor_module.camera_configure_test_pattern(CAMERA_MASK):
        #     print("Failed to set grayscale test pattern for camera FPGA.")

        # print("Capture histogram frame.")
        # if not interface.sensor_module.camera_capture_histogram(CAMERA_MASK):
        #     print("Failed to capture histogram frame.")

    return True

def main():

    args = parse_args()

    if not args.auto_upload and not args.bit_file:
        print("❌ --bit-file is required when manual upload is selected.")
        exit(1)

    # Acquire interface + connection state
    interface, console_connected, left_sensor, right_sensor = MOTIONInterface.acquire_motion_interface()

    if console_connected and left_sensor and right_sensor:
        print("MOTION System fully connected.")
    else:
        print(f'MOTION System NOT Fully Connected. CONSOLE: {console_connected}, SENSOR (LEFT,RIGHT): {left_sensor}, {right_sensor}')

    if not left_sensor and not right_sensor:
        print("Sensor Module not connected.")
        exit(1)

    run_sensor_tests(interface, args.camera_mask, not args.manual_upload, args.bit_file)

    print("\nSensor Module Test Completed.")
    
if __name__ == "__main__":
    main()
