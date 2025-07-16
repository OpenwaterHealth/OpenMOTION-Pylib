import sys
import time
import usb.core
import usb.util
import json
from omotion.Interface import MotionComposite
import threading

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_comms.py

# ---- Constants ----
VID = 0x0483
PID = 0x5A5A
# PID = 0x5750

OW_CMD = 0xE2


OW_CMD_PING = 0x00  
OW_CMD_VERSION = 0x02
OW_CMD_ECHO = 0x03

EP_IN = 0x83
EP_IN_HISTO = 0x82
EP_SIZE = 512  # TODO(fix to 1024)
TIMEOUT = 100  # milliseconds

stop_event = threading.Event()

def read_usb_stream(dev, endpoint=EP_IN, timeout=TIMEOUT):
    data = bytearray()
    while True:
        try:
            chunk = dev.read(endpoint, EP_SIZE, timeout=timeout)
            data.extend(chunk)
            # If packet is shorter than max size, it's the end
            if len(chunk) < EP_SIZE:
                break
        except usb.core.USBError as e:
            print(f"USB read error: {e}")
            break
    return data

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

def main_imu_data_stream():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("Device not found")
        return

    dev.set_configuration()
    usb.util.claim_interface(dev, 2)  # Interface #2 for IMU
    try:
        while True:
            json_str = read_usb_stream(dev)            
            if json_str:
                for line in json_str.splitlines():
                    try:
                        data = json.loads(line)
                        # print("Received JSON:", data)
                    except json.JSONDecodeError:
                        print(f"Invalid JSON: {line}")                
            else:
                print("No data received.")
                # 25ms update rate, so sleep accordingly or adjust as needed
            time.sleep(0.0125)
    except KeyboardInterrupt:
        print("\nStopped by user.")
        usb.util.release_interface(dev, 2)
        usb.util.dispose_resources(dev)

def main_histo_dummy_data_stream():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("Device not found")
        return

    dev.set_configuration()
    usb.util.claim_interface(dev, 1)  # Interface #1 for HISTO
    try:
        while True:
            json_str = read_usb_stream(dev, endpoint=EP_IN_HISTO)            
            if json_str:
                # print(json_str)
                str_length = str(json_str.__len__())
                if(str_length != 32801): print("String length" + str_length)
                print(json_str[0:4])
                with open("my_file.bin", "wb") as binary_file:
                    binary_file.write(json_str)                
                # for line in json_str.splitlines():
                #     try:
                #         data = json.loads(line)
                #         print("Received JSON:", data)
                #     except json.JSONDecodeError:
                #         print(f"Invalid JSON: {line}")                
            else:
                print("No data received.")
                # 25ms update rate, so sleep accordingly or adjust as needed
            time.sleep(0.0125)
    except KeyboardInterrupt:
        print("\nStopped by user.")
        usb.util.release_interface(dev, 1)
        usb.util.dispose_resources(dev)


# ---- Main ----
def main():
    myUart = MotionComposite(vid=VID, pid=PID, baudrate=921600, timeout=5, desc="sensor", demo_mode=False, async_mode=False)
    if myUart == None:
        print("Error establishing uart object")
        sys.exit(1)

    myUart.check_usb_status()
    if myUart.is_connected():
        print("MOTION MOTIONSensor connected.")
    else:
        print("MOTION MOTIONSensor NOT Connected.")

    echo_data = b"Hello VCP COMMS, we want to test the length past 64 bytes which is the max packet size for FS!"

    r = myUart.send_packet(id=None, packetType=OW_CMD, command=OW_CMD_ECHO, data=echo_data)
    myUart.clear_buffer()
    if r.data_len > 0:
        print(f"Received {r}")
        print(f"Echoed: {r.data.decode(errors='ignore')}")
    else:
        print("Uart command error")


def threaded_imu_stream(dev):
    usb.util.claim_interface(dev, 2)
    try:
        while not stop_event.is_set():
            json_str = read_usb_stream(dev, endpoint=EP_IN)
            if json_str:
                for line in json_str.splitlines():
                    try:
                        data = json.loads(line)
                        # print("[IMU] Received JSON:", data)
                    except json.JSONDecodeError:
                        print("[IMU] Invalid JSON:", line)
            time.sleep(0.0125)
    except Exception as e:
        print(f"[IMU] Exception: {e}")
    finally:
        usb.util.release_interface(dev, 2)

def threaded_histo_stream(dev):
    usb.util.claim_interface(dev, 1)
    try:
        while not stop_event.is_set():
            json_str = read_usb_stream(dev, endpoint=EP_IN_HISTO)

            str_length = str(json_str.__len__())
            if(str_length != "32801"): print("String length " + str_length)

            if json_str:
                # print("[HISTO] String length:", len(json_str))
                # print("[HISTO] First bytes:", json_str[0:4])
                with open("my_file.bin", "ab") as binary_file:
                    binary_file.write(json_str)
            time.sleep(0.0125)
    except Exception as e:
        print(f"[HISTO] Exception: {e}")
    finally:
        usb.util.release_interface(dev, 1)

def threaded_uart_work():
    try:
        myUart = MotionComposite(vid=VID, pid=PID, baudrate=921600, timeout=5, desc="sensor", demo_mode=False, async_mode=False)
        if myUart is None:
            print("Error establishing uart object")
            return

        myUart.check_usb_status()
        if myUart.is_connected():
            print("[UART] MOTION Sensor connected.")
        else:
            print("[UART] MOTION Sensor NOT Connected.")
            return

        echo_data = b"Hello VCP COMMS, we want to test the length past 64 bytes which is the max packet size for FS!"
        r = myUart.send_packet(id=None, packetType=OW_CMD, command=OW_CMD_ECHO, data=echo_data)
        myUart.clear_buffer()

        if r.data_len > 0:
            print(f"[UART] Received: {r}")
            print(f"[UART] Echoed: {r.data.decode(errors='ignore')}")
        else:
            print("[UART] Command error")
    except Exception as e:
        print(f"[UART] Exception: {e}")

def run_both_streams():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("Device not found")
        return

    dev.set_configuration()

    t1 = threading.Thread(target=threaded_imu_stream, args=(dev,), daemon=True)
    t2 = threading.Thread(target=threaded_histo_stream, args=(dev,), daemon=True)

    t1.start()
    t2.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Exiting...")
        usb.util.dispose_resources(dev)

def run_all_streams():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("Device not found")
        return

    dev.set_configuration()
    print("set config")
    t_imu = threading.Thread(target=threaded_imu_stream, args=(dev,), daemon=True)
    t_histo = threading.Thread(target=threaded_histo_stream, args=(dev,), daemon=True)
    t_uart = threading.Thread(target=threaded_uart_work, daemon=True)

    # t_imu.start()
    # t_histo.start()
    t_uart.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Exiting...")
        stop_event.set()
        time.sleep(0.1)
        usb.util.dispose_resources(dev)

if __name__ == "__main__":
    enumerate_and_print_interfaces(vid=VID, pid=PID)
    # main_imu_data_stream()
    # main_histo_dummy_data_stream()
    # main()
    # run_both_streams()
    # run_all_streams()