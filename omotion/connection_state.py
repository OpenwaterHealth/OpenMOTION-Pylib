from enum import Enum, auto


class ConnectionState(Enum):
    DISCONNECTED = auto()
    DISCOVERED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    ERROR = auto()
