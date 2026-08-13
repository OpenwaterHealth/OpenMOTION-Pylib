"""Production hardware boundaries for WI-00015 laser calibration.

The adapters in this module keep hardware lifetime and wire-format details out
of the procedure workflow.  Tests inject fakes; importing this module never
constructs an Ophir COM object or touches attached hardware.
"""

from __future__ import annotations

import math
import re
import statistics
import time
from typing import Callable, Mapping

from omotion.MotionConfig import MotionConfig
from omotion.MotionInterface import MotionInterface
from omotion.WI15LaserCalibration import (
    DeviceIdentity,
    EnergyMeasurement,
    OphirIdentity,
    SettingReadback,
    TopologySnapshot,
)
from omotion.WI15SingleSensorLaserCalibration import (
    OphirEvidenceApplicability,
    OphirSettingEvidence,
    PreflightSnapshot,
)
from omotion.laser import FpgaMap


def _default_interface_factory() -> MotionInterface:
    return MotionInterface()


def _default_ophir_com_factory():
    # pywin32 is an optional, Windows-only runtime dependency.  Keep the import
    # here so SDK import and all fake-driven tests work without COM installed.
    import win32com.client

    return win32com.client.Dispatch("OphirLMMeasurement.CoLMMeasurement")


class OphirEnergyMeter:
    """Channel-0 Ophir COM adapter with exact WI setting evidence."""

    _CHANNEL = 0

    def __init__(
        self,
        *,
        com_factory=_default_ophir_com_factory,
        duration_s: float = 0.65,
        poll_interval_s: float = 0.05,
        clock=time.monotonic,
        sleep=time.sleep,
    ):
        if duration_s <= 0 or poll_interval_s <= 0:
            raise ValueError("duration and poll interval must be positive")
        self._com_factory = com_factory
        self._duration_s = float(duration_s)
        self._poll_interval_s = float(poll_interval_s)
        self._clock = clock
        self._sleep = sleep
        self._com = None
        self._handle = None

    def _ensure_com(self):
        if self._com is not None:
            return self._com
        try:
            self._com = self._com_factory()
        except Exception as exc:
            raise RuntimeError(f"Ophir COM construction failed: {exc}") from exc
        if self._com is None:
            raise RuntimeError("Ophir COM construction failed: factory returned None")
        return self._com

    @staticmethod
    def _required_text(value) -> str:
        if value is None:
            raise ValueError
        text = str(value).strip()
        if not text:
            raise ValueError
        return text

    @staticmethod
    def _option_number(option, expected_unit: str | None = None) -> float | None:
        text = str(option).strip().lower().replace(" ", "")
        match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))([a-z%]*)", text)
        if match is None:
            return None
        unit = match.group(2)
        if expected_unit is not None and unit not in ("", expected_unit):
            return None
        return float(match.group(1))

    @staticmethod
    def _options_response(response, setting_name: str) -> tuple[int, list]:
        if not isinstance(response, (tuple, list)) or len(response) != 2:
            raise RuntimeError(f"Ophir {setting_name} getter returned malformed data")
        index, options = response
        try:
            options = list(options)
            index = int(index)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Ophir {setting_name} getter returned malformed data"
            ) from exc
        if not options or not 0 <= index < len(options):
            raise RuntimeError(
                f"Ophir {setting_name} getter returned invalid selection"
            )
        return index, options

    def _configure_setting(
        self,
        *,
        name: str,
        requested,
        getter_name: str,
        setter_name: str,
        matches,
        actual_value,
    ) -> OphirSettingEvidence:
        com = self._com
        _, options = self._options_response(
            getattr(com, getter_name)(self._handle, self._CHANNEL), name
        )
        selected = next(
            (i for i, option in enumerate(options) if matches(option)), None
        )
        if selected is None:
            raise RuntimeError(f"Required Ophir {name} option is unavailable")
        getattr(com, setter_name)(self._handle, self._CHANNEL, selected)
        readback_index, readback_options = self._options_response(
            getattr(com, getter_name)(self._handle, self._CHANNEL), name
        )
        readback_option = readback_options[readback_index]
        if not matches(readback_option):
            raise RuntimeError(f"Ophir {name} readback mismatch")
        return OphirSettingEvidence(
            name,
            requested,
            actual_value(readback_option),
            OphirEvidenceApplicability.APPLICABLE,
            True,
        )

    def preflight(self) -> tuple[OphirIdentity, tuple[OphirSettingEvidence, ...]]:
        com = self._ensure_com()
        serials = com.ScanUSB()
        if not serials:
            raise RuntimeError("No Ophir USB meters were found")
        try:
            handle = com.OpenUSBDevice(serials[0])
        except Exception as exc:
            raise RuntimeError(f"Ophir meter open failed: {exc}") from exc
        if (
            isinstance(handle, bool)
            or not isinstance(handle, (int, float))
            or handle <= 0
        ):
            raise RuntimeError("Ophir meter did not return a valid handle")
        self._handle = handle
        if not com.IsSensorExists(handle, self._CHANNEL):
            raise RuntimeError("Ophir channel 0 energy sensor is missing")

        try:
            device_info = com.GetDeviceInfo(handle)
            sensor_info = com.GetSensorInfo(handle, self._CHANNEL)
            if len(device_info) < 3 or len(sensor_info) < 3:
                raise ValueError
            meter_model = self._required_text(device_info[0])
            meter_serial = self._required_text(device_info[2])
            sensor_serial = self._required_text(sensor_info[0])
            sensor_model = self._required_text(sensor_info[2])
            device_due = self._required_text(com.GetDeviceCalibrationDueDate(handle))
            sensor_due = self._required_text(
                com.GetSensorCalibrationDueDate(handle, self._CHANNEL)
            )
        except Exception as exc:
            raise RuntimeError(
                "Ophir identity and calibration due information is incomplete"
            ) from exc

        evidence = (
            self._configure_setting(
                name="measurement_mode",
                requested="Energy",
                getter_name="GetMeasurementMode",
                setter_name="SetMeasurementMode",
                matches=lambda option: str(option).strip().lower() == "energy",
                actual_value=lambda option: "Energy",
            ),
            self._configure_setting(
                name="range_mj",
                requested=2.0,
                getter_name="GetRanges",
                setter_name="SetRange",
                matches=lambda option: self._option_number(option, "mj") == 2.0,
                actual_value=lambda option: 2.0,
            ),
            self._configure_setting(
                name="wavelength_nm",
                requested=795,
                getter_name="GetWavelengths",
                setter_name="SetWavelength",
                matches=lambda option: self._option_number(option, "nm") == 795.0,
                actual_value=lambda option: 795,
            ),
            self._configure_setting(
                name="pulse_length_ms",
                requested=1.0,
                getter_name="GetPulseLengths",
                setter_name="SetPulseLength",
                matches=lambda option: self._option_number(option, "ms") == 1.0,
                actual_value=lambda option: 1.0,
            ),
            self._configure_setting(
                name="threshold",
                requested="minimum_available",
                getter_name="GetThreshold",
                setter_name="SetThreshold",
                matches=lambda option: (
                    str(option).strip().lower() in ("min", "minimum")
                ),
                actual_value=lambda option: "minimum_available",
            ),
            OphirSettingEvidence(
                "display_averaging_s",
                3,
                None,
                OphirEvidenceApplicability.NOT_APPLICABLE,
                True,
            ),
            OphirSettingEvidence(
                "graph_mode",
                "Statistics",
                None,
                OphirEvidenceApplicability.NOT_APPLICABLE,
                True,
            ),
        )
        return (
            OphirIdentity(
                meter_model,
                meter_serial,
                sensor_model,
                sensor_serial,
                f"meter: {device_due}; sensor: {sensor_due}",
            ),
            evidence,
        )

    def measure(self) -> EnergyMeasurement:
        if self._com is None or self._handle is None:
            raise RuntimeError("Ophir preflight must pass before measurement")
        values_uj: list[float] = []
        timestamps_ms: list[float] = []
        discarded = 0
        started_at = self._clock()
        try:
            self._com.StartStream(self._handle, self._CHANNEL)
            while True:
                elapsed = self._clock() - started_at
                if elapsed >= self._duration_s:
                    break
                self._sleep(min(self._poll_interval_s, self._duration_s - elapsed))
                values, timestamps, statuses = self._com.GetData(
                    self._handle, self._CHANNEL
                )
                if not (len(values) == len(timestamps) == len(statuses)):
                    raise RuntimeError("Ophir stream returned misaligned sample arrays")
                for value, timestamp, status in zip(values, timestamps, statuses):
                    if status != 0:
                        discarded += 1
                        continue
                    values_uj.append(float(value) * 1e6)
                    timestamps_ms.append(float(timestamp))
        finally:
            self._com.StopStream(self._handle, self._CHANNEL)
        duration = self._clock() - started_at
        n = len(values_uj)
        mean = statistics.fmean(values_uj) if values_uj else math.nan
        stdev = statistics.stdev(values_uj) if n >= 2 else math.nan
        minimum = min(values_uj) if values_uj else math.nan
        maximum = max(values_uj) if values_uj else math.nan
        timestamp_delta_s = (
            (timestamps_ms[-1] - timestamps_ms[0]) / 1000.0 if n >= 2 else 0.0
        )
        rate = (n - 1) / timestamp_delta_s if timestamp_delta_s > 0 else math.nan
        return EnergyMeasurement(
            n=n,
            discarded=discarded,
            mean_uj=mean,
            stdev_uj=stdev,
            rate_hz=rate,
            min_uj=minimum,
            max_uj=maximum,
            duration_s=duration,
        )

    def close(self) -> None:
        if self._com is None:
            return
        first_error = None
        cleanups = []
        if self._handle is not None:
            cleanups.extend(
                (
                    lambda: self._com.StopStream(self._handle, self._CHANNEL),
                    self._com.StopAllStreams,
                    lambda: self._com.Close(self._handle),
                )
            )
        else:
            cleanups.append(self._com.StopAllStreams)
        cleanups.append(self._com.CloseAll)
        for cleanup in cleanups:
            try:
                cleanup()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        self._handle = None
        if first_error is not None:
            raise first_error


class FpgaRegisterIO:
    """Scaled friendly-name register access through MotionConsole I2C."""

    def __init__(self, console, *, fpga_map=None):
        self._console = console
        self._map = fpga_map if fpga_map is not None else FpgaMap()

    def _entry(self, name: str) -> tuple[dict, int, str, float]:
        entry = self._map.get_entry_by_friendly_name(name)
        if entry is None:
            raise KeyError(f"Unknown FPGA register: {name}")
        data_size = entry.get("data_size")
        if not isinstance(data_size, str) or not data_size.endswith("B"):
            raise ValueError(f"Invalid data size for {name}: {data_size!r}")
        bits = int(data_size[:-1])
        if bits <= 0 or bits % 8:
            raise ValueError(f"Invalid data size for {name}: {data_size!r}")
        byteorder = "big" if entry.get("isMsbFirst", False) else "little"
        scale = entry.get("scale")
        scale = 1.0 if scale is None else float(scale)
        if not math.isfinite(scale) or scale == 0:
            raise ValueError(f"Invalid scale for {name}: {scale!r}")
        return entry, bits // 8, byteorder, scale

    @staticmethod
    def _i2c_args(entry: dict) -> dict:
        return {
            "mux_index": entry["mux_idx"],
            "channel": entry["channel"],
            "device_addr": entry["i2c_addr"],
            "reg_addr": entry["start_address"],
        }

    def read(self, name: str) -> float:
        entry, width, byteorder, scale = self._entry(name)
        data, length = self._console.read_i2c_packet(
            **self._i2c_args(entry), read_len=width
        )
        if (
            not isinstance(data, (bytes, bytearray))
            or length != width
            or len(data) != width
        ):
            raise RuntimeError(f"Incomplete FPGA readback for {name}")
        raw = int.from_bytes(data, byteorder=byteorder, signed=False)
        return raw * scale

    def write(self, name: str, value: float) -> SettingReadback | None:
        entry, width, byteorder, scale = self._entry(name)
        requested = float(value)
        if not math.isfinite(requested):
            raise ValueError(f"FPGA value for {name} must be finite")
        raw = int(round(requested / scale))
        if not 0 <= raw < (1 << (width * 8)):
            raise ValueError(f"FPGA value for {name} is out of range")
        data = raw.to_bytes(width, byteorder=byteorder, signed=False)
        if not self._console.write_i2c_packet(**self._i2c_args(entry), data=data):
            return None
        return SettingReadback(name, requested, self.read(name))


class MotionLaserCalibrationBench:
    """One long-lived MotionInterface plus an Ophir energy-meter adapter."""

    def __init__(
        self,
        energy_meter,
        *,
        interface_factory: Callable[[], MotionInterface] = _default_interface_factory,
        fpga_map=None,
        wait_timeout: float = 10.0,
    ):
        self._meter = energy_meter
        self._interface = interface_factory()
        self._console = self._interface.console
        self._registers = FpgaRegisterIO(self._console, fpga_map=fpga_map)
        self._wait_timeout = float(wait_timeout)
        self._started = False

    def _ensure_started(self) -> None:
        if self._started:
            return
        self._interface.start(wait=False)
        self._started = True
        self._interface.wait_for_ready(
            console=True, sensors=1, timeout=self._wait_timeout
        )

    @staticmethod
    def _safe_call(device, method_name: str):
        try:
            return getattr(device, method_name)()
        except Exception:
            return None

    def _identity(self, role: str, device) -> DeviceIdentity:
        # Each field is independent: a failed firmware query must not replace a
        # successfully-read serial (especially not with exception text).
        return DeviceIdentity(
            role=role,
            serial=self._safe_call(device, "read_serial_number"),
            firmware=self._safe_call(device, "get_version"),
            hardware_id=self._safe_call(device, "get_hardware_id"),
        )

    def _console_responsive(self) -> bool:
        try:
            echoed, length = self._console.echo(b"WI15")
            return echoed == b"WI15" and length == 4
        except Exception:
            return False

    def preflight(self, side: str) -> PreflightSnapshot:
        if side not in ("left", "right"):
            raise ValueError("side must be 'left' or 'right'")
        self._ensure_started()
        topology = TopologySnapshot(
            console_connected=bool(self._console.is_connected()),
            left_connected=bool(self._interface.left.is_connected()),
            right_connected=bool(self._interface.right.is_connected()),
        )
        selected = self._interface.left if side == "left" else self._interface.right
        ophir_identity = None
        ophir_evidence = ()
        ophir_ready = False
        ophir_failure_reason = None
        try:
            ophir_identity, ophir_evidence = self._meter.preflight()
            ophir_evidence = tuple(ophir_evidence)
            ophir_ready = True
        except Exception as exc:
            ophir_failure_reason = str(exc) or exc.__class__.__name__
        return PreflightSnapshot(
            topology=topology,
            console_identity=self._identity("console", self._console),
            selected_sensor_identity=self._identity("sensor", selected),
            console_responsive=self._console_responsive(),
            ophir_identity=ophir_identity,
            ophir_ready=ophir_ready,
            ophir_setting_evidence=ophir_evidence,
            ophir_failure_reason=ophir_failure_reason,
        )

    def read_user_configuration(self) -> Mapping[str, float]:
        config = self._console.read_config()
        if not isinstance(config, MotionConfig) or not isinstance(
            config.json_data, dict
        ):
            raise RuntimeError("Complete MotionConfig readback was not returned")
        return dict(config.json_data)

    def write_user_configuration(
        self, configuration: Mapping[str, float]
    ) -> Mapping[str, float] | None:
        write_result = self._console.write_config(
            MotionConfig(json_data=dict(configuration))
        )
        if not isinstance(write_result, MotionConfig):
            return None
        try:
            return self.read_user_configuration()
        except Exception:
            return None

    def bring_up_laser_configuration(self) -> None:
        if not self._interface.apply_laser_power():
            raise RuntimeError("Laser configuration bring-up failed")

    def read_register(self, name: str) -> float:
        return self._registers.read(name)

    def write_register(self, name: str, value: float) -> SettingReadback | None:
        return self._registers.write(name, value)

    def read_trigger_rate_hz(self) -> float:
        response = self._console.get_trigger_json()
        if not isinstance(response, dict) or "TriggerFrequencyHz" not in response:
            raise RuntimeError("Trigger-frequency readback was unavailable")
        rate = float(response["TriggerFrequencyHz"])
        if not math.isfinite(rate):
            raise RuntimeError("Trigger-frequency readback was not finite")
        return rate

    def write_trigger_rate_hz(self, rate_hz: float) -> float | None:
        requested = float(rate_hz)
        try:
            current = self._console.get_trigger_json()
            if not isinstance(current, dict):
                return None
            updated = dict(current)
            updated["TriggerFrequencyHz"] = requested
            if not self._console.set_trigger_json(updated):
                return None
            actual = self.read_trigger_rate_hz()
        except Exception:
            return None
        return actual if actual == requested else None

    def measure_energy(self) -> EnergyMeasurement:
        rate_hz = self.read_trigger_rate_hz()
        if rate_hz != 40.0:
            raise RuntimeError("Trigger frequency must read back exactly 40 Hz")
        ta_pulse = self.read_register("TA_PULSE_WIDTH")
        ee_limit = self.read_register("EE_PULSE_WIDTH_UL")
        opt_limit = self.read_register("OPT_PULSE_WIDTH_UL")
        if not (
            all(math.isfinite(value) for value in (ta_pulse, ee_limit, opt_limit))
            and ta_pulse < ee_limit
            and ta_pulse < opt_limit
        ):
            raise RuntimeError(
                "TA pulse width must be finite and below both active safety limits"
            )
        try:
            if not self._console.start_trigger():
                raise RuntimeError("Console failed to start trigger")
            return self._meter.measure()
        finally:
            self.stop_trigger()

    def stop_trigger(self) -> None:
        if not self._console.stop_trigger():
            raise RuntimeError("Console failed to stop trigger")

    def close(self) -> None:
        first_error = None
        for cleanup in (
            self.stop_trigger,
            self._meter.close,
            self._interface.stop,
        ):
            try:
                cleanup()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error
