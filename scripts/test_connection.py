import asyncio
import time
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_console_if.py

async def main():
    print("Starting MOTION Console Module Test Script...")

    # Create an instance of the Sensor interface
    interface = MOTIONInterface(run_async=True, demo_mode=False)

    interface.sensor_module.uart.start_monitoring(interval=1)

    await interface.sensor_module.uart.monitoring_task

    time.sleep(20)  # Allow some time for monitoring to start
    print("Checking if the sensor device is connected...")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Test script interrupted by user.")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        print("Exiting test script...")