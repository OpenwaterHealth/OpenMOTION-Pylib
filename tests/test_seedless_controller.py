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
    RATE_LL_SEEDLESS, RATE_LL_BASELINE,
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
            (6, 0x08): bytes(RATE_LL_BASELINE),   # EE_RATE_LL
            (7, 0x08): bytes(RATE_LL_BASELINE),   # OPT_RATE_LL
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
    assert (6, 0x08, tuple(RATE_LL_SEEDLESS)) in w         # EE rate LL -> 0
    assert (7, 0x08, tuple(RATE_LL_SEEDLESS)) in w         # OPT rate LL -> 0
    assert (5, 0x04, (0x00, 0x00)) in w                     # seed CW gain -> 0
    assert (5, 0x02, (0x00, 0x00)) in w                     # seed DDS gain -> 0
    assert (4, 0x00, tuple(TA_PULSE_WIDTH_SEEDLESS)) in w   # TA -> 2 ms
    # Safety ULs must be widened BEFORE the TA width is raised.
    assert w.index((6, 0x04, tuple(PULSE_WIDTH_UL_SEEDLESS))) \
         < w.index((4, 0x00, tuple(TA_PULSE_WIDTH_SEEDLESS)))
    # Rate LL must be relaxed BEFORE the seed is turned off (else the seed-off
    # dim output trips rate_lower_limit_fail before the check is disabled).
    assert w.index((6, 0x08, tuple(RATE_LL_SEEDLESS))) \
         < w.index((5, 0x04, (0x00, 0x00)))


def test_apply_clears_latched_faults_first():
    # A latched safety fault from a prior run blocks the next scan's trigger.
    # apply() must pulse EE/OPT dynamic_control[0] (reg 0x22) to clear it,
    # BEFORE writing the seedless config.
    console = FakeConsole()
    ctrl = _controller(console=console)
    assert ctrl.apply() is True
    w = console.writes
    assert (6, 0x22, (0x01, 0x00)) in w        # EE clear_fail asserted
    assert (7, 0x22, (0x01, 0x00)) in w        # OPT clear_fail asserted
    assert (6, 0x22, (0x00, 0x00)) in w        # de-asserted
    # Fault clear happens before the first seedless config write.
    assert w.index((6, 0x22, (0x01, 0x00))) \
         < w.index((6, 0x04, tuple(PULSE_WIDTH_UL_SEEDLESS)))


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
    i_rate_ee = w.index((6, 0x08, tuple(RATE_LL_BASELINE)))
    i_rate_opt = w.index((7, 0x08, tuple(RATE_LL_BASELINE)))
    assert i_ta < i_ee and i_ta < i_opt        # TA narrowed FIRST
    assert i_ee < i_cw and i_opt < i_cw        # pulse-width ULs before seed back on
    # Rate LL restored AFTER the seed is back on (so normal pulses are flowing
    # when rate monitoring resumes; else it would immediately re-trip).
    assert i_cw < i_rate_ee and i_cw < i_rate_opt
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


def test_restore_racing_apply_serializes_to_baseline():
    # Bench 2026-07-17: a restore() fired while apply() was mid-flight
    # interleaved with it -- restore wrote TA baseline early, apply wrote TA
    # seedless LAST, and restore's latch made every later restore a no-op:
    # TA left at 2 ms with tight safety ULs (safety FPGA latched
    # pulse_upper_limit_fail on the first pulse). apply() now holds the same
    # lock as restore(), so a racing restore must serialize BEHIND apply and
    # leave every register at baseline.
    import time as _time

    console = FakeConsole()
    gate = threading.Event()
    orig_write = console.write_i2c_packet

    def gated_write(*args, **kwargs):
        gate.wait(5.0)   # hold apply mid-write until the racing restore exists
        return orig_write(*args, **kwargs)

    console.write_i2c_packet = gated_write
    ctrl = _controller(console=console)

    t_apply = threading.Thread(target=ctrl.apply)
    t_apply.start()
    _time.sleep(0.1)                 # apply holds the lock, blocked at gate
    t_restore = threading.Thread(target=ctrl.restore)
    t_restore.start()
    _time.sleep(0.1)                 # restore is queued on the lock
    gate.set()                       # let apply finish; restore runs after
    t_apply.join(5.0)
    t_restore.join(5.0)
    assert not t_apply.is_alive() and not t_restore.is_alive()

    # Final register state must be BASELINE everywhere -- the restore ran
    # strictly after apply completed, not interleaved with it.
    assert console.regs[(4, 0x00)] == bytes(TA_PULSE_WIDTH_BASELINE)
    assert console.regs[(6, 0x04)] == bytes(PULSE_WIDTH_UL_BASELINE)
    assert console.regs[(7, 0x04)] == bytes(PULSE_WIDTH_UL_BASELINE)
    assert console.regs[(5, 0x04)] == bytes(SEED_CW_GAIN_BASELINE)
    assert console.regs[(6, 0x08)] == bytes(RATE_LL_BASELINE)
    assert ctrl._restored.is_set()


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
