"""
Pure-logic tests for the Keysight E36300 driver (keysight_psu.py).

No hardware involved — a FakeInstrument stands in for the PyVISA resource so
these run anywhere, same tier as test_calibration_workflow_compute.py etc.
"""

import pytest

from keysight_psu import (
    CHANNEL_RANGES,
    ChannelReading,
    KeysightE36300,
    PSUError,
    list_available_resources,
)

pytestmark = pytest.mark.unit


class FakeInstrument:
    """Stand-in for an open pyvisa resource: records writes, scripts query replies."""

    def __init__(self, idn="Keysight Technologies,E36313A,MY00000000,K01.03.06.01"):
        self.written = []
        self.timeout = None
        self.read_termination = None
        self.write_termination = None
        self.closed = False
        self._idn = idn
        self._error_queue = []
        self._responses = {}

    def queue_error(self, error_line):
        self._error_queue.append(error_line)

    def script_response(self, cmd, value):
        self._responses[cmd] = value

    def write(self, cmd):
        self.written.append(cmd)

    def query(self, cmd):
        if cmd == "*IDN?":
            return self._idn
        if cmd == "SYST:ERR?":
            return self._error_queue.pop(0) if self._error_queue else '+0,"No error"'
        return self._responses.get(cmd, "0")

    def close(self):
        self.closed = True


def make_psu(idn=None):
    fake = FakeInstrument(idn=idn) if idn else FakeInstrument()
    return KeysightE36300(fake), fake


# ---------------------------------------------------------------------------
# identity / construction
# ---------------------------------------------------------------------------

def test_identify_parses_idn():
    psu, _ = make_psu()
    identity = psu.identify()
    assert identity.manufacturer == "Keysight Technologies"
    assert identity.model == "E36313A"
    assert identity.serial == "MY00000000"


def test_unsupported_model_raises():
    with pytest.raises(PSUError):
        make_psu(idn="Keysight Technologies,E99999A,MY00000000,1.0")


def test_malformed_idn_raises():
    with pytest.raises(PSUError):
        make_psu(idn="not a valid idn response")


def test_ranges_selected_for_model():
    psu, _ = make_psu()
    assert psu.ranges == CHANNEL_RANGES["E36313A"]


# ---------------------------------------------------------------------------
# validation (reject before writing to the instrument)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("channel", [0, 4, -1])
def test_set_voltage_rejects_invalid_channel(channel):
    psu, fake = make_psu()
    with pytest.raises(ValueError):
        psu.set_voltage(channel, 1.0)
    assert fake.written == []


def test_set_voltage_rejects_out_of_range():
    psu, fake = make_psu()
    with pytest.raises(ValueError):
        psu.set_voltage(1, 999.0)
    assert fake.written == []


def test_set_current_limit_rejects_out_of_range():
    psu, fake = make_psu()
    with pytest.raises(ValueError):
        psu.set_current_limit(2, 999.0)
    assert fake.written == []


def test_set_voltage_rejects_negative():
    psu, fake = make_psu()
    with pytest.raises(ValueError):
        psu.set_voltage(1, -0.1)
    assert fake.written == []


# ---------------------------------------------------------------------------
# SCPI formatting
# ---------------------------------------------------------------------------

def test_set_voltage_writes_expected_scpi():
    psu, fake = make_psu()
    psu.set_voltage(1, 3.3)
    assert fake.written[0] == "VOLT 3.3000, (@1)"


def test_set_current_limit_writes_expected_scpi():
    psu, fake = make_psu()
    psu.set_current_limit(3, 0.5)
    assert fake.written[0] == "CURR 0.5000, (@3)"


def test_set_output_writes_on_off():
    psu, fake = make_psu()
    psu.set_output(1, True)
    psu.set_output(1, False)
    assert fake.written == ["OUTP ON, (@1)", "OUTP OFF, (@1)"]


def test_get_output_parses_bool():
    psu, fake = make_psu()
    fake.script_response("OUTP? (@1)", "1")
    assert psu.get_output(1) is True
    fake.script_response("OUTP? (@1)", "0")
    assert psu.get_output(1) is False


def test_measure_returns_channel_reading():
    psu, fake = make_psu()
    fake.script_response("MEAS:VOLT? (@2)", "5.021")
    fake.script_response("MEAS:CURR? (@2)", "0.103")
    reading = psu.measure(2)
    assert reading == ChannelReading(voltage=5.021, current=0.103)


def test_reset_sends_star_rst():
    psu, fake = make_psu()
    psu.reset()
    assert fake.written == ["*RST"]


# ---------------------------------------------------------------------------
# error-queue propagation
# ---------------------------------------------------------------------------

def test_scpi_error_after_write_raises_psu_error():
    psu, fake = make_psu()
    fake.queue_error('-222,"Data out of range"')
    with pytest.raises(PSUError):
        psu.set_voltage(1, 1.0)


def test_get_errors_drains_queue():
    psu, fake = make_psu()
    fake.queue_error('-222,"Data out of range"')
    fake.queue_error('-410,"Query INTERRUPTED"')
    errors = psu.get_errors()
    assert len(errors) == 2


# ---------------------------------------------------------------------------
# lifecycle / discovery
# ---------------------------------------------------------------------------

def test_context_manager_closes():
    fake = FakeInstrument()
    with KeysightE36300(fake) as psu:
        assert psu.identity.model == "E36313A"
    assert fake.closed is True


class FakeResourceManager:
    def __init__(self, resources):
        self._resources = resources

    def list_resources(self):
        return self._resources


def test_list_available_resources_filters_to_usb():
    rm = FakeResourceManager(["USB0::0x2A8D::0x1202::MY123::INSTR", "ASRL3::INSTR", "TCPIP0::1.2.3.4::INSTR"])
    assert list_available_resources(rm) == ["USB0::0x2A8D::0x1202::MY123::INSTR"]
