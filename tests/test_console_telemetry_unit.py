import logging

from omotion.ConsoleTelemetry import ConsoleTelemetry, ConsoleTelemetryPoller


class _FakeConsole:
    def __init__(self, se_raw: int, so_raw: int) -> None:
        self._se_raw = se_raw
        self._so_raw = so_raw

    def read_i2c_packet(self, mux_index, channel, device_addr, reg_addr, read_len):
        assert mux_index == 1
        assert device_addr == 0x41
        assert reg_addr == 0x24
        assert read_len == 1
        if channel == 6:
            return bytes([self._se_raw]), 1
        if channel == 7:
            return bytes([self._so_raw]), 1
        raise AssertionError(f"Unexpected channel {channel}")


def test_read_safety_logs_named_se_so_faults(caplog):
    poller = ConsoleTelemetryPoller(_FakeConsole(se_raw=0x01, so_raw=0x06))
    snap = ConsoleTelemetry()

    with caplog.at_level(logging.ERROR):
        poller._read_safety(snap)

    assert snap.safety_ok is False
    assert "Safety interlock SE faults" in caplog.text
    assert "POWER_PEAK_CURRENT_LIMIT_FAIL" in caplog.text
    assert "Safety interlock SO faults" in caplog.text
    assert "PULSE_UPPER_LIMIT_FAIL_OR_PULSE_LOWER_LIMIT_FAIL" in caplog.text
    assert "RATE_LOWER_LIMIT_FAIL" in caplog.text


from omotion.ConsoleTelemetry import PdcSample, PDC_MA_PER_LSB


def test_pdc_sample_scales_raw_to_mA():
    s = PdcSample.from_raw(frame_idx=42, pdc_raw=100, flags=0x01, host_recv_timestamp=1.23)
    assert s.frame_idx == 42
    assert s.pdc_mA == 100 * PDC_MA_PER_LSB
    assert s.dark_slot is True
    assert s.host_recv_timestamp == 1.23
    assert s.dropped_delta == 0


def test_pdc_sample_dark_slot_false_when_flags_clear():
    s = PdcSample.from_raw(frame_idx=43, pdc_raw=200, flags=0x00, host_recv_timestamp=0.0)
    assert s.dark_slot is False
    assert s.demod_slot is False


def test_pdc_sample_demod_slot_decoded_from_flags_bit1():
    s = PdcSample.from_raw(frame_idx=44, pdc_raw=300, flags=0x02, host_recv_timestamp=0.0)
    assert s.demod_slot is True
    assert s.dark_slot is False


def test_demod_frequency_word_conversion():
    from omotion.MotionConsole import MotionConsole

    # f_out = word * MCLK / 2**28 with assumed 25 MHz MCLK
    assert MotionConsole.demod_frequency_word(0) == 0
    assert MotionConsole.demod_frequency_word(1_562_500.0) == 0x01000000
    assert MotionConsole.demod_frequency_word(25e6 / (1 << 28)) == 1  # one LSB
    # clamps to the 28-bit register
    assert MotionConsole.demod_frequency_word(1e12) == (1 << 28) - 1
    # explicit MCLK override
    assert MotionConsole.demod_frequency_word(1000.0, mclk_hz=1 << 28) == 1000


def test_demod_phase_word_conversion():
    import math
    from omotion.MotionConsole import MotionConsole

    # phase = 2*pi/4096 * PHASEREG  =>  PHASEREG = phase * 4096 / (2*pi)
    assert MotionConsole.demod_phase_word(0.0) == 0
    assert MotionConsole.demod_phase_word(math.pi) == 2048
    assert MotionConsole.demod_phase_word(math.pi / 2) == 1024
    # full turn wraps back to zero, negative phases normalize
    assert MotionConsole.demod_phase_word(2 * math.pi) == 0
    assert MotionConsole.demod_phase_word(-math.pi / 2) == 3072
    # result always fits the 12-bit register
    assert 0 <= MotionConsole.demod_phase_word(123.456) <= 0x0FFF


from unittest.mock import MagicMock
import time as _time

class _FakeConsoleForDrain:
    """Mock console that returns scripted drain responses on each call."""
    def __init__(self, drain_responses):
        self._drain_responses = list(drain_responses)
        self.drain_calls = 0
        self.tec_calls = 0
        self.pdu_calls = 0
        self.safety_calls = 0
        self.analog_calls = 0

    def is_connected(self):
        return True

    def get_pdc_buffer(self, max_samples=64):
        self.drain_calls += 1
        if self._drain_responses:
            return self._drain_responses.pop(0)
        return 0, []

    # Stubs used by the slow tick — set as MagicMocks externally if needed
    def tec_status(self):
        self.tec_calls += 1
        return 0.0, 0.0, 0.0, 0.0, False

    def read_pdu_mon(self):
        self.pdu_calls += 1
        m = MagicMock(); m.raws = []; m.volts = []
        return m

    def read_i2c_packet(self, mux_index, channel, device_addr, reg_addr, read_len):
        self.safety_calls += 1
        return b"\x00" * read_len, read_len

    def get_lsync_pulsecount(self):
        self.analog_calls += 1
        return 0


def test_poller_drains_pdc_each_tick_and_fires_listeners():
    drain_responses = [
        (0, [(1, 100, 0x00), (2, 200, 0x01)]),
        (0, [(3, 150, 0x00)]),
    ]
    console = _FakeConsoleForDrain(drain_responses)
    poller = ConsoleTelemetryPoller(console)
    received = []
    poller.add_pdc_listener(received.append)

    # Run two ticks synchronously by calling the inner method that processes
    # one drain pass + (optionally) a slow refresh.
    poller._tick_once()
    poller._tick_once()

    assert console.drain_calls == 2
    assert len(received) == 3
    assert received[0].frame_idx == 1 and received[0].dark_slot is False
    assert received[1].dark_slot is True
    assert received[2].frame_idx == 3
    assert poller.get_last_pdc_sample().frame_idx == 3


def test_poller_runs_slow_refresh_every_10th_tick():
    console = _FakeConsoleForDrain([(0, [])] * 25)
    poller = ConsoleTelemetryPoller(console)
    for _ in range(20):
        poller._tick_once()
    # Slow refresh fires on tick 0 and tick 10 (and not in between).
    assert console.tec_calls == 2
    assert console.pdu_calls == 2


def test_poller_attaches_dropped_delta_to_first_sample_only():
    drain_responses = [(7, [(1, 100, 0), (2, 110, 0)])]
    console = _FakeConsoleForDrain(drain_responses)
    poller = ConsoleTelemetryPoller(console)
    received = []
    poller.add_pdc_listener(received.append)
    poller._tick_once()
    assert received[0].dropped_delta == 7
    assert received[1].dropped_delta == 0
