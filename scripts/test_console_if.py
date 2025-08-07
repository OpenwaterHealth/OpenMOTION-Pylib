import asyncio
import time
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_console_if.py


def main():

    print("Starting MOTION Console Module Test Script...")

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
    print("\n[1] Ping Sensor Module...")
    response = interface.console_module.ping()
    print("Ping successful." if response else "Ping failed.")

    # Get Firmware Version
    print("\n[2] Reading Firmware Version...")
    try:
        version = interface.console_module.get_version()
        print(f"Firmware Version: {version}")
    except Exception as e:
        print(f"Error reading version: {e}")

    # Echo Test
    print("\n[3] Echo Test...")
    try:
        echo_data = b"Hello MOTION!"
        echoed, echoed_len = interface.console_module.echo(echo_data)
        if echoed:
            print(f"Echoed {echoed_len} bytes: {echoed.decode(errors='ignore')}")
        else:
            print("Echo failed.")
    except Exception as e:
        print(f"Echo test error: {e}")

    # Toggle LED
    print("\n[4] Toggle LED...")
    try:
        led_result = interface.console_module.toggle_led()
        print("LED toggled." if led_result else "LED toggle failed.")
        time.sleep(1)  # Wait for a second before toggling off
        led_result = interface.console_module.toggle_led()
    except Exception as e:
        print(f"LED toggle error: {e}")

    # Get HWID
    print("\n[5] Read Hardware ID...")
    try:
        hwid = interface.console_module.get_hardware_id()
        if hwid:
            print(f"Hardware ID: {hwid}")
        else:
            print("Failed to read HWID.")
    except Exception as e:
        print(f"HWID read error: {e}")

    print("\n[6] Scan I2C MUX Channels...")

    mux_index = 1  # Choose MUX 0 or 1
    channel = 0    # Choose channel 0–7

    try:
        addresses = interface.console_module.scan_i2c_mux_channel(mux_index, channel)
        if addresses:
            hex_addresses = [hex(addr) for addr in addresses]
            print(f"Devices found on MUX {mux_index} channel {channel}: {hex_addresses}")
        else:
            print(f"No devices found on MUX {mux_index} channel {channel}.")
    except Exception as e:
        print(f"I2C scan error: {e}")

    print("\n[7] Test Fan...")
    fan_speed = interface.console_module.set_fan_speed(40)
    if fan_speed < 0:
        print("Failed to set Fan Speed.")
    else:
        print(f"Fan Speed: {fan_speed}")

    print("\n[8] Get Fan Speed...")
    fan_speed = interface.console_module.get_fan_speed()
    if fan_speed < 0:
        print("Failed to set Fan Speed.")
    else:
        print(f"Fan Speed: {fan_speed}")

    print("\n[9] Start trigger...")
    if not interface.console_module.start_trigger():
        print("Failed to start trigger.")
    else:
        print("Press [ENTER] to stop trigger...")
        input()
        interface.console_module.stop_trigger()

        
if __name__ == "__main__":
    main()