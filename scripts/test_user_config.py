#!/usr/bin/env python3
"""
Example script demonstrating how to read and write user configuration
to the Motion Console device.
"""

import sys
import json
from omotion.Interface import MOTIONInterface
from omotion.config import CONSOLE_MODULE_PID

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_user_config.py

def main():
    # Acquire interface + connection state
    interface, console_connected, left_sensor, right_sensor = MOTIONInterface.acquire_motion_interface()

    if console_connected and left_sensor and right_sensor:
        print("MOTION System fully connected.")
    else:
        print(f'MOTION System NOT Fully Connected. CONSOLE: {console_connected}, SENSOR (LEFT,RIGHT): {left_sensor}, {right_sensor}')
        
    if not console_connected:
        print("Console Module not connected.")
        exit(1)
        
    
    
    # Read current configuration
    print("Reading configuration from device...")
    config = interface.console_module.read_config()
    
    if config is None:
        print("Error: Failed to read configuration")
        return 1
    
    print("\nCurrent configuration:")
    print(f"  Sequence: {config.header.seq}")
    print(f"  CRC: 0x{config.header.crc:04X}")
    print(f"  JSON length: {config.header.json_len}")
    print("\nJSON data:")
    print(config.get_json_str())
    
    # Example: Update a configuration value
    print("\n" + "="*60)
    print("Updating configuration...")
    
    # Modify the configuration
    config.set("example_key", "example_value")
    config.set("timestamp", "2026-02-06T12:00:00Z")
    config.update({
        "device_name": "Motion Console",
        "version": "1.0.0"
    })
    
    print("\nNew configuration:")
    print(config.get_json_str())
    
    # Write updated configuration to device
    print("\nWriting configuration to device...")
    updated_config = interface.console_module.write_config(config)
    
    if updated_config is None:
        print("Error: Failed to write configuration")
        return 1
    
    print("\nConfiguration written successfully!")
    print(f"  New sequence: {updated_config.header.seq}")
    print(f"  New CRC: 0x{updated_config.header.crc:04X}")
    
    # Verify by reading again
    print("\nVerifying configuration...")
    verify_config = interface.console_module.read_config()
    
    if verify_config is None:
        print("Error: Failed to verify configuration")
        return 1
    
    print("\nVerified configuration:")
    print(verify_config.get_json_str())
    
    # Alternative: Write JSON string directly
    print("\n" + "="*60)
    print("Alternative: Writing JSON string directly...")
    
    json_string = json.dumps({
        "test": "direct_json_write",
        "enabled": True,
        "value": 42
    })
    
    result = interface.console_module.write_config_json(json_string)
    
    if result:
        print(f"Success! New sequence: {result.header.seq}")
    else:
        print("Failed to write JSON string")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
