"""SEEDLESS ScanRequest field + trigger-config merge (pure-software).

Also covers the safety-critical exit-path guarantee: if the console fails
AFTER a successful seedless apply() (opening the widened-safety / seed-off
window), the window is still closed by the worker's outer finally.
"""

from types import SimpleNamespace

from omotion.ScanWorkflow import (
    ScanRequest,
    ScanWorkflow,
    _seedless_trigger_overrides,
)
from omotion.seedless import (
    TA_PULSE_WIDTH_BASELINE,
    PULSE_WIDTH_UL_BASELINE,
    SEED_CW_GAIN_BASELINE,
)


def test_scan_request_defaults_off():
    req = ScanRequest(subject_id="s", duration_sec=10,
                      left_camera_mask=0x66, right_camera_mask=0x66)
    assert req.seedless_frames == 0


def test_seedless_trigger_overrides_push_dark_slot_past_exposure():
    # Dark frames are laser pulses delayed past the exposure window. The
    # pulse starts at (100 us base delay + LaserPulseSkipDelayUsec) and must
    # begin after the 2295 us seedless exposure closes.
    ov = _seedless_trigger_overrides()
    assert ov == {"LaserPulseSkipDelayUsec": 2500}
    assert 100 + ov["LaserPulseSkipDelayUsec"] > 2295


def test_no_overrides_when_disabled():
    assert callable(_seedless_trigger_overrides)


# ── Exit-path safety guarantee ─────────────────────────────────────────────
#
# apply() opens the widened-safety / seed-off window BEFORE the worker's inner
# try. set_trigger_json / start_trigger run in that gap and RE-RAISE on a
# console comm error, jumping straight to the outer finally. That outer finally
# is the only block guaranteed to close the window on this path — the test
# fails if that backstop restore is removed.


class _FakeSignal:
    # connect() must accept a type= kwarg (PyQt DirectConnection path).
    def connect(self, *a, **k):
        pass

    def disconnect(self, *a, **k):
        pass


class _FakeConsole:
    """Records I2C writes (mirrors tests/test_seedless_controller.FakeConsole)
    and raises from set_trigger_json to simulate a console comm error that
    surfaces AFTER a successful seedless apply()."""

    def __init__(self):
        self.writes = []            # (channel, reg_addr, tuple(data))
        self.name = "console"
        self.signal_state_changed = _FakeSignal()
        self.regs = {
            (4, 0x00): bytes(TA_PULSE_WIDTH_BASELINE),
            (5, 0x02): bytes([0x00, 0x00]),
            (5, 0x04): bytes(SEED_CW_GAIN_BASELINE),
            (6, 0x04): bytes(PULSE_WIDTH_UL_BASELINE),
            (7, 0x04): bytes(PULSE_WIDTH_UL_BASELINE),
        }

    def read_i2c_packet(self, mux_index, channel, device_addr, reg_addr, read_len):
        data = self.regs[(channel, reg_addr)]
        return data, len(data)

    def write_i2c_packet(self, mux_index, channel, device_addr, reg_addr, data):
        self.writes.append((channel, reg_addr, tuple(data)))
        self.regs[(channel, reg_addr)] = bytes(data)
        return True

    def set_trigger_json(self, data=None):
        raise RuntimeError("console gone")

    def start_trigger(self):            # not reached on the failure path
        pass

    def stop_trigger(self):
        pass


class _FakeSensor:
    def __init__(self, name):
        self.name = name
        self.signal_state_changed = _FakeSignal()
        self.uart = SimpleNamespace(
            histo=SimpleNamespace(flush_stale_data=lambda expected_size=None: 0)
        )

    def is_connected(self):
        return True

    def switch_camera(self, cam_id):    # SeedlessController exposure path
        return object()

    def camera_i2c_write(self, packet):
        return True


class _FakeInterface:
    def __init__(self, console, left, right):
        self.console = console
        self.left = left
        self.right = right

    def resolve_trigger_config(self, override=None):
        return dict(override or {})

    def run_on_sensors(self, method, *args, target=None):
        return True


def test_console_failure_after_apply_still_restores_window():
    console = _FakeConsole()
    wf = ScanWorkflow(_FakeInterface(
        console, _FakeSensor("left"), _FakeSensor("right")))

    # One camera, one side keeps the (settle-delayed) apply/restore quick.
    req = ScanRequest(subject_id="seedless", duration_sec=1,
                      left_camera_mask=0x01, right_camera_mask=0x00,
                      seedless_frames=100, skip_default_storage=True)

    assert wf.start_scan(req) is True
    wf.await_complete(timeout_sec=10.0)

    # The scan aborted because set_trigger_json raised...
    assert wf.last_scan_error == "console gone"
    # ...and the outer-finally backstop still closed the window.
    ctrl = wf._seedless_ctrl
    assert ctrl is not None and ctrl.wait_restored(timeout=5.0)
    # Baseline writes prove the close: TA narrowed + both safety ULs re-tightened.
    assert (4, 0x00, tuple(TA_PULSE_WIDTH_BASELINE)) in console.writes
    assert (6, 0x04, tuple(PULSE_WIDTH_UL_BASELINE)) in console.writes
    assert (7, 0x04, tuple(PULSE_WIDTH_UL_BASELINE)) in console.writes
