import cv2

cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)  # Disable automatic conversion

# Capture a frame
ret, frame = cap.read()
if not ret:
    print("Failed to capture frame")
    cap.release()
    exit()

# Print frame shape
print("Frame shape:", frame.shape)  # Expecting (height, width, 3) or (height, width)

# Check if OpenCV thinks it's converting RGB
is_rgb = cap.get(cv2.CAP_PROP_CONVERT_RGB)
print("Auto RGB Conversion Enabled:", bool(is_rgb))

# Try getting the FOURCC format again
fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
codec = "".join([chr((fourcc >> (8 * i)) & 0xFF) for i in range(4)])
print("Camera FOURCC Format:", codec if codec.isprintable() else "Unknown/Not Reported")

cap.release()
