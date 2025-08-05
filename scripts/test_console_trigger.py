import asyncio
import time
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_console_trigger.py

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

    print("\n[0] Set trigger...")
    json_trigger_data = {
        "TriggerFrequencyHz": 40,
        "TriggerPulseWidthUsec": 500,
        "LaserPulseDelayUsec": 100,
        "LaserPulseWidthUsec": 500,
        "LaserPulseSkipInterval": 0,
        "EnableSyncOut": True,
        "EnableTaTrigger": True
    }

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

    print("\n[2] Start trigger...")
    if not interface.console_module.start_trigger():
        print("Failed to start trigger.")
    else:
        print("Press [ENTER] to stop trigger...")
        input()
        interface.console_module.stop_trigger()

    print("\n[3] Set trigger...")
    json_trigger_data = {
        "TriggerFrequencyHz": 25,
        "TriggerPulseWidthUsec": 500,
        "LaserPulseDelayUsec": 100,
        "LaserPulseWidthUsec": 500,
        "LaserPulseSkipInterval": 5,
        "EnableSyncOut": True,
        "EnableTaTrigger": True
    }

    new_setting = interface.console_module.set_trigger_json(data=json_trigger_data)
    if new_setting:
        print(f"Trigger Setting: {new_setting}")
    else:
        print("Failed to get trigger setting.")

    print("\n[4] Start trigger...")
    if not interface.console_module.start_trigger():
        print("Failed to start trigger.")
    else:    
        trigger_setting = interface.console_module.get_trigger_json()
        if trigger_setting:
            print(f"Trigger Setting: {trigger_setting}")
        else:
            print("Failed to get trigger setting.")

        time.sleep(1)
        fsync_pulsecount = interface.console_module.get_fsync_pulsecount()
        print(f"FSYNC PulseCount: {fsync_pulsecount}")

        print("Press [ENTER] to stop trigger...")
        input()
        interface.console_module.stop_trigger()
        
if __name__ == "__main__":
    main()