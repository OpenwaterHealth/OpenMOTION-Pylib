import usb.core
import usb.util
import time
import threading
import queue
import logging

log = logging.getLogger("BulkBase")
logging.basicConfig(level=logging.INFO)


class MOTIONBulkBase:
    def __init__(self, vid, pid, timeout=100):
        self.vid = vid
        self.pid = pid
        self.timeout = timeout
        self.devices = None
        self.left_dev = None
        self.right_dev = None
        self.interface = 0
        self.ep_in = None
        self.ep_out = None
        self.response_queues = {}
        self.response_lock = threading.Lock()

    def connect(self):
        self.devices = usb.core.find(find_all=True, idVendor=self.vid, idProduct=self.pid)
        if not self.devices:
            raise ValueError(f"Device VID=0x{self.vid:X}, PID=0x{self.pid:X} not found.")

        if self.devices is None:
            raise ValueError('Device not found')
        for dev in self.devices:
            try:
                print(f"Bus: {dev.bus}")
                print(f"Address: {dev.address}")

                # For port path, use _ctx.backend
                port_numbers = dev.port_numbers
                if port_numbers[-1] == 2:
                    print("Left side")
                    self.left_dev = dev
                elif port_numbers[-1] == 3:
                    print("Right side")
                    self.right_dev = dev

            except Exception as e:
                print(f'exception {e}')

        self.right_dev.set_configuration()
        cfg = self.right_dev.get_active_configuration()
        intf = cfg[(self.interface, 0)]

        usb.util.claim_interface(self.right_dev, self.interface)
        self.ep_out = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
        )
        self.ep_in = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN
        )

        if not self.ep_out or not self.ep_in:
            raise RuntimeError("Bulk IN/OUT endpoints not found.")

        log.info("Connected to Interface #%d, EP_IN=0x%02X, EP_OUT=0x%02X",
                 self.interface, self.ep_in.bEndpointAddress, self.ep_out.bEndpointAddress)

    def disconnect(self):
        if self.left_dev:
            usb.util.release_interface(self.left_dev, self.interface)
            usb.util.dispose_resources(self.left_dev)
            self.left_dev = None
        if self.right_dev:
            usb.util.release_interface(self.right_dev, self.interface)
            usb.util.dispose_resources(self.right_dev)
            self.right_dev = None

    def send(self, data: bytes):
        self.right_dev.write(self.ep_out.bEndpointAddress, data, timeout=self.timeout)

    def receive(self, length=512):
        return self.right_dev.read(self.ep_in.bEndpointAddress, length, timeout=self.timeout)
