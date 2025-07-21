import threading
import usb.core
import usb.util
import time
import json
import queue
from omotion.MotionBulkCommand import MOTIONBulkCommand
from omotion.config import OW_CAMERA, OW_CAMERA_FSIN, OW_CMD, OW_CMD_HISTO_OFF, OW_CMD_HISTO_ON, OW_IMU, OW_IMU_ON, OW_IMU_OFF, OW_RESP

EP_SIZE = 512

class MOTIONComposite(MOTIONBulkCommand):
    def __init__(self, vid, pid, timeout=100, left_imu_queue=None, right_imu_queue=None, histo_queue=None):
        super().__init__(vid, pid, timeout)
        self.imu_interface = 2
        self.left_imu_ep = None
        self.right_imu_ep = None
        self.left_stop_event = threading.Event()
        self.right_stop_event = threading.Event()
        self.left_imu_thread = None
        self.right_imu_thread = None
        self.left_imu_queue = left_imu_queue or queue.Queue()
        self.right_imu_queue = right_imu_queue or queue.Queue()
        self.histo_interface = 1
        self.expected_frame_size = 4112
        self.histo_ep = None
        self.histo_thread = None
        self.histo_queue = histo_queue or queue.Queue()

    def connect(self):
        super().connect()  # Claims interface 0

        if self.left_dev is not None:
            pass

        if self.right_dev is not None:
            right_cfg = self.right_dev.get_active_configuration()
        
            # Claim HISTO Interface
            right_histo_intf = right_cfg[(self.histo_interface, 0)]
            usb.util.claim_interface(self.right_dev, self.histo_interface)
            for ep in right_histo_intf:
                if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN:
                    self.histo_ep = ep
                    break

            if not self.histo_ep:
                raise RuntimeError("HISTO IN endpoint not found on interface 1")
            
            # Claim IMU Interface
            right_imu_intf = right_cfg[(self.imu_interface, 0)]
            usb.util.claim_interface(self.right_dev, self.imu_interface)

            for ep in right_imu_intf:
                if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN:
                    self.right_imu_ep = ep
                    break

            if not self.right_imu_ep:
                raise RuntimeError("IMU IN endpoint not found on interface 2")
            
    def histo_thread_func(self):
        expected_size = self.expected_frame_size
        print(f"Reading HISTO data from EP 0x{self.histo_ep.bEndpointAddress:X}, Expected {expected_size} bytes per frame")
        try:
            while not self.right_stop_event.is_set():
                try:
                    data = self.right_dev.read(self.histo_ep.bEndpointAddress, expected_size, timeout=500)
                    if len(data) != expected_size:
                        print(f"[HISTO] Skipping incomplete frame ({len(data)} bytes)")
                        continue

                    self.histo_queue.put(bytes(data))

                except usb.core.USBError as e:
                    if e.errno != 110:
                        print(f"[HISTO] USB error: {e}")
                    time.sleep(0.01)
        finally:
            usb.util.release_interface(self.right_dev, self.histo_interface)
            usb.util.dispose_resources(self.right_dev)
            print("\nStopped HISTO read thread.")

    def start_histo_thread(self, expected_frame_size):
        if self.histo_thread and self.histo_thread.is_alive():
            print("HISTO thread already running.")
            return
        self.expected_frame_size = expected_frame_size  # Store for the thread to use
        self.right_stop_event.clear()
        self.histo_thread = threading.Thread(target=self.histo_thread_func, daemon=True)
        self.histo_thread.start()

    def stop_histo_thread(self):
        self.right_stop_event.set()
        if self.histo_thread:
            self.histo_thread.join()

    def start_histo_stream(self, camera_count=1, frame_size=4112):
        print(f'Camera Count {camera_count}')
        r = self.send_packet(
            packetType=OW_CMD,
            command=OW_CMD_HISTO_ON,
            addr=camera_count,
            reserved=0,
            data=b''
        )
        if r.packet_type == OW_RESP:
            print("HISTO Stream ON")
        else:
            print("HISTO ON ERROR")

    def stop_histo_stream(self):
        r = self.send_packet(
            packetType=OW_CMD,
            command=OW_CMD_HISTO_OFF,
            addr=0,
            reserved=0,
            data=b''
        )
        if r.packet_type == OW_RESP:
            print("HISTO Stream OFF")
            self.stop_histo_thread()
        else:
            print("HISTO OFF ERROR")
            
    def start_imu_stream(self):
        r = self.send_packet(
            packetType=OW_IMU,
            command=OW_IMU_ON,
            addr=0,
            reserved=0,
            data=b''
        )
        if r.packet_type == OW_RESP:
            print("IMU Stream ON")
        else:
            print("IMU ON ERROR")

    def stop_imu_stream(self):
        r = self.send_packet(
            packetType=OW_IMU,
            command=OW_IMU_OFF,
            addr=0,
            reserved=0,
            data=b''
        )
        if r.packet_type == OW_RESP:
            print("IMU Stream OFF")
        else:
            print("IMU OFF ERROR")
            
    def imu_thread_func(self):
        print(f"Reading Right IMU data from EP 0x{self.right_imu_ep.bEndpointAddress:X}")
        try:
            while not self.right_stop_event.is_set():
                try:
                    data = self.right_dev.read(self.right_imu_ep.bEndpointAddress, EP_SIZE, timeout=25)
                    json_bytes = bytes(data)
                    for line in json_bytes.splitlines():
                        try:
                            parsed = json.loads(line)
                            self.right_imu_queue.put(parsed)
                        except json.JSONDecodeError:
                            print("[IMU] Invalid JSON:", line)
                except usb.core.USBError as e:
                    if e.errno != 110:
                        print(f"[IMU] USB error: {e}")
                    time.sleep(0.01)
        finally:
            usb.util.release_interface(self.right_dev, self.imu_interface)
            usb.util.dispose_resources(self.right_dev)
            print("\nStopped IMU read thread.")

    def start_imu_thread(self):
        if self.right_imu_thread and self.right_imu_thread.is_alive():
            print("IMU thread already running.")
            return
        self.right_stop_event.clear()
        self.right_imu_thread = threading.Thread(target=self.imu_thread_func, daemon=True)
        self.right_imu_thread.start()

    def stop_imu_thread(self):
        self.right_stop_event.set()
        if self.right_imu_thread:
            self.right_imu_thread.join()

    def __enter__(self):
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_value, traceback):
        self.stop_imu_stream()
        self.stop_imu_thread()
        self.stop_histo_stream()