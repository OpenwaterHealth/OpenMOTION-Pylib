import cv2
import numpy as np

# Open the UVC device (typically starts at ID 0)
# Replace 0 with the appropriate device index if you have multiple cameras
camera = cv2.VideoCapture(0, cv2.CAP_DSHOW)  # Use CAP_DSHOW for Windows
# Set desired resolution (if needed)
camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

if not camera.isOpened():
    print("Unable to open UVC device")
    exit()

print("UVC device opened successfully. Reading data...")

while True:
    # Read a single frame from the UVC device
    ret, frame = camera.read()

    if not ret:
        print("Failed to capture frame")
        break

    # Simulate processing 32KB packets by splitting the frame data
    packet_size = 32 * 1024
    frame_data = frame.tobytes()
    for i in range(0, len(frame_data), packet_size):
        packet = frame_data[i:i + packet_size]
        print(f"Processing packet of size: {len(packet)} bytes")

    # Display the frame (optional)
    cv2.imshow("UVC Frame", frame)

    # Press 'q' to quit
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

camera.release()
cv2.destroyAllWindows()
