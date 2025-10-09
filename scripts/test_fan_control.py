#!/usr/bin/env python3
"""
Test script for fan control commands on STM32 firmware.

This script tests the SET_FAN_CTL and GET_FAN_CTL commands that control
the FAN_CTL_PIN on the STM32 microcontroller.

Usage:
    python test_fan_control.py

Requirements:
    - STM32 device must be connected and running the updated firmware
    - omotion library must be installed
"""

import sys
import time
import logging
from pathlib import Path

# Add the parent directory to the path to import omotion
sys.path.insert(0, str(Path(__file__).parent.parent))

from omotion.Interface import MOTIONInterface

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_fan_control():
    """Test the fan control commands."""
    
    # Initialize the motion interface
    motion_interface, console_connected, left_sensor, right_sensor = MOTIONInterface.acquire_motion_interface()
    
    if not left_sensor and not right_sensor:
        logger.error("No sensors connected. Please connect a sensor and try again.")
        return False
    
    # Use left sensor if available, otherwise right sensor
    sensor_side = "left" if left_sensor else "right"
    logger.info(f"Testing fan control with {sensor_side} sensor")
    
    try:
        # Test 1: Get initial fan status
        logger.info("=== Test 1: Get initial fan status ===")
        initial_status = motion_interface.sensors[sensor_side].get_fan_control_status()
        logger.info(f"Initial fan status: {'ON' if initial_status else 'OFF'}")
        
        # Test 2: Turn fan ON
        logger.info("=== Test 2: Turn fan ON ===")
        result = motion_interface.sensors[sensor_side].set_fan_control(True)
        if result:
            logger.info("✓ Fan control set to ON successfully")
            time.sleep(1)  # Wait a moment
            
            # Verify the fan is ON
            status = motion_interface.sensors[sensor_side].get_fan_control_status()
            if status:
                logger.info("✓ Fan status confirmed as ON")
            else:
                logger.error("✗ Fan status verification failed - expected ON but got OFF")
                return False
        else:
            logger.error("✗ Failed to set fan control to ON")
            return False
        
        # Test 3: Turn fan OFF
        logger.info("=== Test 3: Turn fan OFF ===")
        result = motion_interface.sensors[sensor_side].set_fan_control(False)
        if result:
            logger.info("✓ Fan control set to OFF successfully")
            time.sleep(5)  # Wait a moment
            
            # Verify the fan is OFF
            status = motion_interface.sensors[sensor_side].get_fan_control_status()
            if not status:
                logger.info("✓ Fan status confirmed as OFF")
            else:
                logger.error("✗ Fan status verification failed - expected OFF but got ON")
                return False
        else:
            logger.error("✗ Failed to set fan control to OFF")
            return False
        
        # Test 4: Toggle test (ON -> OFF -> ON)
        logger.info("=== Test 4: Toggle test ===")
        
        # Turn ON
        motion_interface.sensors[sensor_side].set_fan_control(True)
        time.sleep(0.5)
        status1 = motion_interface.sensors[sensor_side].get_fan_control_status()
        logger.info(f"After setting ON: {'ON' if status1 else 'OFF'}")
        
        # Turn OFF
        motion_interface.sensors[sensor_side].set_fan_control(False)
        time.sleep(0.5)
        status2 = motion_interface.sensors[sensor_side].get_fan_control_status()
        logger.info(f"After setting OFF: {'ON' if status2 else 'OFF'}")
        
        # Turn ON again
        motion_interface.sensors[sensor_side].set_fan_control(True)
        time.sleep(0.5)
        status3 = motion_interface.sensors[sensor_side].get_fan_control_status()
        logger.info(f"After setting ON again: {'ON' if status3 else 'OFF'}")
        
        if status1 and not status2 and status3:
            logger.info("✓ Toggle test passed - all states correct")
        else:
            logger.error("✗ Toggle test failed - unexpected state sequence")
            return False
        
        # Test 5: Multiple rapid toggles
        logger.info("=== Test 5: Rapid toggle test ===")
        for i in range(5):
            state = (i % 2) == 0
            motion_interface.sensors[sensor_side].set_fan_control(state)
            time.sleep(0.2)
            actual_status = motion_interface.sensors[sensor_side].get_fan_control_status()
            expected = "ON" if state else "OFF"
            actual = "ON" if actual_status else "OFF"
            logger.info(f"Toggle {i+1}: Set {expected}, Got {actual}")
            
            if actual_status != state:
                logger.error(f"✗ Rapid toggle test failed at iteration {i+1}")
                return False
        
        logger.info("✓ Rapid toggle test passed")
        
        # Test 6: Restore initial state
        logger.info("=== Test 6: Restore initial state ===")
        motion_interface.sensors[sensor_side].set_fan_control(initial_status)
        time.sleep(0.5)
        final_status = motion_interface.sensors[sensor_side].get_fan_control_status()
        
        if final_status == initial_status:
            logger.info("✓ Initial state restored successfully")
        else:
            logger.warning(f"⚠ Could not restore initial state. Expected: {'ON' if initial_status else 'OFF'}, Got: {'ON' if final_status else 'OFF'}")
        
        logger.info("🎉 All fan control tests passed!")
        return True
        
    except Exception as e:
        logger.error(f"Test failed with exception: {e}")
        return False

def main():
    """Main function."""
    logger.info("Starting fan control test...")
    logger.info("Make sure the STM32 device is connected and running the updated firmware.")
    
    success = test_fan_control()
    
    if success:
        logger.info("✅ All tests completed successfully!")
        sys.exit(0)
    else:
        logger.error("❌ Some tests failed!")
        sys.exit(1)

if __name__ == "__main__":
    main()
