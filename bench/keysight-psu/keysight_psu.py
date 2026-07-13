"""
SCPI/USBTMC driver for the Keysight E36300-series triple-output DC power
supply (E36311A / E36312A / E36313A) via PyVISA.

Requires a VISA backend that can actually open the instrument's USBTMC
interface — either PyVISA-py with WinUSB bound to the USB interface (e.g.
via Zadig, same as this repo's sensor boards), or a vendor VISA runtime
(NI-VISA / Keysight IO Libraries Suite). Neither is installed by default;
`connect()` raises a clear `PSUError` if no USB SCPI resource is visible.
"""

import logging
from dataclasses import dataclass
from typing import Any, List, Optional

import pyvisa

logger = logging.getLogger("bench.KeysightPSU")

CHANNELS = (1, 2, 3)

KEYSIGHT_USB_VID = "0x2A8D"
_KEYSIGHT_VID_FIELDS = (KEYSIGHT_USB_VID.upper(), str(int(KEYSIGHT_USB_VID, 16)))

# (max volts, max amps) per channel, from the Keysight E36300-series datasheet.
CHANNEL_RANGES = {
    "E36311A": {1: (6.0, 5.0), 2: (25.0, 1.0), 3: (25.0, 1.0)},
    "E36312A": {1: (6.0, 5.0), 2: (25.0, 1.0), 3: (25.0, 1.0)},
    "E36313A": {1: (6.0, 10.0), 2: (25.0, 2.0), 3: (25.0, 2.0)},
}


class PSUError(RuntimeError):
    """Raised for SCPI errors reported by the instrument, or an unrecognised model."""


@dataclass(frozen=True)
class Identity:
    manufacturer: str
    model: str
    serial: str
    firmware: str


@dataclass(frozen=True)
class ChannelReading:
    voltage: float
    current: float


def _default_resource_manager() -> pyvisa.ResourceManager:
    """Prefer a real VISA runtime (NI-VISA / Keysight IO Libraries Suite) if one is
    installed; fall back to the pure-Python pyvisa-py backend (works with a plain
    WinUSB binding, e.g. via Zadig) when no vendor VISA library is found."""
    try:
        return pyvisa.ResourceManager()
    except Exception:
        return pyvisa.ResourceManager("@py")


def list_available_resources(resource_manager: Optional[pyvisa.ResourceManager] = None) -> List[str]:
    rm = resource_manager or _default_resource_manager()
    return [r for r in rm.list_resources() if r.upper().startswith("USB")]


def _is_keysight_resource(resource: str) -> bool:
    parts = resource.split("::")
    return len(parts) > 1 and parts[1].upper() in _KEYSIGHT_VID_FIELDS


def _discover_resource(rm: pyvisa.ResourceManager) -> str:
    resources = list_available_resources(rm)
    if not resources:
        raise PSUError(
            "No USB SCPI instruments visible to PyVISA. The PSU's USBTMC interface "
            "needs a driver bound before it can be opened — install the Keysight IO "
            "Libraries Suite, or bind WinUSB to the interface with Zadig (same as "
            "this repo's sensor boards) so the pyvisa-py backend can reach it."
        )
    if len(resources) == 1:
        return resources[0]
    # Multiple USB SCPI instruments on the bench — narrow to Keysight's VID before
    # giving up, since other lab gear (e.g. a Thorlabs USB488 instrument) can share
    # the bus without being what the caller meant by "the PSU".
    keysight = [r for r in resources if _is_keysight_resource(r)]
    if len(keysight) == 1:
        return keysight[0]
    if keysight:
        raise PSUError(f"Multiple Keysight USB SCPI instruments found; pass resource_name explicitly: {keysight}")
    raise PSUError(f"No Keysight USB SCPI instrument found; pass resource_name explicitly: {resources}")


class KeysightE36300:
    """Driver for one Keysight E36300-series supply. Build via `connect()`."""

    def __init__(self, resource: Any) -> None:
        self._inst = resource
        self._inst.read_termination = "\n"
        self._inst.write_termination = "\n"
        self.identity = self._read_identity()
        if self.identity.model not in CHANNEL_RANGES:
            raise PSUError(f"Unsupported model {self.identity.model!r}; expected one of {sorted(CHANNEL_RANGES)}")
        self.ranges = CHANNEL_RANGES[self.identity.model]

    @classmethod
    def connect(
        cls,
        resource_name: Optional[str] = None,
        *,
        resource_manager: Optional[pyvisa.ResourceManager] = None,
        timeout_ms: int = 5000,
    ) -> "KeysightE36300":
        rm = resource_manager or _default_resource_manager()
        name = resource_name or _discover_resource(rm)
        inst = rm.open_resource(name)
        inst.timeout = timeout_ms
        psu = cls(inst)
        logger.info("Connected to %s (%s)", psu.identity.model, name)
        return psu

    def close(self) -> None:
        self._inst.close()

    def __enter__(self) -> "KeysightE36300":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"KeysightE36300({self.identity.model}, serial={self.identity.serial!r})"

    # --- low-level ---

    def _write(self, cmd: str) -> None:
        self._inst.write(cmd)
        self._raise_if_error(cmd)

    def _query(self, cmd: str) -> str:
        return self._inst.query(cmd).strip()

    def _raise_if_error(self, context: str) -> None:
        err = self._query("SYST:ERR?")
        code = err.split(",", 1)[0].strip()
        if code not in ("0", "+0"):
            raise PSUError(f"SCPI error after {context!r}: {err}")

    def _read_identity(self) -> Identity:
        raw = self._query("*IDN?")
        parts = [p.strip() for p in raw.split(",", 3)]
        if len(parts) != 4:
            raise PSUError(f"Unexpected *IDN? response: {raw!r}")
        manufacturer, model, serial, firmware = parts
        return Identity(manufacturer=manufacturer, model=model, serial=serial, firmware=firmware)

    def _validate_channel(self, channel: int) -> None:
        if channel not in CHANNELS:
            raise ValueError(f"channel must be one of {CHANNELS} (got {channel})")

    def _validate_voltage(self, channel: int, volts: float) -> None:
        max_v, _ = self.ranges[channel]
        if not (0.0 <= volts <= max_v):
            raise ValueError(f"channel {channel} voltage must be within [0, {max_v}] V (got {volts})")

    def _validate_current(self, channel: int, amps: float) -> None:
        _, max_i = self.ranges[channel]
        if not (0.0 <= amps <= max_i):
            raise ValueError(f"channel {channel} current limit must be within [0, {max_i}] A (got {amps})")

    # --- identity / global state ---

    def identify(self) -> Identity:
        return self.identity

    def reset(self) -> None:
        """`*RST` — resets ALL channels to their power-on default (output off, 0 V)."""
        self._write("*RST")

    def clear_status(self) -> None:
        self._write("*CLS")

    def get_errors(self) -> List[str]:
        """Drains and returns the SCPI error queue (oldest first)."""
        errors = []
        while True:
            err = self._query("SYST:ERR?")
            code = err.split(",", 1)[0].strip()
            if code in ("0", "+0"):
                break
            errors.append(err)
        return errors

    def set_remote(self, remote: bool) -> None:
        self._write("SYST:REM" if remote else "SYST:LOC")

    # --- per-channel control ---

    def set_voltage(self, channel: int, volts: float) -> None:
        self._validate_channel(channel)
        self._validate_voltage(channel, volts)
        self._write(f"VOLT {volts:.4f}, (@{channel})")

    def get_voltage_setpoint(self, channel: int) -> float:
        self._validate_channel(channel)
        return float(self._query(f"VOLT? (@{channel})"))

    def set_current_limit(self, channel: int, amps: float) -> None:
        self._validate_channel(channel)
        self._validate_current(channel, amps)
        self._write(f"CURR {amps:.4f}, (@{channel})")

    def get_current_limit(self, channel: int) -> float:
        self._validate_channel(channel)
        return float(self._query(f"CURR? (@{channel})"))

    def set_output(self, channel: int, enabled: bool) -> None:
        self._validate_channel(channel)
        self._write(f"OUTP {'ON' if enabled else 'OFF'}, (@{channel})")

    def get_output(self, channel: int) -> bool:
        self._validate_channel(channel)
        return self._query(f"OUTP? (@{channel})").strip() in ("1", "ON")

    def measure(self, channel: int) -> ChannelReading:
        self._validate_channel(channel)
        voltage = float(self._query(f"MEAS:VOLT? (@{channel})"))
        current = float(self._query(f"MEAS:CURR? (@{channel})"))
        return ChannelReading(voltage=voltage, current=current)

    def measure_voltage(self, channel: int) -> float:
        self._validate_channel(channel)
        return float(self._query(f"MEAS:VOLT? (@{channel})"))

    def measure_current(self, channel: int) -> float:
        self._validate_channel(channel)
        return float(self._query(f"MEAS:CURR? (@{channel})"))

    # --- overvoltage protection ---

    def set_ovp(self, channel: int, volts: float) -> None:
        self._validate_channel(channel)
        if volts < 0:
            raise ValueError(f"OVP level must be >= 0 V (got {volts})")
        self._write(f"VOLT:PROT {volts:.4f}, (@{channel})")

    def get_ovp(self, channel: int) -> float:
        self._validate_channel(channel)
        return float(self._query(f"VOLT:PROT? (@{channel})"))

    def set_ovp_enabled(self, channel: int, enabled: bool) -> None:
        self._validate_channel(channel)
        self._write(f"VOLT:PROT:STAT {'ON' if enabled else 'OFF'}, (@{channel})")

    def ovp_tripped(self, channel: int) -> bool:
        self._validate_channel(channel)
        return self._query(f"VOLT:PROT:TRIP? (@{channel})").strip() in ("1", "ON")

    def clear_protection(self, channel: int) -> None:
        self._validate_channel(channel)
        self._write(f"OUTP:PROT:CLE (@{channel})")
