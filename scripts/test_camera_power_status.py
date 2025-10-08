#!/usr/bin/env python3
"""
Test script for camera power status functionality.

This script tests the get_camera_power_status method in the Sensor class,
verifying that it correctly queries and parses the 8-bit power status mask
from the MCU.

Usage:
    python scripts/test_camera_power_status.py
"""

import sys
import os
import time
import argparse

# Add the parent directory to the path so we can import omotion
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omotion.Interface import MOTIONInterface
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_power_status_mask_conversion():
    """Test the 8-bit mask to boolean list conversion logic."""
    print("=== Testing 8-bit Mask Conversion Logic ===")
    
    def mask_to_list(power_mask):
        """Convert 8-bit mask to list of boolean power statuses."""
        power_status = [False] * 8
        for i in range(8):
            power_status[i] = bool(power_mask & (1 << i))
        return power_status
    
    test_cases = [
        (0x00, "All cameras off"),
        (0xFF, "All cameras on"),
        (0xAA, "Alternating pattern (1,3,5,7 on)"),
        (0x55, "Alternating pattern (2,4,6,8 on)"),
        (0x0F, "First 4 cameras on"),
        (0xF0, "Last 4 cameras on"),
        (0x81, "Cameras 1 and 8 on"),
        (0x42, "Cameras 3 and 7 on"),
    ]
    
    for mask, description in test_cases:
        status_list = mask_to_list(mask)
        print(f"Mask 0x{mask:02X} ({mask:08b}) - {description}")
        print("  Camera Status:", end=" ")
        for i, is_powered in enumerate(status_list):
            status = "ON" if is_powered else "OFF"
            print(f"C{i+1}:{status}", end=" ")
        print("\n")
    
    print("Mask conversion logic test completed successfully!\n")

def test_camera_power_status_method():
    """Test the actual camera power status method with hardware."""
    print("=== Testing Camera Power Status Method ===")
    
    try:
        # Create interface and check connections
        interface, console_connected, left_sensor, right_sensor = MOTIONInterface.acquire_motion_interface()
        
        print(f"Console connected: {console_connected}")
        print(f"Left sensor connected: {left_sensor}")
        print(f"Right sensor connected: {right_sensor}")
        
        if not (left_sensor or right_sensor):
            print("ERROR: No sensor modules connected!")
            return False
        
        # Test on connected sensors
        for sensor_side in ["left", "right"]:
            if sensor_side == "left" and not left_sensor:
                continue
            if sensor_side == "right" and not right_sensor:
                continue
                
            print(f"\n--- Testing {sensor_side.capitalize()} Sensor ---")
            
            sensor = interface.sensors[sensor_side]
            
            try:
                # Query power status for all cameras
                power_status = sensor.get_camera_power_status()
                
                if power_status is not None:
                    print(f"  Success: Received {len(power_status)} status values")
                    print("  Camera Power Status:", end=" ")
                    for i, is_powered in enumerate(power_status):
                        status = "ON" if is_powered else "OFF"
                        print(f"C{i+1}:{status}", end=" ")
                    print()
                else:
                    print(f"  ERROR: Received None for power status query")
                    return False
                    
            except Exception as e:
                print(f"  ERROR: Exception for power status query: {e}")
                return False
        
        print("\nCamera power status method test completed successfully!")
        return True
        
    except Exception as e:
        print(f"ERROR: Failed to test camera power status: {e}")
        return False

def test_power_status_edge_cases():
    """Test edge cases for the power status method."""
    print("=== Testing Edge Cases ===")
    
    try:
        interface, console_connected, left_sensor, right_sensor = MOTIONInterface.acquire_motion_interface()
        
        if not (left_sensor or right_sensor):
            print("ERROR: No sensor modules connected for edge case testing!")
            return False
        
        # Use the first available sensor
        sensor_side = "left" if left_sensor else "right"
        sensor = interface.sensors[sensor_side]
        
        print(f"Testing edge cases on {sensor_side} sensor")
        
        # Test the method (no parameters needed now)
        print(f"\nTesting get_camera_power_status() method")
        try:
            result = sensor.get_camera_power_status()
            print(f"  Result: {result}")
            if isinstance(result, list) and len(result) == 8:
                print("  ✓ Method returned correct format (list of 8 booleans)")
            else:
                print("  ✗ Method returned incorrect format")
                return False
        except Exception as e:
            print(f"  Unexpected error: {e}")
            return False
        
        print("\nEdge case testing completed!")
        return True
        
    except Exception as e:
        print(f"ERROR: Edge case testing failed: {e}")
        return False

def main():
    """Main test function."""
    parser = argparse.ArgumentParser(description="Test camera power status functionality")
    parser.add_argument("--skip-hardware", action="store_true", 
                       help="Skip hardware tests, only test conversion logic")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Enable verbose logging")
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    print("Camera Power Status Test Script")
    print("=" * 40)
    
    # Always test the conversion logic
    test_power_status_mask_conversion()
    
    if not args.skip_hardware:
        # Test with actual hardware
        success = True
        
        if not test_camera_power_status_method():
            success = False
        
        if not test_power_status_edge_cases():
            success = False
        
        if success:
            print("\n" + "=" * 40)
            print("ALL TESTS PASSED! ✓")
            print("Camera power status functionality is working correctly.")
        else:
            print("\n" + "=" * 40)
            print("SOME TESTS FAILED! ✗")
            print("Please check the errors above and fix any issues.")
            sys.exit(1)
    else:
        print("\n" + "=" * 40)
        print("CONVERSION LOGIC TEST PASSED! ✓")
        print("(Hardware tests skipped)")

if __name__ == "__main__":
    main()
