"""Unit coverage for the USB-drain-stall HIL harness (openmotion-sdk#116).

The harness in test_usb_drain_stall.py only earns bench time if its
classifier actually recognises the firmware printf it will be reading.
Every line quoted below is verbatim from the bloodflow-app#300 field log
(sensor FW 1.8.1-rc.2), including the multi-line records — CommInterface
emits several newline-separated firmware lines per log record, which is
exactly where a line-oriented matcher would silently miss the escalation.
"""

import logging
import time

import pytest

from test_usb_drain_stall import (
    SIGNATURES,
    StallResult,
    StallSink,
    _SignatureCollector,
)

pytestmark = pytest.mark.unit


# Verbatim from open-motion-20260702_104931.log
FIELD_QUEUE_FULL = (
    "[LEFT-COMM PRINTF] HISTO enqueue fail: queue full "
    "(count=4 size=4 enq=393 deq=389 datain=10842631 txfail=0)"
)
FIELD_ESCALATION = (
    "[LEFT-COMM PRINTF] USBD_HISTO_SendData failed: 3\n"
    "COMM USB TX Timeout\n"
    "COMM USB TX Timeout\n"
    "Error in USART2: Overrun error \n"
    "failed to setup receive for Camera 1 channel\n"
    "Error in USART3: Overrun error \n"
    "HISTO enqueue fail: queue full (count=4 size=4 enq=393 de"
)
FIELD_SCAN_FINISHED = "[RIGHT-COMM PRINTF] Scan finished (741352 ms, 29600 frames)"
FIELD_SPURIOUS_CYCLE = "[LEFT-COMM PRINTF] Scan finished (194 ms, 2 frames)"
FIELD_SPI_OVERRUN = "[LEFT-COMM PRINTF] Error in SPI2: Overrun error"
FIELD_ENDPOINT_LOST = "RIGHT-HISTO stream error (device lost): [Errno 32] Pipe error"
FIELD_HOST_PUT_WARN = (
    "LEFT-HISTO: data_queue full for >1s during streaming "
    "(parser falling behind?); dropping 32837-byte chunk"
)

# Benign traffic that must NOT be classified as failure
FIELD_BENIGN = (
    "[LEFT-COMM PRINTF] Scan started",
    "[LEFT-COMM PRINTF] HISTO flush(start): q=0 ep=0 e-d=0",
    "[RIGHT-COMM PRINTF] Enabled Power for Camera 1",
    "[LEFT-COMM PRINTF] [DIAG] overruns c1-c8: 0 0 0 0 0 0 0 0",
    "LEFT-HISTO: Streaming started",
)


def _feed(collector, *messages):
    log = logging.getLogger("openmotion.sdk.test_harness")
    log.addHandler(collector)
    log.setLevel(logging.DEBUG)
    log.propagate = False
    try:
        for msg in messages:
            log.warning("%s", msg)
    finally:
        log.removeHandler(collector)


@pytest.fixture
def collector():
    return _SignatureCollector()


def test_classifies_queue_full(collector):
    _feed(collector, FIELD_QUEUE_FULL)
    assert collector.hits == {"HISTO_QUEUE_FULL": 1}


def test_multiline_record_hits_every_stage(collector):
    """One CommInterface record carrying several firmware lines must be
    credited to each stage it contains — the field log's records look
    exactly like this, and a line-oriented matcher would drop most of them."""
    _feed(collector, FIELD_ESCALATION)
    assert collector.hits["HISTO_QUEUE_FULL"] == 1
    assert collector.hits["USB_TX_BLOCKED"] == 1
    assert collector.hits["CAMERA_OVERRUN"] == 1
    assert "FALSE_SCAN_END" not in collector.hits


def test_spi_overrun_counts_as_camera_overrun(collector):
    """L7's dropout came through SPI2, not a USART — both are the same stage."""
    _feed(collector, FIELD_SPI_OVERRUN)
    assert collector.hits == {"CAMERA_OVERRUN": 1}


def test_scan_finished_captures_duration(collector):
    _feed(collector, FIELD_SCAN_FINISHED, FIELD_SPURIOUS_CYCLE)
    assert collector.hits["FALSE_SCAN_END"] == 2
    assert collector.scan_finished_ms == [741352, 194]


def test_endpoint_lost_and_host_warning(collector):
    _feed(collector, FIELD_ENDPOINT_LOST, FIELD_HOST_PUT_WARN)
    assert collector.hits["ENDPOINT_LOST"] == 1
    assert collector.hits["HOST_PUT_WARNED"] == 1


def test_benign_traffic_is_not_classified(collector):
    _feed(collector, *FIELD_BENIGN)
    assert collector.hits == {}


def test_reset_clears_state(collector):
    _feed(collector, FIELD_QUEUE_FULL, FIELD_SCAN_FINISHED)
    collector.reset()
    assert collector.hits == {} and collector.scan_finished_ms == []


def test_first_seen_is_recorded_once(collector):
    _feed(collector, FIELD_QUEUE_FULL)
    first = collector.first_seen["HISTO_QUEUE_FULL"]
    time.sleep(0.01)
    _feed(collector, FIELD_QUEUE_FULL)
    assert collector.hits["HISTO_QUEUE_FULL"] == 2
    assert collector.first_seen["HISTO_QUEUE_FULL"] == first


def test_every_signature_has_a_field_example():
    """Guard against adding a signature with no known-good sample."""
    covered = _SignatureCollector()
    _feed(covered, FIELD_ESCALATION, FIELD_SCAN_FINISHED,
          FIELD_ENDPOINT_LOST, FIELD_HOST_PUT_WARN)
    assert set(covered.hits) == {name for name, _ in SIGNATURES}


# --------------------------------------------------------------------------
# StallSink
# --------------------------------------------------------------------------

def test_stall_sink_only_stalls_on_every_nth_batch():
    sink = StallSink(stall_s=0.05, every_n=4)
    sink.on_scan_start(None)

    t0 = time.perf_counter()
    for _ in range(3):
        sink.consume("live", object())
    assert sink.stalls == 0
    assert time.perf_counter() - t0 < 0.04, "stalled before the Nth batch"

    sink.consume("live", object())
    assert sink.stalls == 1
    assert time.perf_counter() - t0 >= 0.05


def test_stall_sink_counters_reset_between_scans():
    sink = StallSink(stall_s=0.0, every_n=2)
    sink.on_scan_start(None)
    for _ in range(4):
        sink.consume("live", object())
    assert (sink.batches, sink.stalls) == (4, 2)
    sink.on_scan_start(None)
    assert (sink.batches, sink.stalls) == (0, 0)


def test_stall_sink_declares_only_the_live_channel():
    """'live' fires once per batch (~0.25 s); 'final' only on interval close
    (~15 s), which is too coarse to sweep a 40 s scan with."""
    assert StallSink(0.0).channels == frozenset({"live"})


# --------------------------------------------------------------------------
# StallResult
# --------------------------------------------------------------------------

def test_false_scan_end_ignores_a_normal_completion():
    r = StallResult(stall_s=0.1, stalls_injected=8,
                    scan_finished_ms=[40_120], commanded_sec=40)
    assert r.false_scan_end is False


def test_false_scan_end_flags_an_early_finish():
    r = StallResult(stall_s=2.0, stalls_injected=8,
                    scan_finished_ms=[21_500], commanded_sec=40)
    assert r.false_scan_end is True


def test_false_scan_end_flags_the_spurious_restart_cycles():
    """The field failure's signature: a plausible finish followed by dozens
    of millisecond-long ones."""
    r = StallResult(stall_s=2.0, stalls_injected=8,
                    scan_finished_ms=[39_900, 194, 430], commanded_sec=40)
    assert r.false_scan_end is True
