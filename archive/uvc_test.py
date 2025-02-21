import uvc
import numpy as np

def process_frame(frame):
    """
    Unparcel a frame into 8 arrays of 4 KB each.
    
    Args:
        frame (uvc.Frame): The frame object received from the UVC device.
        
    Returns:
        List of NumPy arrays: 8 arrays of 4 KB each.
    """
    # Ensure the frame data is a multiple of 8 * 4096 (4 KB)
    frame_data = frame.bgr  # Assuming data is received as a BGR image
    total_size = len(frame_data)
    expected_size = 8 * 4096  # 32 KB

    if total_size < expected_size:
        print(f"Frame size too small: {total_size} bytes")
        return None

    if total_size % (8 * 4096) != 0:
        print(f"Unexpected frame size: {total_size} bytes, not a multiple of 32 KB")
        return None

    # Split the frame data into 8 arrays of 4 KB
    arrays = np.split(np.frombuffer(frame_data[:expected_size], dtype=np.uint8), 8)
    return arrays

def main():
    # Find available UVC devices
    devices = uvc.device_list()
    if not devices:
        print("No UVC devices found.")
        return

    # Open the first available device
    dev = uvc.open(devices[0]['uid'])
    print(f"Connected to device: {devices[0]['name']}")

    # Set the desired resolution and frame rate (adjust as needed)
    dev.streaming_config = dev.get_stream_ctrl_format_size(
        fourcc='MJPG',  # Format
        width=640,      # Width
        height=480,     # Height
        fps=30          # Frame rate
    )

    try:
        # Start streaming frames
        for frame in dev.capture():
            arrays = process_frame(frame)
            if arrays:
                for idx, array in enumerate(arrays):
                    print(f"Array {idx}: {array[:10]}...")  # Print first 10 bytes as a sample
    except KeyboardInterrupt:
        print("Exiting...")
    finally:
        dev.close()

if __name__ == "__main__":
    main()
