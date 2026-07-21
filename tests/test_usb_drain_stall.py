"""HIL characterization: how long can the host stall the USB drain before
the sensor firmware gives up?

Why this exists
---------------
`PipelineRunner` consumes sinks inline on the same thread that drains
`LiveUsbSource._batch_queue`, so any slow sink stops `dev.read()` from being
called.  With ~0.2 s of packet queue plus a *shared* 4-slot batch queue, the
host absorbs only a few hundred ms before back-pressure reaches the sensor's
deliberately shallow 4-deep (~100 ms) histogram queue.  See openmotion-sdk#116.

In the field (bloodflow-app#300) a sub-second stall on a 20-minute scan
escalated all the way to the RIGHT module dropping off the USB bus, and no
host-side warning was emitted at any point — `StreamInterface` only warns
after a *full second* of blocked `put`, so everything below that is invisible.

This test injects a controlled stall and records which firmware failure
signatures appear at each stall length, turning "sub-second stalls are bad"
into numbers:

    a. HISTO_QUEUE_FULL  host absorption exhausted
    b. USB_TX_BLOCKED    firmware's histogram TX blocks
    c. CAMERA_OVERRUN    camera receive channels starve
    d. FALSE_SCAN_END    firmware abandons the scan   <- sensor-fw#101
    e. ENDPOINT_LOST     host sees EPIPE / device lost

(d) is the number sensor-fw#101 needs to be specified against.  (a) is the
acceptance metric for #116: with the fix in PR #113 the first stall length
that produces a queue-full should move out by roughly the ratio of the new
batch-queue budget to the current one.

Run it
------
    pytest tests/test_usb_drain_stall.py -s -m "sensor and console"

Needs a console and both sensors.  Nothing is asserted about (b)-(e): they
are recorded and printed.  The only hard assertion is the #116 budget —
see STALL_BUDGET_S.
"""

import logging
import re
import time
from dataclasses import dataclass, field

import pytest

from omotion.ScanWorkflow import ScanRequest
from omotion.config import DEBUG_FLAG_USB_PRINTF

pytestmark = [pytest.mark.sensor, pytest.mark.console, pytest.mark.slow]


# Stall lengths to sweep, seconds.  Ordered small -> large so the run
# degrades gradually; a stall that kills the endpoint ends that scan but
# not the sweep.
STALL_SWEEP_S = (0.1, 0.2, 0.3, 0.5, 1.0, 2.0)

# Seconds of scan per stall value.  Long enough for several stalls to land
# plus one dark interval close (600 frames @ 40 Hz = 15 s).
SCAN_SEC = 40

# One stall every Nth "live" batch.  Batches are batch_size_frames=10 at
# 40 Hz = 0.25 s, so 20 -> a stall roughly every 5 s.
STALL_EVERY_N_BATCHES = 20

# The host stall the system must tolerate without back-pressuring the
# firmware at all.  Today's shipping code is expected to FAIL this — that
# failure is the point of the test, and the number is what PR #113 has to
# move.  Tighten/relax deliberately, with a bench run to back it up.
STALL_BUDGET_S = 0.3


# --------------------------------------------------------------------------
# Firmware signatures, in escalation order
# --------------------------------------------------------------------------

SIGNATURES = (
    ("HISTO_QUEUE_FULL", re.compile(r"HISTO enqueue fail: queue full")),
    ("USB_TX_BLOCKED", re.compile(r"USBD_HISTO_SendData failed|COMM USB TX Timeout")),
    ("CAMERA_OVERRUN", re.compile(r"Error in (?:USART|SPI)\d+: Overrun error"
                                  r"|failed to setup receive for Camera")),
    ("FALSE_SCAN_END", re.compile(r"Scan finished \((\d+) ms")),
    ("ENDPOINT_LOST", re.compile(r"device lost|Pipe error|errno=32")),
    # Host-side: the >1 s put warning. Present == the stall was so long even
    # the current coarse instrumentation caught it.
    ("HOST_PUT_WARNED", re.compile(r"data_queue full for >1s")),
)


class _SignatureCollector(logging.Handler):
    """Scan SDK log records for the firmware/host failure signatures.

    Firmware printf arrives as WARNING records on openmotion.sdk.CommInterface,
    often several newline-separated lines per record, so match against the
    whole formatted message rather than line by line.
    """

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.hits: dict[str, int] = {}
        self.first_seen: dict[str, float] = {}
        self.scan_finished_ms: list[int] = []
        self._t0 = time.monotonic()

    def reset(self):
        self.hits.clear()
        self.first_seen.clear()
        self.scan_finished_ms.clear()
        self._t0 = time.monotonic()

    def emit(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            return
        for name, pattern in SIGNATURES:
            m = pattern.search(msg)
            if not m:
                continue
            self.hits[name] = self.hits.get(name, 0) + 1
            self.first_seen.setdefault(name, time.monotonic() - self._t0)
            if name == "FALSE_SCAN_END":
                self.scan_finished_ms.append(int(m.group(1)))


class StallSink:
    """Sink that blocks the drain thread for `stall_s` every Nth batch.

    Stands in for a slow storage sink (SQLite commit, CSV flush) or any
    heavy work the runner does inline.  Sleeping here is exactly equivalent:
    PipelineRunner._dispatch calls consume() on the thread that would
    otherwise be pulling from _batch_queue -> parser -> dev.read().
    """

    channels = frozenset({"live"})

    def __init__(self, stall_s: float, every_n: int = STALL_EVERY_N_BATCHES):
        self.stall_s = float(stall_s)
        self.every_n = int(every_n)
        self.batches = 0
        self.stalls = 0

    def on_scan_start(self, meta) -> None:
        self.batches = 0
        self.stalls = 0

    def consume(self, channel: str, payload) -> None:
        self.batches += 1
        if self.every_n > 0 and self.batches % self.every_n == 0:
            self.stalls += 1
            time.sleep(self.stall_s)

    def on_complete(self) -> None:
        pass


@dataclass
class StallResult:
    stall_s: float
    stalls_injected: int
    hits: dict = field(default_factory=dict)
    first_seen: dict = field(default_factory=dict)
    scan_finished_ms: list = field(default_factory=list)
    commanded_sec: int = SCAN_SEC

    @property
    def false_scan_end(self) -> bool:
        """A 'Scan finished' well short of the commanded duration."""
        return any(ms < (self.commanded_sec - 5) * 1000
                   for ms in self.scan_finished_ms)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def collector():
    """Attach the signature collector to the SDK's logger tree."""
    handler = _SignatureCollector()
    log = logging.getLogger("openmotion.sdk")
    prev_level = log.level
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        log.removeHandler(handler)
        log.setLevel(prev_level)


@pytest.fixture(scope="module")
def printf_on(sensor_left, sensor_right):
    """Firmware printf must be ON — it is the only channel these signatures
    travel over.  Restores the previous flags afterwards."""
    prev = {}
    for name, sensor in (("left", sensor_left), ("right", sensor_right)):
        try:
            flags = sensor.get_debug_flags()
        except Exception:
            pytest.skip(f"{name} sensor did not answer get_debug_flags")
        prev[name] = flags
        sensor.set_debug_flags(flags | DEBUG_FLAG_USB_PRINTF)
    try:
        yield
    finally:
        for name, sensor in (("left", sensor_left), ("right", sensor_right)):
            try:
                sensor.set_debug_flags(prev[name])
            except Exception:
                logging.getLogger(__name__).warning(
                    "could not restore %s debug flags to 0x%02X",
                    name, prev[name])


@pytest.fixture(scope="module")
def results() -> list:
    """Collected across the sweep; printed by the summary test."""
    return []


# --------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------

def _run_stalled_scan(motion, collector, stall_s: float) -> StallResult:
    sink = StallSink(stall_s)
    collector.reset()

    request = ScanRequest(
        subject_id=f"stall{stall_s:g}",
        duration_sec=SCAN_SEC,
        left_camera_mask=0xFF,
        right_camera_mask=0xFF,
        disable_laser=False,
        sinks=[sink],
        # Production-like: the default DB/CSV sinks stay in the chain, so the
        # measured tolerance is the real system's, not a stripped-down one.
        skip_default_storage=False,
        write_corrected_csv=False,
        write_telemetry_csv=False,
    )

    assert motion.start_scan(request), f"start_scan refused at stall={stall_s}s"
    motion.scan_workflow.await_complete(timeout_sec=SCAN_SEC + 30)

    return StallResult(
        stall_s=stall_s,
        stalls_injected=sink.stalls,
        hits=dict(collector.hits),
        first_seen=dict(collector.first_seen),
        scan_finished_ms=list(collector.scan_finished_ms),
    )


@pytest.mark.parametrize("stall_s", STALL_SWEEP_S)
def test_drain_stall_sweep(motion, console, printf_on, collector, results, stall_s):
    """Record which firmware failure signatures a host stall of `stall_s`
    produces.  Characterization — only the #116 budget is asserted."""
    result = _run_stalled_scan(motion, collector, stall_s)
    results.append(result)

    print(f"\n  stall={stall_s:>4}s  injected={result.stalls_injected:>3}  "
          + "  ".join(f"{name}={result.hits.get(name, 0)}"
                      for name, _ in SIGNATURES))
    if result.scan_finished_ms:
        print(f"    firmware 'Scan finished' at: "
              f"{[ms / 1000 for ms in result.scan_finished_ms]} s "
              f"(commanded {SCAN_SEC} s)")

    if stall_s <= STALL_BUDGET_S:
        assert "HISTO_QUEUE_FULL" not in result.hits, (
            f"a {stall_s}s host stall — within the {STALL_BUDGET_S}s budget — "
            f"already back-pressures the sensor into queue-full. This is "
            f"openmotion-sdk#116; expected to fail until PR #113 lands."
        )

    # Recovery gate: whatever happened, the next scan must still be able to
    # start. If the endpoint was left halted, this is where the sweep stops
    # being meaningful and the operator needs to know immediately.
    if "ENDPOINT_LOST" in result.hits:
        pytest.fail(
            f"stall={stall_s}s took a module off the USB bus "
            f"(ENDPOINT_LOST x{result.hits['ENDPOINT_LOST']}). Remaining "
            f"sweep points need a device power-cycle to be trustworthy."
        )


def test_zz_summary(results):
    """Print the escalation table. Named zz_ so it sorts after the sweep."""
    if not results:
        pytest.skip("sweep did not run")

    def first(pred):
        hits = [r.stall_s for r in sorted(results, key=lambda r: r.stall_s) if pred(r)]
        return f"{hits[0]:g}s" if hits else "not reached"

    print("\n\n  USB drain stall tolerance (openmotion-sdk#116)")
    print("  " + "-" * 62)
    print(f"  {'stall':>7} | " + " | ".join(f"{n[:9]:>9}" for n, _ in SIGNATURES))
    print("  " + "-" * 62)
    for r in sorted(results, key=lambda r: r.stall_s):
        cells = " | ".join(
            f"{('x' + str(r.hits[n])) if n in r.hits else '-':>9}"
            for n, _ in SIGNATURES
        )
        print(f"  {r.stall_s:>6g}s | {cells}")
    print("  " + "-" * 62)
    print(f"  first queue-full      : {first(lambda r: 'HISTO_QUEUE_FULL' in r.hits)}")
    print(f"  first camera overrun  : {first(lambda r: 'CAMERA_OVERRUN' in r.hits)}")
    print(f"  first false scan end  : {first(lambda r: r.false_scan_end)}   <- sensor-fw#101")
    print(f"  first endpoint loss   : {first(lambda r: 'ENDPOINT_LOST' in r.hits)}")
    print(f"  host warned (>1s put) : {first(lambda r: 'HOST_PUT_WARNED' in r.hits)}")
