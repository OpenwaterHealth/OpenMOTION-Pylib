import logging

# Configure default logger (you can customize or configure externally)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Add a basic console handler if not already configured
if not logger.hasHandlers():
    ch = logging.StreamHandler()
    formatter = logging.Formatter('[%(levelname)s] %(name)s: %(message)s')
    ch.setFormatter(formatter)
    logger.addHandler(ch)

try:
    from PyQt6.QtCore import QObject, pyqtSignal
    PYQT_AVAILABLE = True
    logger.info("PyQt6 is available. SignalWrapper will emit real signals.")
except ImportError:
    PYQT_AVAILABLE = False
    logger.warning("PyQt6 is NOT available. SignalWrapper will use no-op fallbacks.")
    QObject = object

class SignalWrapper(QObject if PYQT_AVAILABLE else object):
    """
    A wrapper class for emitting PyQt signals if PyQt6 is available.
    If not available, provides no-op methods instead of signals.
    """
    if PYQT_AVAILABLE:
        signal_connect = pyqtSignal(str, str)
        signal_disconnect = pyqtSignal(str, str)
        signal_data_received = pyqtSignal(str, str)

        def __init__(self):
            super().__init__()
            logger.info("SignalWrapper initialized with real signals.")
    else:
        def __init__(self):
            self.signal_connect = self._noop
            self.signal_disconnect = self._noop
            self.signal_data_received = self._noop
            logger.info("SignalWrapper initialized with no-op signals.")

        def _noop(self, *args, **kwargs):
            logger.debug(f"Called no-op signal with args={args}, kwargs={kwargs}")
