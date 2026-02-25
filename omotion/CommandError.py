from typing import Optional

class CommandError(RuntimeError):
    """
    Raised when the hardware returns a non-OK response (NAK, BAD_CRC, etc.)
    or when the response payload cannot be decoded.
    """

    def __init__(self, message: str, response: Optional[any] = None) -> None:
        super().__init__(message)
        self.response = response