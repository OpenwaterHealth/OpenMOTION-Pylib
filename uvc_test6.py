import cv2
import csv
import time
import logging
import usb.core
import usb.util
import numpy as np

# Configure logging
logging.basicConfig(
    filename="camera_debug.log", 
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# Check USB devices for UVC camera
def check_usb_devices():
    devices = usb.core.find(find_all=True)
    for device in devices:
        logging.info(f"USB Device Found: ID {device.idVendor}:{device.idProduct}")

# Initialize the camera
camera_index = 0  # Change if necessary
cap = None
try:
    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        raise RuntimeError("Could not open camera.")
    logging.info("Camera successfully initialized.")
except Exception as e:
    logging.error(f"Camera initialization failed: {e}")
    exit(1)
# cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'NV12'))
# cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)  # Try disabling auto-conversion
# Open CSV file for logging
csv_filename = "camera_data.csv"
with open(csv_filename, mode="w", newline="") as file:
    writer = csv.writer(file)
    writer.writerow(["Timestamp", "Frame Width", "Frame Height", "Frame Capture Success", "Channels"])
    
    while True:
        try:
            start_time = time.time()
            ret, frame = cap.read()
            
            if not ret:
                logging.warning("Frame capture failed!")
                writer.writerow([time.time(), "N/A", "N/A", False, "N/A"])
                continue
            
            # Debug: Log frame shape
            logging.debug(f"Captured frame shape: {frame.shape}")
            
            # Convert NV12 to RGB
            if len(frame.shape) == 2:
                height = int(frame.shape[0] * 2 / 3)  # NV12 format height calculation
                y_plane = frame[:height, :]
                uv_plane = frame[height:, :].reshape(-1, frame.shape[1] // 2, 2)
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_YUV2RGB_NV12)
            else:
                logging.warning("Unexpected number of channels, skipping conversion.")
                frame_rgb = frame  # Use raw frame for debugging
            
            frame_height = frame_rgb.shape[0]
            frame_width = frame_rgb.shape[1]
            num_channels = frame_rgb.shape[2] if len(frame_rgb.shape) == 3 else 1
            frame_data = frame.flatten().tolist()  # Convert frame to list

            writer.writerow([time.time(), frame_width, frame_height, True, num_channels,frame_data])
            
            # Display the frame (optional for debugging)
            cv2.imshow("Camera Feed", frame_rgb)
            
            # Exit on key press
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
        except KeyboardInterrupt:
            logging.info("User interrupted, shutting down gracefully.")
            break
        except Exception as e:
            logging.error(f"Unexpected error: {e}")
            break
    
cap.release()
cv2.destroyAllWindows()
logging.info("Camera application exited.")