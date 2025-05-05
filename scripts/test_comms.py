import sys
import time
import usb.core
import usb.util
from omotion.Interface import MOTIONUart

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_comms.py

# ---- Constants ----
VID = 0x0483
PID = 0x5750

OW_CMD = 0xE2


OW_CMD_PING = 0x00  
OW_CMD_VERSION = 0x02
OW_CMD_ECHO = 0x03


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

# ---- Main ----
def main():
    myUart = MOTIONUart(vid=VID, pid=PID, baudrate=921600, timeout=5, desc="console", demo_mode=False, async_mode=False)
    if myUart == None:
        print("Error establishing uart object")
        sys.exit(1)

    myUart.check_usb_status()
    if myUart.is_connected():
        print("MOTION MOTIONSensor connected.")
    else:
        print("MOTION MOTIONSensor NOT Connected.")

    echo_data = b"Hello VCP COMMS!"

    r = myUart.send_packet(id=None, packetType=OW_CMD, command=OW_CMD_ECHO, data=echo_data)
    myUart.clear_buffer()
    if r.data_len > 0:
        print(f"Received {r}")
        print(f"Echoed: {r.data.decode(errors='ignore')}")
    else:
        print("Uart command error")


if __name__ == "__main__":
    # enumerate_and_print_interfaces(vid=VID, pid=PID)
    main()