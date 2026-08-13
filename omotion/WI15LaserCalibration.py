"""Pure values and validation helpers for WI-00015 laser calibration."""

from dataclasses import dataclass
from enum import Enum
import math
from types import MappingProxyType
from typing import Iterable, Literal


SensorSide = Literal["left", "right"]


class ProcedureStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    PASSED = "passed"
    FAILED = "failed"
    FAILED_NCR = "failed_ncr"
    CANCELED = "canceled"


class FailureKind(str, Enum):
    SETUP = "setup"
    CONFIGURATION = "configuration"
    MEASUREMENT = "measurement"
    NCR = "ncr"
    CANCELED = "canceled"
    REPORT = "report"


@dataclass(frozen=True)
class EnergyMeasurement:
    n: int
    discarded: int
    mean_uj: float
    stdev_uj: float
    rate_hz: float
    min_uj: float
    max_uj: float
    duration_s: float


@dataclass(frozen=True)
class CriterionResult:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class SettingReadback:
    name: str
    requested: float
    actual: float


@dataclass(frozen=True)
class FinalSettingCheck:
    name: str
    requested: float
    actual: float
    absolute_difference: float
    percent_difference: float
    tolerance_percent: float
    passed: bool


@dataclass(frozen=True)
class DeviceIdentity:
    role: str
    serial: str | None
    firmware: str | None
    hardware_id: str | None
    fpga_firmware: str | None = None


@dataclass(frozen=True)
class OphirIdentity:
    meter_model: str | None
    meter_serial: str | None
    sensor_model: str | None
    sensor_serial: str | None
    calibration_due: str | None


@dataclass(frozen=True)
class TopologySnapshot:
    console_connected: bool
    left_connected: bool
    right_connected: bool

TARGET_ENERGY_UJ = 350
MIN_ACCEPTABLE_ENERGY_UJ = 300
MAX_ACCEPTABLE_ENERGY_UJ = 400
MIN_PULSE_COUNT_EXCLUSIVE = 25
MAX_STDEV_UJ_EXCLUSIVE = 40
MIN_RATE_HZ = 39
MAX_RATE_HZ = 41
CURRENT_STEP_MA = 50
CURRENT_FLOOR_MA = 2000
PULSE_WIDTH_STEP_US = 10
MAX_PULSE_WIDTH_US = 600
TEMPORARY_PULSE_WIDTH_LIMIT_US = 660

_CANONICAL_DEFAULT_USER_CONFIG = MappingProxyType({
    "TA_PULSE_WIDTH": 500,
    "TA_CURRENT_DRV": 5000,
    "SEED_CW_GAIN": 140,
    "EE_PULSE_WIDTH_UL": 550,
    "EE_RATE_LL": 23125,
    "EE_DRIVE_CL": 9999,
    "OPT_PULSE_WIDTH_UL": 550,
    "OPT_RATE_LL": 23125,
    "OPT_DRIVE_CL": 9999,
    "TEC_TRIP": 40,
})

# Compatibility export: callers may compare, copy, or even mutate this dict,
# but workflow runs always obtain a fresh copy of the private canonical data.
DEFAULT_USER_CONFIG = dict(_CANONICAL_DEFAULT_USER_CONFIG)


def default_user_configuration() -> dict[str, int]:
    """Return a fresh copy of the approved ten-key WI-00015 defaults."""
    return dict(_CANONICAL_DEFAULT_USER_CONFIG)


def _is_finite(value: object) -> bool:
    try:
        return math.isfinite(value)
    except TypeError:
        return False


def validate_energy_measurement(
    measurement: EnergyMeasurement,
) -> tuple[CriterionResult, ...]:
    """Return each WI-00015 measurement-quality criterion without raising."""
    pulse_count_is_valid = (
        _is_finite(measurement.n) and measurement.n > MIN_PULSE_COUNT_EXCLUSIVE
    )
    return (
        CriterionResult("n", _is_finite(measurement.n), "Pulse count must be finite."),
        CriterionResult(
            "pulse_count",
            pulse_count_is_valid,
            "Pulse count must be > 25.",
        ),
        CriterionResult(
            "discarded",
            _is_finite(measurement.discarded),
            "Discarded count must be finite.",
        ),
        CriterionResult(
            "mean_uj",
            _is_finite(measurement.mean_uj),
            "Mean energy must be finite.",
        ),
        CriterionResult(
            "stdev_uj",
            _is_finite(measurement.stdev_uj)
            and measurement.stdev_uj < MAX_STDEV_UJ_EXCLUSIVE,
            "Standard deviation must be finite and < 40 uJ.",
        ),
        CriterionResult(
            "rate_hz",
            _is_finite(measurement.rate_hz)
            and MIN_RATE_HZ <= measurement.rate_hz <= MAX_RATE_HZ,
            "Rate must be finite and between 39 and 41 Hz inclusive.",
        ),
        CriterionResult(
            "min_uj",
            _is_finite(measurement.min_uj),
            "Minimum energy must be finite.",
        ),
        CriterionResult(
            "max_uj",
            _is_finite(measurement.max_uj),
            "Maximum energy must be finite.",
        ),
        CriterionResult(
            "duration_s",
            _is_finite(measurement.duration_s),
            "Measurement duration must be finite.",
        ),
    )


def validate_exact_single_topology(
    topology: TopologySnapshot,
    side: SensorSide,
) -> CriterionResult:
    """Require a console and only the sensor declared for this run."""
    selected_present = (
        topology.left_connected and not topology.right_connected
        if side == "left"
        else topology.right_connected and not topology.left_connected
        if side == "right"
        else False
    )
    passed = topology.console_connected and selected_present
    return CriterionResult(
        "topology",
        passed,
        "Expected a console and exactly the declared sensor side.",
    )


def validate_serial(serial: str | None) -> CriterionResult:
    """Require a nonblank textual serial number for reportable identity."""
    passed = isinstance(serial, str) and bool(serial.strip())
    return CriterionResult("serial", passed, "Serial must be nonblank text.")


def percent_difference(requested: float, actual: float) -> float:
    """Return absolute percent difference, with defined behavior at zero."""
    if not (_is_finite(requested) and _is_finite(actual)):
        return math.nan
    if requested == 0:
        return 0.0 if actual == 0 else math.inf
    return abs(actual - requested) / abs(requested) * 100.0


def within_percent(requested: float, actual: float, tolerance_percent: float) -> bool:
    """Return whether an active setting stays within an inclusive tolerance."""
    return (
        _is_finite(tolerance_percent)
        and tolerance_percent >= 0
        and percent_difference(requested, actual) <= tolerance_percent
    )


def select_closest_valid_setting(
    candidates: Iterable[tuple[float, EnergyMeasurement]],
) -> tuple[float, EnergyMeasurement] | None:
    """Select the valid observed setting nearest the 350 uJ target.

    A lower requested setting deterministically wins equal-distance ties for
    both current and pulse-width tuning directions.
    """
    valid_candidates = [
        candidate
        for candidate in candidates
        if all(result.passed for result in validate_energy_measurement(candidate[1]))
    ]
    if not valid_candidates:
        return None
    return min(
        valid_candidates,
        key=lambda candidate: (
            abs(candidate[1].mean_uj - TARGET_ENERGY_UJ),
            candidate[0],
        ),
    )
