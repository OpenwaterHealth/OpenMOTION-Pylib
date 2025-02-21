import usb.core
import usb.util

# Vendor ID and Product ID of your USB UVC device
VENDOR_ID = 0x0483  # Replace with your device's Vendor ID
PRODUCT_ID = 0x5710  # Replace with your device's Product ID
PACKET_SIZE = 32 * 1024  # 32 KB

def find_uvc_device(vendor_id, product_id):
    # Find the USB device
    device = usb.core.find(idVendor=vendor_id, idProduct=product_id)
    if device is None:
        raise ValueError("Device not found")
    return device

def setup_device(device):
    # Detach kernel driver if necessary
    if device.is_kernel_driver_active(0):
        device.detach_kernel_driver(0)
    
    # Set configuration
    device.set_configuration()

    # Claim the interface
    usb.util.claim_interface(device, 0)

def read_uvc_data(device, endpoint_address):
    try:
        while True:
            # Read data from the endpoint
            data = device.read(endpoint_address, PACKET_SIZE, timeout=5000)
            print(f"Received {len(data)} bytes")
            # Process data as needed
    except usb.core.USBTimeoutError:
        print("Timeout occurred while reading data")
    except Exception as e:
        print(f"Error: {e}")

def main():
    try:
        device = find_uvc_device(VENDOR_ID, PRODUCT_ID)
        print("Device found")
        setup_device(device)
        
        # Replace with the correct endpoint address for your UVC device
        endpoint_address = 0x81  # Example: IN endpoint address
        
        print("Reading data...")
        read_uvc_data(device, endpoint_address)
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
