"""
Hardware-in-the-loop tests for the Keysight E36300-series bench supply
(bench equipment, not OpenMOTION hardware — see keysight_psu.py).

Safety: channel 1 is free to command. Channels 2 and 3 may have a DUT
attached, so only read-only queries (measure/get_*) ever touch them here —
no test sets voltage/current/output/OVP on channel 2 or 3. Do not add one
without confirming the DUT is disconnected or the change is safe.

Skips the whole module if no PSU is reachable (e.g. USBTMC driver not yet
bound — see keysight_psu.py:_discover_resource for what's needed).
"""

import pytest

from keysight_psu import KeysightE36300

pytestmark = pytest.mark.psu


@pytest.fixture(scope="module")
def psu():
    try:
        instrument = KeysightE36300.connect(timeout_ms=3000)
    except Exception as exc:
        pytest.skip(f"Keysight PSU not reachable: {exc}")
    yield instrument
    instrument.close()


def test_identify(psu):
    identity = psu.identify()
    assert "keysight" in identity.manufacturer.lower()
    assert identity.model in ("E36311A", "E36312A", "E36313A")


def test_no_errors_pending(psu):
    assert psu.get_errors() == []


@pytest.mark.parametrize("channel", [1, 2, 3])
def test_measure_is_read_only(psu, channel):
    # Near zero (e.g. output off, no load) the current/voltage sense ADC can read a
    # few tens of microamps/volts either side of 0 — that's noise floor, not a fault.
    reading = psu.measure(channel)
    assert reading.voltage >= -0.01
    assert reading.current >= -0.01


@pytest.mark.parametrize("channel", [1, 2, 3])
def test_get_output_state(psu, channel):
    assert isinstance(psu.get_output(channel), bool)


@pytest.mark.parametrize("channel", [1, 2, 3])
def test_get_voltage_and_current_setpoints(psu, channel):
    max_v, max_i = psu.ranges[channel]
    assert 0.0 <= psu.get_voltage_setpoint(channel) <= max_v
    assert 0.0 <= psu.get_current_limit(channel) <= max_i


# ---------------------------------------------------------------------------
# State-changing tests — channel 1 only, restored to its prior setpoint after.
# ---------------------------------------------------------------------------

def test_set_voltage_channel1(psu):
    original = psu.get_voltage_setpoint(1)
    try:
        psu.set_voltage(1, 1.0)
        assert psu.get_voltage_setpoint(1) == pytest.approx(1.0, abs=1e-3)
    finally:
        psu.set_voltage(1, original)


def test_set_current_limit_channel1(psu):
    original = psu.get_current_limit(1)
    try:
        psu.set_current_limit(1, 0.5)
        assert psu.get_current_limit(1) == pytest.approx(0.5, abs=1e-3)
    finally:
        psu.set_current_limit(1, original)


def test_output_toggle_channel1(psu):
    original = psu.get_output(1)
    try:
        psu.set_output(1, False)
        assert psu.get_output(1) is False
        psu.set_output(1, True)
        assert psu.get_output(1) is True
    finally:
        psu.set_output(1, original)


def test_invalid_channel_rejected(psu):
    with pytest.raises(ValueError):
        psu.measure(4)
