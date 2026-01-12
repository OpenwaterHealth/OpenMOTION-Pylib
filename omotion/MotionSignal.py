import logging
from omotion import _log_root

# Set up logging
logger = logging.getLogger(f"{_log_root}.Signal" if _log_root else "Signal")
logger.setLevel(logging.INFO)  # or INFO depending on what you want to see

class MOTIONSignal:
    def __init__(self):
        # Initialize a list to store connected slots (callback functions)
        self._slots = []

    def connect(self, slot):
        """
        Connect a slot (callback function) to the signal.

        Args:
            slot (callable): A callable to be invoked when the signal is emitted.
        """
        if callable(slot) and slot not in self._slots:
            self._slots.append(slot)

    def disconnect(self, slot):
        """
        Disconnect a slot (callback function) from the signal.

        Args:
            slot (callable): The callable to disconnect.
        """
        if slot in self._slots:
            self._slots.remove(slot)

    def emit(self, *args, **kwargs):
        """
        Emit the signal, invoking all connected slots.

        Args:
            *args: Positional arguments to pass to the connected slots.
            **kwargs: Keyword arguments to pass to the connected slots.
        """
        for slot in self._slots:
            try:
                slot(*args, **kwargs)
            except Exception as e:
                logger.error("Signal emit error in slot %s: %s", slot, e)

