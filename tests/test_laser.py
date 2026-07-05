"""omotion.laser — bundled laser-power config + I2C application."""

from omotion.laser import FpgaMap, apply_laser_power, load_laser_params


class _FakeConsole:
    """Records write_i2c_packet calls; read_config returns no user overrides."""

    def __init__(self, write_ok=True):
        self.writes = []
        self._write_ok = write_ok

    def read_config(self):
        return None

    def write_i2c_packet(self, *, mux_index, channel, device_addr, reg_addr, data):
        self.writes.append((mux_index, channel, device_addr, reg_addr, bytes(data)))
        return self._write_ok


def test_load_laser_params_returns_bundled_list():
    params = load_laser_params()
    assert params, "bundled laser_params.json should be non-empty"
    assert all("friendlyName" in p and "dataToSend" in p for p in params)


def test_load_laser_params_fault_set_available():
    assert load_laser_params(force_fault=True), "fault param set should load"


def test_fpga_map_lookup_known_entry():
    entry = FpgaMap().get_entry_by_friendly_name("TA_PULSE_WIDTH")
    assert entry is not None
    assert entry["mux_idx"] == 1
    assert entry["channel"] == 4
    assert entry["i2c_addr"] == 65
    assert entry["start_address"] == 0
    assert entry["data_size"] == "24B"


def test_fpga_map_unknown_entry_returns_none():
    assert FpgaMap().get_entry_by_friendly_name("NOT_A_REAL_NAME") is None


def test_apply_laser_power_writes_bundled_params():
    console = _FakeConsole()
    assert apply_laser_power(console) is True
    assert console.writes, "expected at least one I2C write"
    # First bundled param is TA_PULSE_WIDTH (dataToSend [27,6,0]) → TA block:
    # mux 1, channel 4, device 0x41 (65), register 0.
    assert console.writes[0] == (1, 4, 65, 0, bytes([27, 6, 0]))


def test_apply_laser_power_returns_false_on_write_failure():
    assert apply_laser_power(_FakeConsole(write_ok=False)) is False


def test_apply_laser_power_false_when_no_params():
    # Empty params + a map that finds nothing → nothing to apply.
    assert apply_laser_power(_FakeConsole(), laser_params=[]) is False


class _Lock:
    def __init__(self):
        self.locked = 0
        self.unlocked = 0

    def lock(self):
        self.locked += 1

    def unlock(self):
        self.unlocked += 1


def test_apply_laser_power_holds_lock_around_writes():
    lk = _Lock()
    assert apply_laser_power(_FakeConsole(), lock=lk) is True
    assert lk.locked == 1 and lk.unlocked == 1


def _rate_ll_writes(console):
    # Both RATE_LL registers live at 0x41 offset 0x08 (EE ch 6, OPT ch 7).
    return {
        ch: data
        for (mux, ch, dev, reg, data) in console.writes
        if dev == 0x41 and reg == 0x08 and ch in (6, 7)
    }


def test_apply_laser_power_default_rate_keeps_baseline_rate_ll():
    console = _FakeConsole()
    assert apply_laser_power(console, trigger_freq_hz=40.0) is True
    writes = _rate_ll_writes(console)
    # Baseline: 70313 ticks x 0.32 us = 22,500 us min period.
    assert writes[6] == (70313).to_bytes(4, "little")
    assert writes[7] == (70313).to_bytes(4, "little")


def test_apply_laser_power_60hz_scales_rate_ll():
    console = _FakeConsole()
    assert apply_laser_power(console, trigger_freq_hz=60.0) is True
    writes = _rate_ll_writes(console)
    # 70313 * 40/60 = 46875 ticks x 0.32 us = 15,000 us min period —
    # same 0.9x proportional margin at the 16,667 us period of 60 Hz.
    assert writes[6] == (46875).to_bytes(4, "little")
    assert writes[7] == (46875).to_bytes(4, "little")


def test_apply_laser_power_none_rate_keeps_baseline_rate_ll():
    console = _FakeConsole()
    assert apply_laser_power(console) is True
    writes = _rate_ll_writes(console)
    assert writes[6] == (70313).to_bytes(4, "little")
    assert writes[7] == (70313).to_bytes(4, "little")


def test_apply_laser_power_60hz_scales_user_config_rate_ll_override():
    """A stored per-key RATE_LL user-config override (us, calibrated for
    40 Hz) must be rescaled like the bundled baseline — written verbatim
    at 60 Hz it would exceed the pulse period and trip the interlock on
    every pulse (sdk#129 review finding)."""
    class _FakeConsoleWithCfg(_FakeConsole):
        def read_config(self):
            class _Cfg:
                json_data = {"EE_RATE_LL": 22500.0}
            return _Cfg()

    console = _FakeConsoleWithCfg()
    assert apply_laser_power(console, trigger_freq_hz=60.0) is True
    writes = _rate_ll_writes(console)
    # override 22,500 us / 0.32 = 70312.5 raw ticks, x 40/60 = 46875.
    assert writes[6] == (46875).to_bytes(4, "little")
    # OPT side has no override — bundled baseline scaling still applies.
    assert writes[7] == (46875).to_bytes(4, "little")


def test_trigger_overrides_for_rate_rejects_unsupported_rate():
    from omotion.config import trigger_overrides_for_rate
    import pytest

    with pytest.raises(ValueError):
        trigger_overrides_for_rate(0)
    with pytest.raises(ValueError):
        trigger_overrides_for_rate(100)


def test_trigger_overrides_for_rate_scales_skip_delay():
    from omotion.config import trigger_overrides_for_rate

    o60 = trigger_overrides_for_rate(60)
    assert o60["TriggerFrequencyHz"] == 60
    # 1800 us x 40/60 = 1200 us: post-dark interval 16667-1200 = 15467 us
    # stays above the scaled 15000 us RATE_LL floor.
    assert o60["LaserPulseSkipDelayUsec"] == 1200
    o40 = trigger_overrides_for_rate(40)
    assert o40["LaserPulseSkipDelayUsec"] == 1800


def test_apply_laser_power_releases_lock_on_write_failure():
    lk = _Lock()
    assert apply_laser_power(_FakeConsole(write_ok=False), lock=lk) is False
    assert lk.locked == 1 and lk.unlocked == 1
