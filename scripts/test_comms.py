import sys
import time
from omotion.Interface import MOTIONUart

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_comms.py

OW_CMD = 0xE2


OW_CMD_PING = 0x00  
OW_CMD_VERSION = 0x02
OW_CMD_ECHO = 0x03

myUart = MOTIONUart(vid=0x0483, pid=0x5750, baudrate=921600, timeout=5, desc="console", demo_mode=False, async_mode=False)
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