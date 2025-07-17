import threading
import time
import json
import queue
from omotion.MotionComposite import MOTIONComposite

# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_bulk_imu.py

# Your VID/PID
VID = 0x0483
PID = 0x5A5A
frame_queue = queue.Queue()
print_queue = queue.Queue()
stop_event = threading.Event()

def print_worker(print_queue, stop_event):
    while not stop_event.is_set() or not print_queue.empty():
        try:
            msg = print_queue.get(timeout=0.1)
            print(msg)
            print_queue.task_done()
        except queue.Empty:
            continue
        
def frame_monitor_worker(frame_queue, print_queue, stop_event, stats_interval=1000):
    """Worker thread to monitor frame continuity and calculate bitrate."""
    expected_frame = 1
    first_frame_seen = None
    total_frames = 0
    dropped_frames = 0
    last_stats_time = time.time()
    total_bytes_received = 0
    sample_packet_size = None

    while not stop_event.is_set():
        try:
            data = frame_queue.get(timeout=0.1)

            if 'F' in data:
                current_frame = data['F']

                if first_frame_seen is None:
                    first_frame_seen = current_frame
                    expected_frame = current_frame + 1  # Start continuity from the first seen frame

                total_frames += 1

                # Estimate packet size on first frame
                if sample_packet_size is None:
                    sample_packet_size = len(json.dumps(data).encode('utf-8'))
                    print_queue.put(f"📦 Estimated packet size: {sample_packet_size} bytes")

                total_bytes_received += sample_packet_size

                if current_frame != expected_frame:
                    dropped_frames += 1
                    print_queue.put(
                        f"⚠️ Dropped {dropped_frames} frame(s)! Expected F:{expected_frame}, got F:{current_frame}"
                    )

                expected_frame = current_frame + 1

                # Periodic stats
                if total_frames % stats_interval == 0:
                    current_time = time.time()
                    elapsed = current_time - last_stats_time
                    fps = stats_interval / elapsed if elapsed > 0 else 0
                    bitrate_bps = (sample_packet_size * stats_interval * 8) / elapsed if elapsed > 0 else 0

                    last_stats_time = current_time

                    stats_msg = (
                        f"\n=== Statistics [Frame {total_frames}] ===\n"
                        f"First Frame Seen: {first_frame_seen}\n"
                        f"Frames: {total_frames}\n"
                        f"Dropped: {dropped_frames} ({dropped_frames/max(1,total_frames)*100:.2f}%)\n"
                        f"Current FPS: {fps:.1f}\n"
                        f"Current Bitrate: {bitrate_bps / 1000:.2f} kbps\n"
                        f"Expected next frame: F:{expected_frame}\n"
                        f"Total Data: {total_bytes_received / 1024:.2f} KB\n"
                    )
                    print_queue.put(stats_msg)

            frame_queue.task_done()

        except queue.Empty:
            continue

    # Final stats
    elapsed_total = time.time() - (last_stats_time - (stats_interval / fps if total_frames >= stats_interval and fps > 0 else 0))
    avg_fps = total_frames / elapsed_total if elapsed_total > 0 else 0

    print_queue.put(
        f"\n=== Final Statistics ===\n"
        f"First Frame Seen: {first_frame_seen}\n"
        f"Total Frames: {total_frames}\n"
        f"Dropped Frames: {dropped_frames} ({dropped_frames/max(1,total_frames)*100:.2f}%)\n"
        f"Average FPS: {avg_fps:.1f}\n"
        f"Total Data Transferred: {total_bytes_received / 1024:.2f} KB\n"
        f"Runtime: {elapsed_total:.2f} seconds\n"
    )

printer_thread = threading.Thread(target=print_worker, args=(print_queue, stop_event))
printer_thread.start()

monitor_thread = threading.Thread(
    target=frame_monitor_worker,
    args=(frame_queue, print_queue, stop_event, 1000)
)
monitor_thread.start()

with MOTIONComposite(vid=VID, pid=PID, imu_queue=frame_queue) as motion:
    try:
        # Start IMU manually
        motion.start_imu_stream()
        motion.start_imu_thread()

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("Stopping...")

        # Stop IMU manually
        motion.stop_imu_stream()
        motion.stop_imu_thread()

        # Stop worker threads
        stop_event.set()
        monitor_thread.join()
        printer_thread.join()