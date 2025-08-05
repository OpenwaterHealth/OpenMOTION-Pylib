import asyncio
import time
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_fpga_if.py


def main():
    print("Starting MOTION Console FPGA Test Script...")

    # Acquire interface + connection state
    interface, console_connected, left_sensor, right_sensor = MOTIONInterface.acquire_motion_interface()

    if console_connected and left_sensor and right_sensor:
        print("MOTION System fully connected.")
    else:
        print(f'MOTION System NOT Fully Connected. CONSOLE: {console_connected}, SENSOR (LEFT,RIGHT): {left_sensor}, {right_sensor}')
        
    if not console_connected:
        print("Console Module not connected.")
        exit(1)

    # Ping Test
    print("\n[1] Ping Console Module...")
    response = interface.console_module.ping()
    print("Ping successful." if response else "Ping failed.")

    # Read Firmware Version
    print("\n[2] Reading Firmware Version...")
    try:
        version = interface.console_module.get_version()
        print(f"Firmware Version: {version}")
    except Exception as e:
        print(f"Error reading version: {e}")


    # TA - mux_idx: 1; channel: 4; i2c_addr: 0x41 }
    # Seed - mux_idx: 1; channel: 5; i2c_addr: 0x41 }
    # Safety EE - mux_idx: 1; channel: 6; i2c_addr: 0x41 }
    # Safety OPT - mux_idx: 1; channel: 7; i2c_addr: 0x41 }
        
    # Read FPGA Test
    print("\n[3] Read data from FPGA register...")
    try:
        fpga_data, fpga_data_len = interface.console_module.read_i2c_packet(mux_index=1, channel=4, device_addr=0x41, reg_addr=0x00, read_len=2)
        if fpga_data is None:
            print(f"Read FPGA Failed")
        else:
            print(f"Read FPGA Success")
            print(f"Raw bytes: {fpga_data.hex(' ')}")  # Print as hex bytes separated by spaces

    except Exception as e:
        print(f"Error writing FPGA register: {e}")

    print("\n[3] Read data from FPGA register...")
    try:
        fpga_data, fpga_data_len = interface.console_module.read_i2c_packet(mux_index=1, channel=5, device_addr=0x41, reg_addr=0x00, read_len=2)
        if fpga_data is None:
            print(f"Read FPGA Failed")
        else:
            print(f"Read FPGA Success")
            print(f"Raw bytes: {fpga_data.hex(' ')}")  # Print as hex bytes separated by spaces

    except Exception as e:
        print(f"Error writing FPGA register: {e}")

    print("\n[3] Read data from FPGA register...")
    try:
        fpga_data, fpga_data_len = interface.console_module.read_i2c_packet(mux_index=1, channel=6, device_addr=0x41, reg_addr=0x00, read_len=2)
        if fpga_data is None:
            print(f"Read FPGA Failed")
        else:
            print(f"Read FPGA Success")
            print(f"Raw bytes: {fpga_data.hex(' ')}")  # Print as hex bytes separated by spaces

    except Exception as e:
        print(f"Error writing FPGA register: {e}")

    print("\n[3] Read data from FPGA register...")
    try:
        fpga_data, fpga_data_len = interface.console_module.read_i2c_packet(mux_index=1, channel=7, device_addr=0x41, reg_addr=0x00, read_len=2)
        if fpga_data is None:
            print(f"Read FPGA Failed")
        else:
            print(f"Read FPGA Success")
            print(f"Raw bytes: {fpga_data.hex(' ')}")  # Print as hex bytes separated by spaces

    except Exception as e:
        print(f"Error writing FPGA register: {e}")

    # Write FPGA Test
    print("\n[4] Write data to FPGA register...")
    try:
        if interface.console_module.write_i2c_packet(mux_index=1, channel=4, device_addr=0x41, reg_addr=0x00, data=b'\x01\x04'):
            print(f"Write FPGA Success")
        else:
            print(f"Write FPGA Failed")
    except Exception as e:
        print(f"Error writing FPGA register: {e}")


    # Write FPGA Test
    print("\n[4] Write data to FPGA register...")
    try:
        if interface.console_module.write_i2c_packet(mux_index=1, channel=5, device_addr=0x41, reg_addr=0x00, data=b'\x01\x05'):
            print(f"Write FPGA Success")
        else:
            print(f"Write FPGA Failed")
    except Exception as e:
        print(f"Error writing FPGA register: {e}")

    # Write FPGA Test
    print("\n[4] Write data to FPGA register...")
    try:
        if interface.console_module.write_i2c_packet(mux_index=1, channel=6, device_addr=0x41, reg_addr=0x00, data=b'\x01\x06'):
            print(f"Write FPGA Success")
        else:
            print(f"Write FPGA Failed")
    except Exception as e:
        print(f"Error writing FPGA register: {e}")

    # Write FPGA Test
    print("\n[4] Write data to FPGA register...")
    try:
        if interface.console_module.write_i2c_packet(mux_index=1, channel=7, device_addr=0x41, reg_addr=0x00, data=b'\x01\x07'):
            print(f"Write FPGA Success")
        else:
            print(f"Write FPGA Failed")
    except Exception as e:
        print(f"Error writing FPGA register: {e}")

    
if __name__ == "__main__":
    main()
