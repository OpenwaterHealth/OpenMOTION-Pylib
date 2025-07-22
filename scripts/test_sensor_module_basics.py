import asyncio, time
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_sensor_module_basics.py

print("Starting MOTION Sensor Module Test Script...")

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

# Activate then deactivate FSIN
print("\n[6] Activate FSIN...")
try:
    fsin_result = interface.sensor_module.enable_aggregator_fsin()
    print("FSIN activated." if fsin_result else "FSIN activation failed.")
    
    # Wait for a moment to ensure FSIN is activated
    time.sleep(1)

    print("\n[7] Deactivate FSIN...")
    fsin_result = interface.sensor_module.disable_aggregator_fsin()
    print("FSIN deactivated." if fsin_result else "FSIN deactivation failed.")
except Exception as e:
    print(f"FSIN activate error: {e}")
# Query status of camera 0, 3, and 7 (bitmask 0b10001001 = 0x89)
mask = 0xFF

try:
    status_map = interface.sensor_module.get_camera_status(mask)

    if status_map is None:
        print("Failed to get camera status.")
    else:
        for cam_id, status in status_map.items():
            readable = interface.sensor_module.decode_camera_status(status)
            print(f"Camera {cam_id} Status: 0x{status:02X} -> {readable}")

except Exception as e:
    print(f"Error reading camera status: {e}")

# Disconnect and cleanup
interface.sensor_module.disconnect()
print("\nSensor Module Test Completed.")

exit(0)
