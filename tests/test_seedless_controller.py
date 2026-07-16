"""SeedlessController — register apply/restore for the SEEDLESS test.

Pure-software: fake console + fake sensors record every I2C call so the
tests can assert exact registers, values, and ORDER. The restore ordering
assertions are safety-critical — re-tightening the safety ULs while
TA_PULSE_WIDTH is still 2 ms latches TA_shutdown in the safety FPGA.
"""

import threading

from omotion.seedless import (
    SeedlessController,
    TA_PULSE_WIDTH_SEEDLESS, TA_PULSE_WIDTH_BASELINE,
    PULSE_WIDTH_UL_SEEDLESS, PULSE_WIDTH_UL_BASELINE,
    SEED_CW_GAIN_BASELINE,
    EXPOSURE_SEEDLESS_BYTE, EXPOSURE_RESTORE_BYTE,
)


class FakeConsole:
    def __init__(self, fail_reads=False, fail_writes=False):
        self.writes = []            # (channel, reg_addr, tuple(data))
        self.fail_reads = fail_reads
        self.fail_writes = fail_writes
        # Simulated live register values, keyed (channel, reg_addr).
        self.regs = {
            (4, 0x00): bytes(TA_PULSE_WIDTH_BASELINE),
            (5, 0x02): bytes([0x00, 0x00]),
            (5, 0x04): bytes(SEED_CW_GAIN_BASELINE),
            (6, 0x04): bytes(PULSE_WIDTH_UL_BASELINE),
            (7, 0x04): bytes(PULSE_WIDTH_UL_BASELINE),
        }

    def read_i2c_packet(self, mux_index, channel, device_addr, reg_addr, read_len):
        assert mux_index == 1 and device_addr == 0x41
        if self.fail_reads:
            return None, None
        data = self.regs[(channel, reg_addr)]
        return data, len(data)

    def write_i2c_packet(self, mux_index, channel, device_addr, reg_addr, data):
        assert mux_index == 1 and device_addr == 0x41
        if self.fail_writes:
            return False
        self.writes.append((channel, reg_addr, tuple(data)))
        self.regs[(channel, reg_addr)] = bytes(data)
        return True


class FakeSensor:
    def __init__(self):
        self.calls = []             # ("switch", cam) or ("write", reg, val)

    def switch_camera(self, cam_id):
        self.calls.append(("switch", cam_id))
        return object()             # truthy response packet

    def camera_i2c_write(self, packet):
        self.calls.append(("write", packet.register_address, packet.data))
        return True


def _controller(console=None, left=None, n_frames=100):
    return SeedlessController(
        console=console if console is not None else FakeConsole(),
        sensors=[("left", left if left is not None else FakeSensor(), 0x03)],
        n_frames=n_frames,
        settle_s=0.0,               # no 50 ms sleeps in unit tests
    )


def test_apply_writes_seedless_values():
    console = FakeConsole()
    ctrl = _controller(console=console)
    assert ctrl.apply() is True
    w = console.writes
    assert (6, 0x04, tuple(PULSE_WIDTH_UL_SEEDLESS)) in w   # EE UL widened
    assert (7, 0x04, tuple(PULSE_WIDTH_UL_SEEDLESS)) in w   # OPT UL widened
    assert (5, 0x04, (0x00, 0x00)) in w                     # seed CW gain -> 0
    assert (5, 0x02, (0x00, 0x00)) in w                     # seed DDS gain -> 0
    assert (4, 0x00, tuple(TA_PULSE_WIDTH_SEEDLESS)) in w   # TA -> 2 ms
    # Safety ULs must be widened BEFORE the TA width is raised.
    assert w.index((6, 0x04, tuple(PULSE_WIDTH_UL_SEEDLESS))) \
         < w.index((4, 0x00, tuple(TA_PULSE_WIDTH_SEEDLESS)))


def test_apply_sets_exposure_on_masked_cameras_only():
    left = FakeSensor()
    ctrl = _controller(left=left)
    ctrl.apply()
    switches = [c for c in left.calls if c[0] == "switch"]
    assert switches == [("switch", 0), ("switch", 1)]       # mask 0x03
    writes = [c for c in left.calls if c[0] == "write"]
    assert ("write", 0x3502, EXPOSURE_SEEDLESS_BYTE) in writes
    assert ("write", 0x3501, 0x00) in writes


def test_restore_order_is_ta_then_uls_then_seed_then_exposure():
    console = FakeConsole()
    left = FakeSensor()
    ctrl = _controller(console=console, left=left)
    ctrl.apply()
    console.writes.clear()
    left.calls.clear()
    assert ctrl.restore() is True
    w = console.writes
    i_ta  = w.index((4, 0x00, tuple(TA_PULSE_WIDTH_BASELINE)))
    i_ee  = w.index((6, 0x04, tuple(PULSE_WIDTH_UL_BASELINE)))
    i_opt = w.index((7, 0x04, tuple(PULSE_WIDTH_UL_BASELINE)))
    i_cw  = w.index((5, 0x04, tuple(SEED_CW_GAIN_BASELINE)))
    assert i_ta < i_ee and i_ta < i_opt        # TA narrowed FIRST
    assert i_ee < i_cw and i_opt < i_cw        # limits before seed back on
    assert ("write", 0x3502, EXPOSURE_RESTORE_BYTE) in left.calls


def test_restore_uses_snapshot_not_constants():
    console = FakeConsole()
    console.regs[(4, 0x00)] = bytes([0x99, 0x01, 0x00])     # non-default live value
    ctrl = _controller(console=console)
    ctrl.apply()
    console.writes.clear()
    ctrl.restore()
    assert (4, 0x00, (0x99, 0x01, 0x00)) in console.writes


def test_restore_falls_back_to_baseline_when_snapshot_read_failed():
    console = FakeConsole(fail_reads=True)
    ctrl = _controller(console=console)
    ctrl.apply()                                # snapshot reads all fail
    console.writes.clear()
    ctrl.restore()
    assert (4, 0x00, tuple(TA_PULSE_WIDTH_BASELINE)) in console.writes
    assert (6, 0x04, tuple(PULSE_WIDTH_UL_BASELINE)) in console.writes


def test_restore_is_idempotent():
    console = FakeConsole()
    ctrl = _controller(console=console)
    ctrl.apply()
    ctrl.restore()
    n = len(console.writes)
    ctrl.restore()
    assert len(console.writes) == n             # second call is a no-op


def test_restore_without_apply_is_noop():
    console = FakeConsole()
    ctrl = _controller(console=console)
    ctrl.restore()
    assert console.writes == []


def test_schedule_restore_fires_thread_once():
    console = FakeConsole()
    ctrl = _controller(console=console)
    ctrl.apply()
    console.writes.clear()
    ctrl.schedule_restore()
    ctrl.schedule_restore()                     # duplicate — must not double-fire
    assert ctrl.wait_restored(timeout=5.0)
    assert (4, 0x00, tuple(TA_PULSE_WIDTH_BASELINE)) in console.writes
    assert console.writes.count((4, 0x00, tuple(TA_PULSE_WIDTH_BASELINE))) == 1


def test_apply_failure_returns_false():
    console = FakeConsole(fail_writes=True)
    ctrl = _controller(console=console)
    assert ctrl.apply() is False


def test_failed_restore_does_not_latch_and_is_retried():
    # Safety property: the widened-safety window must eventually close. A
    # restore that fails its writes must NOT latch _restored, so a later
    # restore (e.g. from ScanWorkflow teardown) retries and closes it.
    console = FakeConsole()
    ctrl = _controller(console=console)
    ctrl.apply()
    console.writes.clear()
    console.fail_writes = True
    assert ctrl.restore() is False
    assert not ctrl._restored.is_set()
    console.fail_writes = False
    assert ctrl.restore() is True
    assert (4, 0x00, tuple(TA_PULSE_WIDTH_BASELINE)) in console.writes
    assert (6, 0x04, tuple(PULSE_WIDTH_UL_BASELINE)) in console.writes


def test_partial_apply_still_restores():
    # apply() sets _applied=True BEFORE the writes, so even a failed/partial
    # apply leaves the controller in a state where restore() runs and puts
    # the registers back to baseline.
    console = FakeConsole(fail_writes=True)
    ctrl = _controller(console=console)
    assert ctrl.apply() is False
    assert ctrl._applied is True
    console.fail_writes = False
    console.writes.clear()
    assert ctrl.restore() is True
    assert (4, 0x00, tuple(TA_PULSE_WIDTH_BASELINE)) in console.writes
