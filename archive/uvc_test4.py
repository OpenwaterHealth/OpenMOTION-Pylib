import cv2
import csv
import time
import logging
import usb.core
import usb.util

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

# Open CSV file for logging
csv_filename = "camera_data.csv"
with open(csv_filename, mode="w", newline="") as file:
    writer = csv.writer(file)
    writer.writerow(["Timestamp", "Frame Width", "Frame Height", "Frame Capture Success"])
    
    while True:
        try:
            start_time = time.time()
            ret, frame = cap.read()
            
            if not ret:
                logging.warning("Frame capture failed!")
                writer.writerow([time.time(), "N/A", "N/A", False])
                continue
            
            frame_height = frame.shape[0]
            frame_width = frame.shape[1]
            writer.writerow([time.time(), frame_width, frame_height, True])
            
            # Display the frame (optional for debugging)
            cv2.imshow("Camera Feed", frame)
            
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
