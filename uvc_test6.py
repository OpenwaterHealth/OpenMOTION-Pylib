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

cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)  # Disable automatic conversion

# Open CSV file for logging
csv_filename = "camera_data.csv"
with open(csv_filename, mode="w", newline="") as file:
    writer = csv.writer(file)
    writer.writerow(["Timestamp", "Frame Width", "Frame Height", "Frame Capture Success", "Channels"])
    start_time = time.time()
    
    while True:
        try:
            ret, frame = cap.read()
            
            if not ret:
                logging.warning("Frame capture failed!")
                writer.writerow([time.time(), "N/A", "N/A", False, "N/A"])
                continue
            
            # Debug: Log frame shape
            logging.debug(f"Captured frame shape: {frame.shape}")

            if len(frame.shape) == 2:
                reshaped_data = frame.reshape((8, 4104))
            
            frame_height = reshaped_data.shape[0]
            frame_width = reshaped_data.shape[1]
            frame_data = reshaped_data.flatten().tolist()  # Convert frame to list

            writer.writerow([time.time(), frame_width, frame_height, frame_data])
            
            # Display the frame (optional for debugging)
            # cv2.imshow("Camera Feed", frame_data)
            
            # Exit on key press
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
            if time.time() - start_time > 2:
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

# reopen camera_data.csv and compute the average frequency of data reception based on the timestamps in the first column
with open(csv_filename, mode="r") as file:
    reader = csv.reader(file)
    timestamps = []
    for row in reader:
        if row[0] != "Timestamp":  # Skip header
            timestamps.append(float(row[0]))
    
    if len(timestamps) > 1:
        intervals = np.diff(timestamps)
        avg_frequency = 1 / np.mean(intervals)
        logging.info(f"Average frequency of data reception: {avg_frequency:.2f} Hz")
    else:
        logging.warning("Not enough data to compute frequency.")