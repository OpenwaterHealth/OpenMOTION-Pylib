import threading
import time
import queue
import argparse
from omotion.MotionComposite import MOTIONComposite
from omotion.utils import util_crc16


# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_bulk_histo.py --cam 1

VID = 0x0483
PID = 0x5A5A
PER_CAMERA_FRAME_BYTES = 4104

parser = argparse.ArgumentParser(description="HISTO Streaming Test")
parser.add_argument('--cam', type=int, default=1, help='Number of cameras to expect (default 1)')
args = parser.parse_args()

CAMERA_COUNT = args.cam
expected_frame_size = 4 + (PER_CAMERA_FRAME_BYTES * CAMERA_COUNT) + 4  # SOF + data + EOF

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

def histo_monitor_worker(histo_queue, print_queue, stop_event, camera_count, stats_interval=600):
    PER_CAMERA_FRAME_BYTES = 4104
    expected_frame_size = 4 + (PER_CAMERA_FRAME_BYTES * camera_count) + 4  # SOF + camera data + EOF

    expected_frames = [None] * camera_count
    dropped_frames = [0] * camera_count
    bad_frames = 0
    total_frames = 0
    last_stats_time = time.time()
    total_bytes_received = 0

    while not stop_event.is_set():
        try:
            data = histo_queue.get(timeout=0.1)
            if len(data) != expected_frame_size:
                bad_frames += 1
                histo_queue.task_done()
                continue

            offset = 0
            sof = int.from_bytes(data[offset:offset+4], 'little')
            if sof != 0xDEADBEEF:
                bad_frames += 1
                print_queue.put(f"[HISTO] Bad SOF marker 0x{sof:X}")
                histo_queue.task_done()
                continue

            offset += 4  # Skip SOF marker

            valid = True
            for cam in range(camera_count):
                frame_id = int.from_bytes(data[offset:offset+4], 'little')
                histogram_offset = offset + 4
                meta_offset = histogram_offset + 4096
                meta = int.from_bytes(data[meta_offset:meta_offset+4], 'little')
                crc = (meta >> 16) & 0xFFFF
                cam_id = meta & 0xFFFF

                if cam_id != cam:
                    valid = False
                    print_queue.put(f"[HISTO] Bad camera ID {cam_id} at camera {cam}")
                    break

                # Validate CRC on histogram data
                histogram_bytes = data[histogram_offset:histogram_offset + 4096]
                calculated_crc = util_crc16(histogram_bytes)
                if calculated_crc != crc:
                    valid = False
                    print_queue.put(f"[HISTO] CRC FAIL on cam {cam}: expected 0x{crc:04X}, got 0x{calculated_crc:04X}")
                    break

                if expected_frames[cam] is None:
                    expected_frames[cam] = frame_id + 1
                else:
                    if frame_id != expected_frames[cam]:
                        dropped_frames[cam] += 1
                        print_queue.put(
                            f"[HISTO] Camera {cam} dropped frame! Expected {expected_frames[cam]}, got {frame_id}"
                        )
                    expected_frames[cam] = frame_id + 1

                offset += PER_CAMERA_FRAME_BYTES

            eof = int.from_bytes(data[offset:offset+4], 'little')
            if eof != 0xFEEDBEEF:
                bad_frames += 1
                print_queue.put(f"[HISTO] Bad EOF marker 0x{eof:X}")
                histo_queue.task_done()
                continue

            if not valid:
                bad_frames += 1
                histo_queue.task_done()
                continue

            total_frames += 1
            total_bytes_received += expected_frame_size

            if total_frames % stats_interval == 0:
                elapsed = time.time() - last_stats_time
                fps = stats_interval / elapsed if elapsed > 0 else 0
                bitrate_bps = (expected_frame_size * stats_interval * 8) / elapsed if elapsed > 0 else 0

                drops_summary = ", ".join(f"Cam{c}: {d}" for c, d in enumerate(dropped_frames))
                print_queue.put(
                    f"\n=== HISTO Statistics [Block Frame {total_frames}] ===\n"
                    f"{drops_summary}\n"
                    f"Bad Frames: {bad_frames}\n"
                    f"FPS: {fps:.1f}\n"
                    f"Estimated Bitrate: {bitrate_bps / 1000:.2f} kbps\n"
                    f"Total Data: {total_bytes_received / 1024:.2f} KB\n"
                )
                last_stats_time = time.time()

            histo_queue.task_done()
        except queue.Empty:
            continue

# Start Threads
printer_thread = threading.Thread(target=print_worker, args=(print_queue, stop_event))
printer_thread.start()

monitor_thread = threading.Thread(
    target=histo_monitor_worker,
    args=(histo_queue, print_queue, stop_event, CAMERA_COUNT, 800)
)
monitor_thread.start()


# Main Streaming Session
with MOTIONComposite(vid=VID, pid=PID, histo_queue=histo_queue) as motion:
    try:
        motion.start_histo_stream(camera_count=CAMERA_COUNT, frame_size=expected_frame_size)

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("Stopping...")
        stop_event.set()
        monitor_thread.join()
        printer_thread.join()
