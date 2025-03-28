import usb.core
import usb.util

VENDOR_ID = 0x0483  # Replace with your device's Vendor ID
PRODUCT_ID = 0x5A5A  # Replace with your device's Product ID
ISO_ENDPOINT = 0x83  # Replace with your IN isochronous endpoint

dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)

if dev is None:
    print("Device not found!")
    exit()

# Send a custom control request
try:
    print("Sending vendor-specific setup request...")
    response = dev.ctrl_transfer(0xC0, 0x01, 0, 0, 64)
    print(f"Received Response: {response}")
except Exception as e:
    print(f"Control transfer failed: {e}")