import threading
import time
import queue
from omotion.MotionComposite import MOTIONComposite

# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_bulk_histo.py

VID = 0x0483
PID = 0x5A5A

histo_queue = queue.Queue()
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

def histo_monitor_worker(histo_queue, print_queue, stop_event, stats_interval=600):
    expected_frame = None
    total_frames = 0
    dropped_frames = 0
    bad_frames = 0
    last_stats_time = time.time()
    total_bytes_received = 0
    sample_packet_size = 4112  # Fixed size for your HISTO frames

    while not stop_event.is_set():
        try:
            data = histo_queue.get(timeout=0.1)
            if len(data) != sample_packet_size:
                bad_frames += 1
                histo_queue.task_done()
                continue

            sof = int.from_bytes(data[0:4], 'little')
            frame_id = int.from_bytes(data[4:8], 'little')
            meta = int.from_bytes(data[4104:4108], 'little')
            eof = int.from_bytes(data[4108:4112], 'little')

            if sof != 0xDEADBEEF or eof != 0xFEEDBEEF:
                bad_frames += 1
                print_queue.put(f"[HISTO] Bad markers SOF=0x{sof:X}, EOF=0x{eof:X}")
                histo_queue.task_done()
                continue

            total_frames += 1
            total_bytes_received += sample_packet_size

            if expected_frame is None:
                expected_frame = frame_id + 1
            else:
                if frame_id != expected_frame:
                    dropped_frames += 1
                    print_queue.put(
                        f"[HISTO] Dropped frame(s)! Expected {expected_frame}, got {frame_id}"
                    )
                expected_frame = frame_id + 1

            if total_frames % stats_interval == 0:
                elapsed = time.time() - last_stats_time
                fps = stats_interval / elapsed if elapsed > 0 else 0
                bitrate_bps = (sample_packet_size * stats_interval * 8) / elapsed if elapsed > 0 else 0

                print_queue.put(
                    f"\n=== HISTO Statistics [Frame {total_frames}] ===\n"
                    f"Dropped Frames: {dropped_frames}\n"
                    f"Bad Frames: {bad_frames}\n"
                    f"FPS: {fps:.1f}\n"
                    f"Estimated Bitrate: {bitrate_bps / 1000:.2f} kbps\n"
                    f"Total Data: {total_bytes_received / 1024:.2f} KB\n"
                )
                last_stats_time = time.time()

            histo_queue.task_done()
        except queue.Empty:
            continue


printer_thread = threading.Thread(target=print_worker, args=(print_queue, stop_event))
printer_thread.start()

monitor_thread = threading.Thread(
    target=histo_monitor_worker,
    args=(histo_queue, print_queue, stop_event, 600)
)
monitor_thread.start()

with MOTIONComposite(vid=VID, pid=PID, histo_queue=histo_queue) as motion:
    try:
        # Start HISTO manually
        motion.start_histo_stream()
        motion.start_histo_thread()

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("Stopping...")

        # Stop HISTO manually
        motion.stop_histo_stream()
        motion.stop_histo_thread()

        stop_event.set()
        monitor_thread.join()
        printer_thread.join()
