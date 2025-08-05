import logging
import usb.core
import usb.util

logger = logging.getLogger("USBInterfaceBase")
logger.setLevel(logging.INFO)

# =========================================
# Base Interface Class
# =========================================
class USBInterfaceBase:
    def __init__(self, dev, interface_index, desc="USBIF"):
        self.dev = dev
        self.interface_index = interface_index
        self.desc = desc
        self.ep_in = None
        self.ep_out = None

    def claim(self):
        usb.util.claim_interface(self.dev, self.interface_index)
        intf = self.dev.get_active_configuration()[(self.interface_index, 0)]
        self.ep_in = usb.util.find_descriptor(
            intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN
        )
        if not self.ep_in:
            raise RuntimeError(f"{self.desc}: No IN endpoint found")

    def release(self):
        try:
            usb.util.release_interface(self.dev, self.interface_index)
        except usb.core.USBError as e:
            logger.warning(f"{self.desc}: Release failed: {e}")