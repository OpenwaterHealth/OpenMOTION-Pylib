import usb1

# Parameters
VENDOR_ID = 0x0483  # Replace with your device's Vendor ID
PRODUCT_ID = 0x5A5A  # Replace with your device's Product ID
ISO_ENDPOINT = 0x81  # Replace with your IN isochronous endpoint

interface = 0
alt_setting = 0
packet_size = 1024  # or actual wMaxPacketSize
num_packets = 8     # Number of packets in this transfer
endpoint_address = ISO_ENDPOINT  # Replace with your IN endpoint address

context = usb1.USBContext()
context.setDebug(3)
handle = context.openByVendorIDAndProductID(VENDOR_ID, PRODUCT_ID,
                                            skip_on_error=True)

if handle is None:
    raise ValueError("Device not found")
print("Device found")

# print out a bunch of information about the device
print(f"Device: {handle}")
print(f"Device Descriptor: {handle.getDevice()}")
print(f"Configuration: {handle.getConfiguration()}")
print(f"Serial: {handle.getSerialNumber()}")
# print(f"Speed: {handle.getBusSpeed()}")
# print(f"Port Number: {handle.getPortNumber()}")
# print(f"Max Packet Size: {handle.getMaxPacketSize(0)}")
# print(f"Max ISO Packet Size: {handle.getMaxISOPacketSize(ISO_ENDPOINT)}")

# Claim interface and set alt setting
handle.claimInterface(interface)
# handle.setInterfaceAltSetting(interface, alt_setting)

#print what kind of object handle is
print(f"Handle type: {type(handle)}")

# Send a custom control request
print("Sending vendor-specific setup request...")
handle.controlWrite(0x20, 0x01, 0, 0, [64], timeout=1000)


# Set up isochronous transfer
print("Reading Isochronous Data...")
transfer = handle.getTransfer(1024)
transfer.setIsochronous(endpoint = ISO_ENDPOINT,
                        buffer_or_len=1024)

# set transfer callback to function that just prints out the response
transfer.setCallback(lambda transfer: print(f"Transfer status: {transfer.getStatus()}"))
transfer.submit()

# Wait for transfer completion
# transfer.wait()  # Can also pass timeout here
while(transfer.getStatus()==0):
    print("Waiting for transfer to complete...")

# Access data from individual packets
# iso_packets = transfer.getISOCHRONOUSPacketStatuses()
raw_data = transfer.getBuffer()

for i, packet in enumerate(iso_packets):
    print(f"Packet {i}: status={packet.status}, actual_length={packet.actual_length}")
    data = raw_data[i*packet_size:(i+1)*packet_size]
    print(data)

handle.releaseInterface(interface)
