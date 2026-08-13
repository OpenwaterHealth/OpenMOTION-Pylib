from dataclasses import is_dataclass, replace
import math

import pytest

from omotion.WI15LaserCalibration import (
    CURRENT_FLOOR_MA,
    CURRENT_STEP_MA,
    CriterionResult,
    DEFAULT_USER_CONFIG,
    DeviceIdentity,
    EnergyMeasurement,
    FailureKind,
    MAX_ACCEPTABLE_ENERGY_UJ,
    MAX_PULSE_WIDTH_US,
    MAX_RATE_HZ,
    MAX_STDEV_UJ_EXCLUSIVE,
    MIN_ACCEPTABLE_ENERGY_UJ,
    MIN_PULSE_COUNT_EXCLUSIVE,
    MIN_RATE_HZ,
    OphirIdentity,
    PULSE_WIDTH_STEP_US,
    ProcedureStatus,
    SettingReadback,
    TARGET_ENERGY_UJ,
    TEMPORARY_PULSE_WIDTH_LIMIT_US,
    TopologySnapshot,
    percent_difference,
    select_closest_valid_setting,
    validate_energy_measurement,
    validate_exact_single_topology,
    validate_serial,
    within_percent,
)


def test_wi15_fixed_thresholds_and_default_user_configuration():
    """Changing any mandated WI-00015 threshold or default must be detected."""
    assert TARGET_ENERGY_UJ == 350
    assert (MIN_ACCEPTABLE_ENERGY_UJ, MAX_ACCEPTABLE_ENERGY_UJ) == (300, 400)
    assert MIN_PULSE_COUNT_EXCLUSIVE == 25
    assert MAX_STDEV_UJ_EXCLUSIVE == 40
    assert (MIN_RATE_HZ, MAX_RATE_HZ) == (39, 41)
    assert (CURRENT_STEP_MA, CURRENT_FLOOR_MA) == (50, 2000)
    assert (PULSE_WIDTH_STEP_US, MAX_PULSE_WIDTH_US) == (10, 600)
    assert TEMPORARY_PULSE_WIDTH_LIMIT_US == 660
    assert DEFAULT_USER_CONFIG == {
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
    }
    assert isinstance(DEFAULT_USER_CONFIG["TEC_TRIP"], int)


def test_domain_records_are_frozen_and_status_values_are_stable():
    """Mutable procedure evidence or changed terminal values breaks reports."""
    records = (
        EnergyMeasurement(26, 1, 350.0, 10.0, 40.0, 330.0, 370.0, 0.65),
        CriterionResult("pulse_count", True, "> 25"),
        SettingReadback("TA_CURRENT_DRV", 5000.0, 4990.0),
        DeviceIdentity("console", "C-1", "1.2", "H-1"),
        OphirIdentity("meter", "M-1", "sensor", "S-1", "2027-01-01"),
        TopologySnapshot(True, True, False),
    )
    assert all(is_dataclass(record) and record.__dataclass_params__.frozen for record in records)
    assert [status.value for status in ProcedureStatus] == [
        "in_progress",
        "passed",
        "failed",
        "failed_ncr",
        "canceled",
    ]
    assert [kind.value for kind in FailureKind] == [
        "setup",
        "configuration",
        "measurement",
        "ncr",
        "canceled",
        "report",
    ]


def _valid_measurement(**changes):
    return replace(
        EnergyMeasurement(26, 0, 350.0, 10.0, 40.0, 330.0, 370.0, 0.65),
        **changes,
    )


def _criteria(measurement):
    return {result.name: result for result in validate_energy_measurement(measurement)}


def test_energy_measurement_requires_strictly_more_than_25_pulses():
    """Changing the count gate to >= 25 would admit an invalid observation."""
    at_25 = _criteria(_valid_measurement(n=25))["pulse_count"]
    at_26 = _criteria(_valid_measurement(n=26))["pulse_count"]
    assert not at_25.passed
    assert at_25.detail == "Pulse count must be > 25."
    assert at_26.passed


def test_energy_measurement_requires_standard_deviation_below_40_uj():
    """Changing the deviation gate to <= 40 would admit a boundary failure."""
    assert _criteria(_valid_measurement(stdev_uj=39.999))["stdev_uj"].passed
    assert not _criteria(_valid_measurement(stdev_uj=40.0))["stdev_uj"].passed


@pytest.mark.parametrize(
    ("rate_hz", "passed"),
    [(38.999, False), (39.0, True), (41.0, True), (41.001, False)],
)
def test_energy_measurement_accepts_only_the_inclusive_39_to_41_hz_rate(rate_hz, passed):
    """Changing either rate boundary would accept or reject a WI boundary value."""
    assert _criteria(_valid_measurement(rate_hz=rate_hz))["rate_hz"].passed is passed


@pytest.mark.parametrize(
    "field_name",
    [
        "n",
        "discarded",
        "mean_uj",
        "stdev_uj",
        "rate_hz",
        "min_uj",
        "max_uj",
        "duration_s",
    ],
)
@pytest.mark.parametrize("non_finite", [math.nan, math.inf, -math.inf])
def test_energy_measurement_rejects_each_non_finite_statistic(field_name, non_finite):
    """Removing finite checks would allow invalid raw Ophir statistics into tuning."""
    assert not _criteria(_valid_measurement(**{field_name: non_finite}))[field_name].passed


@pytest.mark.parametrize(
    ("topology", "side", "passed"),
    [
        (TopologySnapshot(True, True, False), "left", True),
        (TopologySnapshot(True, False, True), "right", True),
        (TopologySnapshot(True, False, False), "left", False),
        (TopologySnapshot(True, True, True), "left", False),
        (TopologySnapshot(True, False, True), "left", False),
    ],
)
def test_exact_single_topology_requires_only_the_declared_side(topology, side, passed):
    """Ignoring absent/extra/wrong sensors would allow an ambiguous procedure run."""
    assert validate_exact_single_topology(topology, side).passed is passed


@pytest.mark.parametrize(
    ("serial", "passed"),
    [(None, False), ("", False), ("  \t", False), (" SN-123 ", True)],
)
def test_serial_validation_requires_nonblank_text(serial, passed):
    """Accepting blank identity text would make the calibration evidence unusable."""
    assert validate_serial(serial).passed is passed


def test_percent_difference_handles_a_zero_requested_setting_without_raising():
    """A zero requested setting must have defined comparison behavior."""
    assert percent_difference(100.0, 102.0) == 2.0
    assert percent_difference(0.0, 0.0) == 0.0
    assert math.isinf(percent_difference(0.0, 1.0))


@pytest.mark.parametrize(
    ("actual", "passed"),
    [(98.0, True), (102.0, True), (97.999, False), (102.001, False)],
)
def test_within_percent_includes_exact_plus_or_minus_two_percent(actual, passed):
    """Changing the tolerance boundary would reject valid readbacks or admit bad ones."""
    assert within_percent(100.0, actual, 2.0) is passed


def test_closest_valid_setting_returns_none_without_a_valid_candidate():
    """Using an invalid measurement for tuning would bypass the quality gates."""
    assert select_closest_valid_setting([]) is None
    assert select_closest_valid_setting([(5000, _valid_measurement(n=25))]) is None


def test_closest_valid_setting_minimizes_distance_from_350_uj():
    """Choosing the first or last candidate would miss the closest measured setting."""
    candidates = [
        (5000, _valid_measurement(mean_uj=365.0)),
        (4950, _valid_measurement(mean_uj=347.0)),
        (4900, _valid_measurement(mean_uj=330.0)),
    ]
    assert select_closest_valid_setting(candidates) == candidates[1]


@pytest.mark.parametrize(
    "candidates, expected_index",
    [
        (
            [
                (5000, _valid_measurement(mean_uj=360.0)),
                (4950, _valid_measurement(mean_uj=340.0)),
            ],
            1,
        ),
        (
            [
                (500, _valid_measurement(mean_uj=340.0)),
                (510, _valid_measurement(mean_uj=360.0)),
            ],
            0,
        ),
    ],
)
def test_closest_valid_setting_breaks_distance_ties_with_lower_setting(candidates, expected_index):
    """Unstable ties could reapply a higher current or wider pulse than necessary."""
    assert select_closest_valid_setting(candidates) == candidates[expected_index]
