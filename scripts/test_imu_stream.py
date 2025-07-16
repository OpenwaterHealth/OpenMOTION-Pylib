import sys
import time
from omotion.config import OW_BAD_CRC, OW_BAD_PARSE, OW_ERROR, OW_IMU, OW_IMU_OFF, OW_IMU_ON, OW_UNKNOWN
import usb.core
import usb.util
import json
import queue
import threading
from omotion.Interface import MOTIONInterface, MOTIONUart

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_imu_stream.py

# ---- Constants ----
VID = 0x0483
PID = 0x5A5A
# PID = 0x5750

OW_CMD = 0xE2


OW_CMD_PING = 0x00  
OW_CMD_VERSION = 0x02
OW_CMD_ECHO = 0x03

EP_IN = 0x83
EP_SIZE = 64
TIMEOUT = 100  # milliseconds
STATS_INTERVAL = 1000  # Print stats every 1000 frames

# Thread-safe queue for print statements
print_queue = queue.Queue()
# Queue for frame data processing
frame_queue = queue.Queue()
# Event to signal threads to stop
stop_event = threading.Event()

def print_worker():
    """Worker thread to handle print statements"""
    while not stop_event.is_set():
        try:
            msg = print_queue.get(timeout=0.1)
            print(msg)
            print_queue.task_done()
        except queue.Empty:
            continue

def frame_monitor_worker():
    """Worker thread to monitor frame continuity and calculate bitrate"""
    expected_frame = 1
    total_frames = 0
    dropped_frames = 0
    last_stats_time = time.time()
    total_bytes_received = 0  # Track total data volume
    sample_packet_size = None  # Will be set from first packet
    
    while not stop_event.is_set():
        try:
            data = frame_queue.get(timeout=0.1)
            
            if 'F' in data:
                current_frame = data['F']
                total_frames += 1
                
                # Estimate packet size from first frame (assuming JSON serialization)
                if sample_packet_size is None:
                    sample_packet_size = len(json.dumps(data).encode('utf-8'))
                    print_queue.put(f"📦 Estimated packet size: {sample_packet_size} bytes")
                
                # Accumulate total bytes
                total_bytes_received += sample_packet_size
                
                if current_frame != expected_frame:
                    dropped_frames += 1
                    print_queue.put(f"⚠️ Dropped {dropped_frames} frame(s)! Expected F:{expected_frame}, got F:{current_frame}")
                
                expected_frame = current_frame + 1
                
                # Print stats every STATS_INTERVAL frames
                if total_frames % STATS_INTERVAL == 0:
                    current_time = time.time()
                    elapsed = current_time - last_stats_time
                    fps = STATS_INTERVAL / elapsed if elapsed > 0 else 0
                    
                    # Calculate bitrate (convert bytes to bits, divide by time)
                    current_bitrate = (sample_packet_size * STATS_INTERVAL * 8) / elapsed if elapsed > 0 else 0
                    
                    last_stats_time = current_time
                    
                    stats_msg = (
                        f"\n=== Statistics [Frame {total_frames}] ===\n"
                        f"Frames: {total_frames}\n"
                        f"Dropped: {dropped_frames} ({dropped_frames/max(1,total_frames)*100:.2f}%)\n"
                        f"Current FPS: {fps:.1f}\n"
                        f"Current Bitrate: {current_bitrate/1000:.2f} kbps\n"
                        f"Expected next frame: F:{expected_frame}\n"
                        f"Total Data: {total_bytes_received/1024:.2f} KB\n"
                    )
                    print_queue.put(stats_msg)
            
            frame_queue.task_done()
        except queue.Empty:
            continue
    
    # Final statistics when stopping
    elapsed_total = time.time() - (last_stats_time - (STATS_INTERVAL/fps if total_frames >= STATS_INTERVAL and fps > 0 else 0))
    avg_fps = total_frames / elapsed_total if elapsed_total > 0 else 0
    
    print_queue.put(
        f"\n=== Final Statistics ===\n"
        f"Total Frames: {total_frames}\n"
        f"Dropped Frames: {dropped_frames} ({dropped_frames/max(1,total_frames)*100:.2f}%)\n"
        f"Average FPS: {avg_fps:.1f}\n"
        f"Total Data Transferred: {total_bytes_received/1024:.2f} KB\n"
        f"Runtime: {elapsed_total:.2f} seconds\n"
    )

def read_usb_stream(dev, endpoint=EP_IN, timeout=TIMEOUT):
    data = bytearray()
    while True:
        try:            
            chunk = dev.read(endpoint, EP_SIZE, timeout=timeout)
            data.extend(chunk)
            # If packet is shorter than max size, it's the end
            if len(chunk) < EP_SIZE:
                break
        except usb.core.USBError as e:
            print(f"USB read error: {e}")
            break
    return data.decode(errors='ignore')  # or return raw if needed

def enumerate_and_print_interfaces(vid, pid):
    dev = usb.core.find(idVendor=vid, idProduct=pid)
    if dev is None:
        print("Device not found.")
        return

    dev.set_configuration()
    cfg = dev.get_active_configuration()

    print(f"\n=== Enumerating interfaces for VID=0x{vid:04X}, PID=0x{pid:04X} ===")

    for intf in cfg:
        print(f"\nInterface #{intf.bInterfaceNumber}:")
        print(f"  Class     : 0x{intf.bInterfaceClass:02X}")
        print(f"  SubClass  : 0x{intf.bInterfaceSubClass:02X}")
        print(f"  Protocol  : 0x{intf.bInterfaceProtocol:02X}")
        print(f"  Endpoints : {len(intf.endpoints())}")

        for ep in intf.endpoints():
            addr = ep.bEndpointAddress
            dir_str = "IN" if usb.util.endpoint_direction(addr) == usb.util.ENDPOINT_IN else "OUT"
            print(f"    - Endpoint 0x{addr:02X} ({dir_str}), Type: {ep.bmAttributes & 0x03}, MaxPacket: {ep.wMaxPacketSize}")

            # Optional: Read from bulk IN endpoints
            if usb.util.endpoint_direction(addr) == usb.util.ENDPOINT_IN and (ep.bmAttributes & 0x03) == usb.util.ENDPOINT_TYPE_BULK:
                try:
                    data = dev.read(addr, ep.wMaxPacketSize, timeout=200)
                    print(f"      Sample Data: {list(data)}")
                except usb.core.USBError as e:
                    print(f"      No data or timeout (err: {e})")

def main_imu_data_stream():

    print("Stream IMU Data")
    # Create an instance of the Sensor interface
    interface = MOTIONInterface()

    # Claim the interface
    print("set config")
    interface._sensor_uart.dev.set_configuration()
    print("claim interface")
    dev = usb.util.claim_interface(interface._sensor_uart.dev, interface._sensor_uart.imu_interface)

    print("data stream on")
    if not interface.sensor_module.imu_data_stream_on():
        return 
    
    try:
        while not stop_event.is_set():
            json_str = read_usb_stream(interface._sensor_uart.dev, endpoint=interface._sensor_uart.imu_ep_in)            
            if json_str:
                for line in json_str.splitlines():
                    try:
                        data = json.loads(line)
                        # Put raw data in frame queue for monitoring
                        frame_queue.put(data)
                        # Optionally put in print queue if you want to see all data
                        # print_queue.put(f"Data: {data}")
                    except json.JSONDecodeError:
                        print_queue.put(f"Invalid JSON: {line}")                
            else:
                print_queue.put("No data received.")
            
            # Remove sleep to run USB at maximum speed
            # The queue will handle buffering
            
    except KeyboardInterrupt:
        print_queue.put("\nStopped by user.")
        print("data stream off")
        if not interface.sensor_module.imu_data_stream_off():
            return         
    finally:
        print("data stream off")
        if not interface.sensor_module.imu_data_stream_off():
            return         
        usb.util.release_interface(dev, 2)
        usb.util.dispose_resources(dev)
        stop_event.set()

# ---- Main ----
def main():
    myUart = MOTIONUart(vid=VID, pid=PID, baudrate=921600, timeout=5, desc="sensor", demo_mode=False, async_mode=False)
    if myUart == None:
        print("Error establishing uart object")
        sys.exit(1)

    myUart.check_usb_status()
    if myUart.is_connected():
        print("MOTION MOTIONSensor connected.")
    else:
        print("MOTION MOTIONSensor NOT Connected.")

    echo_data = b"Hello VCP COMMS, we want to test the length past 64 bytes which is the max packet size for FS!"

    r = myUart.send_packet(id=None, packetType=OW_CMD, command=OW_CMD_ECHO, data=echo_data)
    myUart.clear_buffer()
    if r.data_len > 0:
        print(f"Received {r}")
        print(f"Echoed: {r.data.decode(errors='ignore')}")
    else:
        print("Uart command error")


if __name__ == "__main__":
    # enumerate_and_print_interfaces(vid=VID, pid=PID)

    # Start worker threads
    print_thread = threading.Thread(target=print_worker, daemon=True)
    frame_thread = threading.Thread(target=frame_monitor_worker, daemon=True)
    print_thread.start()
    frame_thread.start()
    
    
    try:
        main_imu_data_stream()
    except Exception as e:
        print_queue.put(f"Main Error: {e}")
    finally:
        stop_event.set()
        print_thread.join()
        frame_thread.join()

    # main()