
import argparse
import time
import threading
import queue
from datetime import datetime
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\capture_frames.py

MAX_DURATION = 120  # seconds

def parse_args():
    def parse_mask(x):
        try:
            return int(x, 0)  # allows hex like 0x11 or decimal like 17
        except ValueError:
            raise argparse.ArgumentTypeError(f"Invalid camera mask value: {x}")
        
    parser = argparse.ArgumentParser(description="Capture MOTION camera data")

    parser.add_argument(
        "--camera-mask",
        type=parse_mask,
        default=0xFF,
        help="Bitmask for cameras (e.g., 0x11 for cameras 0 and 4, default 0xFF)"
    )
    parser.add_argument(
        "--duration",
        type=int,
        required=True,
        help=f"Duration in seconds (max {MAX_DURATION})"
    )
    parser.add_argument(
        "--subject-id",
        type=str,
        required=True,
        help="Subject or patient identifier"
    )
    parser.add_argument(
        "--disable-laser",
        action="store_true",
        help="If set, disables external frame sync (laser). Otherwise, enables external frame sync by default."
    )
    return parser.parse_args()

def write_stream_to_file(queue_obj, stop_event, filename):
    with open(filename, "wb") as f:
        while not stop_event.is_set() or not queue_obj.empty():
            try:
                data = queue_obj.get(timeout=0.100)
                if data:
                    f.write(data)
                queue_obj.task_done()
            except queue.Empty:
                continue

def main():
    print("Starting MOTION Capture Data Script...")

    args = parse_args()

    if args.duration > MAX_DURATION:
        print(f"Error: Duration cannot exceed {MAX_DURATION} seconds.")
        exit(1)

    print("Starting MOTION Capture Data Script...")
    print(f"Camera Mask: 0x{args.camera_mask:02X}")
    print(f"Duration: {args.duration} seconds")

    # Acquire interface + connection state
    interface, console_connected, left_sensor, right_sensor = MOTIONInterface.acquire_motion_interface()

    if console_connected and left_sensor and right_sensor:
        print("MOTION System fully connected.")
    else:
        print(f'MOTION System NOT Fully Connected. CONSOLE: {console_connected}, SENSOR (LEFT,RIGHT): {left_sensor}, {right_sensor}')
        exit(1)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stream_threads = []
    stop_events = []

    if not args.disable_laser:
        # enable external frame sync
        print("\nEnabling external frame sync...")
        results = interface.run_on_sensors("enable_camera_fsin_ext", args.camera_mask)
        for side, success in results.items():
            if not success:
                print(f"Failed to enable external frame sync on {side}.")
                exit(1)
    else:
        print("\nLaser sync disabled. Using internal sensor-generated frame sync.")

    # Enable cameras
    print("\nEnabling cameras...")
    results = interface.run_on_sensors("enable_camera", args.camera_mask)
    for side, success in results.items():
        if not success:
            print(f"Failed to enable camera on {side}.")
            exit(1)
    
    # Setup streaming
    for side in ("left", "right"):
        sensor = interface.sensors.get(side)
        if sensor and sensor.is_connected():
            q = queue.Queue()
            stop_evt = threading.Event()
            sensor.uart.histo.start_streaming(q, expected_size=32833)  # adjust size if needed probably should be exact based on mask
            filename = f"scan_{args.subject_id}_{timestamp}_{side}_mask{args.camera_mask:02X}.raw"
            t = threading.Thread(target=write_stream_to_file, args=(q, stop_evt, filename), daemon=True)
            t.start()
            stream_threads.append((t, stop_evt))
            stop_events.append(stop_evt)
            print(f"[{side.upper()}] Streaming to: {filename}")

    # Activate Laser
    print("\nStart trigger...")
    if not interface.console_module.start_trigger():
        print("Failed to start trigger.")

    # Start capture loop
    start_time = time.time()
    elapsed = 0

    try:
        while elapsed < args.duration:
            elapsed = time.time() - start_time
            # in case we wnat to do something here
            time.sleep(1)  # capture every 1 second; adjust as needed

    except KeyboardInterrupt:
        print("\n🛑 Capture interrupted by user.")

    print("\nStop trigger...")
    if not interface.console_module.stop_trigger():
        print("Failed to stop trigger.")

    results = interface.run_on_sensors("disable_camera", args.camera_mask)
    for side, success in results.items():
        if not success:
            print(f"Failed to disable camera on {side}.")
            

    # Stop streaming threads
    for side in ("left", "right"):
        sensor = interface.sensors.get(side)
        if sensor:
            sensor.uart.histo.stop_streaming()

    # Signal all threads to stop
    for evt in stop_events:
        evt.set()

    # Wait for all threads to finish
    for t, _ in stream_threads:
        t.join()
                
    print("Capture session complete.")

if __name__ == "__main__":
    main()