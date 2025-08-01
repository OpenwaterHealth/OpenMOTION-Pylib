# signal_wrapper.py
try:
    from PyQt6.QtCore import QObject, pyqtSignal
    PYQT_AVAILABLE = True
except ImportError:
    PYQT_AVAILABLE = False
    QObject = object

class SignalWrapper(QObject if PYQT_AVAILABLE else object):
    if PYQT_AVAILABLE:
        signal_connect = pyqtSignal(str, str)
        signal_disconnect = pyqtSignal(str, str)
        signal_data_received = pyqtSignal(str, str)

        def __init__(self):
            super().__init__()

    else:
        def __init__(self):
            self.signal_connect = self._noop
            self.signal_disconnect = self._noop
            self.signal_data_received = self._noop

        def _noop(self, *args, **kwargs):
            pass  # No-op fallback
