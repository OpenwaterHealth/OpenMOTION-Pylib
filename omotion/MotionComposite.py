# MotionComposite.py
import logging
import usb.core
import usb.util
import threading
from omotion.CommInterface import CommInterface
from omotion.StreamInterface import StreamInterface
from omotion.signal_wrapper import SignalWrapper
from omotion import _log_root
from omotion.connection_state import ConnectionState

logger = logging.getLogger(
    f"{_log_root}.MotionComposite" if _log_root else "MotionComposite"
)


# ===============================
# One Physical Composite Device
# ===============================
class MotionComposite(SignalWrapper):
    def __init__(self, dev, desc="COMPOSITE", async_mode=False):
        super().__init__()
        self.dev = dev
        self.desc = desc
        self.async_mode = async_mode
        self.running = False
        self.demo_mode = False
        self.state = ConnectionState.DISCONNECTED

        # Interfaces
        self.comm = CommInterface(
            dev, 0, desc=f"{desc}-COMM", async_mode=True
        )  # TODO: fix async mode in higher levels
        self.histo = StreamInterface(dev, 1, desc=f"{desc}-HISTO")
        self.imu = StreamInterface(dev, 2, desc=f"{desc}-IMU")
        self.comm.on_disconnect = self._handle_interface_disconnect

        self.packet_count = 0
        self.read_buffer = bytearray()

        self.stop_event = threading.Event()
        self.pause_event = threading.Event()

    def connect(self):
        self._set_state(ConnectionState.CONNECTING)
        self.dev.set_configuration()
        self.comm.claim()
        self.histo.claim()
        self.imu.claim()

        if self.comm.async_mode:
            self.comm.start_read_thread()

        self.running = True
        self._set_state(ConnectionState.CONNECTED)
        self.signal_connect.emit(self.desc, "composite_usb")
        logger.info(f"{self.desc}: Connected")

    def disconnect(self):
        if self.state == ConnectionState.DISCONNECTED:
            return

        # Build an ordered list of labeled teardown steps.  Every step runs
        # regardless of whether earlier ones fail — failures are logged with
        # enough context to diagnose without halting the rest of cleanup.
        steps = []

        # Stop the comm read thread first so no USB I/O races the releases below.
        # CommInterface._trigger_disconnect now dispatches on_disconnect to a
        # separate thread, so this join is safe to call from any context.
        if getattr(self.comm, "async_mode", False):
            steps.append(("stop comm read thread", self.comm.stop_read_thread))

        steps += [
            ("stop histo streaming",  self.histo.stop_streaming),
            ("stop imu streaming",    self.imu.stop_streaming),
            ("release comm",          self.comm.release),
            ("release histo",         self.histo.release),
            ("release imu",           self.imu.release),
            ("dispose usb resources", lambda: usb.util.dispose_resources(self.dev)),
        ]

        for label, step in steps:
            try:
                step()
            except Exception as e:
                logger.warning("%s: disconnect step '%s' failed: %s", self.desc, label, e)

        self.running = False
        self._set_state(ConnectionState.DISCONNECTED)
        try:
            self.signal_disconnect.emit(self.desc, "composite_usb")
        except RuntimeError as e:
            # The Qt C++ backing object can already be deleted when this fires
            # from a background disconnect thread during app shutdown.
            logger.debug("%s: signal_disconnect.emit skipped (object deleted): %s", self.desc, e)
        logger.info(f"{self.desc}: Disconnected")

    def is_connected(self) -> bool:
        """
        Check if the device is connected.
        """
        return self.state == ConnectionState.CONNECTED

    def check_usb_status(self):
        """
        Check if the device is connected.
        """
        return self.state == ConnectionState.CONNECTED

    def _handle_interface_disconnect(self, source, error):
        # This callback fires on a background thread.  The MotionComposite Qt
        # C++ object may already be deleted if app shutdown is racing GC, so
        # wrap the whole body to avoid crashing the background thread.
        try:
            if not self.running:
                return
            logger.warning(f"{self.desc}: Interface {source} disconnected: {error}")
            self._set_state(ConnectionState.ERROR, reason="usb_error")
            self.disconnect()
        except RuntimeError as e:
            logger.debug(
                "%s: _handle_interface_disconnect suppressed (object deleted): %s",
                self.desc, e,
            )

    def _set_state(self, new_state: ConnectionState, reason: str | None = None):
        if self.state == new_state:
            return
        prior = self.state
        self.state = new_state
        if reason:
            logger.info(
                "%s state %s -> %s (%s)", self.desc, prior.name, new_state.name, reason
            )
        else:
            logger.info("%s state %s -> %s", self.desc, prior.name, new_state.name)
