import threading
import usb.core
import time
import json
import queue
from omotion.MotionBulkCommand import MOTIONBulkCommand
from omotion.config import OW_IMU, OW_IMU_ON, OW_IMU_OFF

EP_SIZE = 512

class MotionComposite(MOTIONBulkCommand):
    def __init__(self, vid, pid, timeout=100, imu_queue=None):
        super().__init__(vid, pid, timeout)
        self.imu_interface = 2
        self.imu_ep = None
        self.stop_event = threading.Event()
        self.imu_thread = None
        self.imu_queue = imu_queue or queue.Queue()

    def connect(self):
        super().connect()  # Claims interface 0

        cfg = self.dev.get_active_configuration()
        imu_intf = cfg[(self.imu_interface, 0)]
        usb.util.claim_interface(self.dev, self.imu_interface)

        for ep in imu_intf:
            if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN:
                self.imu_ep = ep
                break

        if not self.imu_ep:
            raise RuntimeError("IMU IN endpoint not found on interface 2")

    def start_imu_stream(self):
        self.send_packet(
            packetType=OW_IMU,
            command=OW_IMU_ON,
            addr=0,
            reserved=0,
            data=b''
        )

    def stop_imu_stream(self):
        self.send_packet(
            packetType=OW_IMU,
            command=OW_IMU_OFF,
            addr=0,
            reserved=0,
            data=b''
        )
    
    def imu_thread_func(self):
        print(f"Reading IMU data from EP 0x{self.imu_ep.bEndpointAddress:X}")
        try:
            while not self.stop_event.is_set():
                try:
                    data = self.dev.read(self.imu_ep.bEndpointAddress, EP_SIZE, timeout=5)
                    json_bytes = bytes(data)
                    for line in json_bytes.splitlines():
                        try:
                            parsed = json.loads(line)
                            self.imu_queue.put(parsed)
                        except json.JSONDecodeError:
                            print("[IMU] Invalid JSON:", line)
                except usb.core.USBError as e:
                    if e.errno != 110:
                        print(f"[IMU] USB error: {e}")
                    time.sleep(0.01)
        finally:
            usb.util.release_interface(self.dev, self.imu_interface)
            usb.util.dispose_resources(self.dev)
            print("\nStopped IMU read thread.")

    def start_imu_thread(self):
        if self.imu_thread and self.imu_thread.is_alive():
            print("IMU thread already running.")
            return
        self.stop_event.clear()
        self.imu_thread = threading.Thread(target=self.imu_thread_func, daemon=True)
        self.imu_thread.start()

    def stop_imu_thread(self):
        self.stop_event.set()
        if self.imu_thread:
            self.imu_thread.join()

    def __enter__(self):
        self.connect()
        self.start_imu_stream()
        self.start_imu_thread()
        return self
    
    def __exit__(self, exc_type, exc_value, traceback):
        self.stop_imu_stream()
        self.stop_imu_thread()