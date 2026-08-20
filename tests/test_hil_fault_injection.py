"""HIL fault-injection verification (sdk#220 / sensor-fw#123).

Runs each deterministic firmware fault mode against a real sensor and
asserts the SDK produces the evidence the contract promises
(sensor-fw docs/superpowers/specs/2026-08-11-histogram-fault-injection-
design.md):

  0x0800 fid_single       -> FrameIdConsensusCorrection
  0x1000 fid_multi        -> FrameIdPacketAnomaly
  0x2000 timestamp_freeze -> TimestampRepairInputAnomaly
  0x4000 packet_drop      -> FrameGapFillAnomaly

Per the design, the wire capture is parsed with parse_histogram_stream
and the rows are fed through FrameClassificationStage and
TimestampRepairStage directly — no full ScanWorkflow, so the assertions
observe exactly the two stages under test.

Requirements: the sensor must run a DEBUG build of sensor-fw carrying
the fault-injection matrix (sensor-fw PR #124). Stock or Release
firmware stores the flag bits but injects nothing, which fails the
evidence assertions with a pointed message rather than skipping —
a silently-inert fault matrix is itself a finding. Frame-id modes fire
at the first frame with raw id in 0xC0..0xFF (~5 s in); timing modes
fire at frame 80 (~2 s in).
"""

import queue
import threading
import time

import numpy as np
import pytest

from omotion.MotionProcessing import (
    EXPECTED_HISTOGRAM_SUMS, HISTOGRAM_BYTES, parse_histogram_stream,
)
from omotion.config import (
    DEBUG_FLAG_FID_CORRUPT, DEBUG_FLAG_FID_CORRUPT_MULTI,
    DEBUG_FLAG_HISTO_DROP_ONCE, DEBUG_FLAG_TIMESTAMP_FREEZE,
)
from omotion.pipeline.batch import (
    FrameBatch, FrameGapFillAnomaly, FrameIdConsensusCorrection,
    FrameIdPacketAnomaly, TimestampRepairInputAnomaly,
)
from omotion.pipeline.stages.classify import FrameClassificationStage
from omotion.pipeline.stages.timestamp_repair import TimestampRepairStage

pytestmark = [pytest.mark.sensor, pytest.mark.slow]

# 4 cameras — consensus needs >= 3 streaming, and fid_multi needs exactly
# a 2-2 tie, so all four must deliver parseable frames. Cameras 1-4 (mask
# 0x1E): the bench right module's cam 0 ships a 32x-accumulated histogram
# every 32nd frame, which the parser's sum check drops, and a 3-camera
# packet turns fid_multi's tie into a corrupt majority.
MASK = 0x1E
CAPTURE_S = {                     # stream long enough for the mode to fire
    DEBUG_FLAG_FID_CORRUPT: 8.0,          # raw must reach 0xC0 (~5 s)
    DEBUG_FLAG_FID_CORRUPT_MULTI: 8.0,
    DEBUG_FLAG_TIMESTAMP_FREEZE: 4.0,     # frame 80 (~2 s)
    DEBUG_FLAG_HISTO_DROP_ONCE: 4.0,
}


@pytest.fixture(scope="module")
def hil_sensor(sensor_right):
    """Bring the right module's cameras up once for all fault modes
    (power -> program FPGA -> configure registers, the ScanWorkflow
    sequence). The right module is the bench unit carrying the
    fault-matrix firmware."""
    s = sensor_right
    if s.enable_camera_power(MASK) is False:
        pytest.fail(f"enable_camera_power(0x{MASK:02X}) returned False")
    time.sleep(0.5)
    if s.program_fpga(camera_position=MASK, manual_process=False) is False:
        pytest.fail(f"program_fpga(0x{MASK:02X}) returned False")
    time.sleep(0.1)
    if s.camera_configure_registers(MASK) is False:
        pytest.fail(f"camera_configure_registers(0x{MASK:02X}) returned False")
    yield s
    s.set_debug_flags(0)


def _capture_rows(sensor, flag: int, duration_s: float):
    """Arm one fault mode, stream laser-less for duration_s, and return
    the parsed rows [(cam_id, raw_fid, ts, ...)] in wire order."""
    assert sensor.set_debug_flags(flag), "set_debug_flags failed"
    q: queue.Queue = queue.Queue()
    sensor.uart.histo.flush_stale_data(expected_size=HISTOGRAM_BYTES)
    sensor.uart.histo.start_streaming(q, expected_size=HISTOGRAM_BYTES)
    try:
        assert sensor.enable_camera(MASK), "enable_camera failed"
        assert sensor.enable_aggregator_fsin(), "enable_aggregator_fsin failed"
        time.sleep(duration_s)
    finally:
        sensor.disable_aggregator_fsin()
        sensor.disable_camera(MASK)
        time.sleep(0.3)
        sensor.uart.histo.stop_streaming()
        sensor.set_debug_flags(0)

    rows = []
    stop_evt = threading.Event()
    stop_evt.set()      # parse until the queue drains, then return
    parse_histogram_stream(
        q, stop_evt, bytearray(),
        on_row_fn=lambda cam, fid, ts, hist, row_sum, temp:
            rows.append((cam, fid, ts)),
        expected_row_sum=EXPECTED_HISTOGRAM_SUMS,
    )
    return rows


def _run_stages(rows):
    """Feed parsed rows through classify + repair in capture-aligned
    batches (a packet's rows never straddle a batch boundary, matching
    LiveUsbSource's per-capture ordering) and collect the evidence."""
    classify = FrameClassificationStage()
    repair = TimestampRepairStage()
    events = []

    batches, current, current_ts, captures = [], [], None, 0
    for row in rows:
        if row[2] != current_ts:
            current_ts = row[2]
            captures += 1
            if captures > 10 and current:
                batches.append(current)
                current, captures = [], 1
        current.append(row)
    if current:
        batches.append(current)

    for chunk in batches:
        n = len(chunk)
        batch = FrameBatch(
            cam_ids=np.array([r[0] for r in chunk], dtype=np.int8),
            frame_ids=np.array([r[1] for r in chunk], dtype=np.uint8),
            side_ids=np.full(n, 1, dtype=np.int8),
            raw_histograms=np.zeros((n, 2, 8, 1024), dtype=np.uint32),
            temperature_c=np.zeros((n, 2, 8), dtype=np.float32),
            timestamp_s=np.array([r[2] for r in chunk], dtype=np.float64),
            pdc=None, tcm=None, tcl=None,
        )
        batch = classify.process(batch)
        batch = repair.process(batch)
        events.extend(batch.events)
    return events


def _evidence(sensor, flag, event_type):
    rows = _capture_rows(sensor, flag, CAPTURE_S[flag])
    assert len(rows) > 100, f"only {len(rows)} rows streamed — rig problem"
    n_cams = len({r[0] for r in rows})
    if flag in (DEBUG_FLAG_FID_CORRUPT, DEBUG_FLAG_FID_CORRUPT_MULTI) and n_cams < 3:
        pytest.skip(f"only {n_cams} camera(s) streaming — consensus needs >= 3")
    events = _run_stages(rows)
    matched = [e for e in events if isinstance(e, event_type)]
    assert matched, (
        f"no {event_type.__name__} evidence for flag 0x{flag:X} — "
        f"is the sensor running a Debug build of sensor-fw PR #124? "
        f"(events seen: {[type(e).__name__ for e in events]})")
    return matched, events


def test_fid_single_yields_consensus_correction(hil_sensor):
    matched, events = _evidence(
        hil_sensor, DEBUG_FLAG_FID_CORRUPT, FrameIdConsensusCorrection)
    # The injected mutation clears the top two bits of one camera's id.
    for c in matched:
        assert c.corrected_frame_id & 0x3F == c.wire_frame_id
    # Consensus repair means no packet was left ambiguous.
    assert not [e for e in events if isinstance(e, FrameIdPacketAnomaly)]


def test_fid_multi_yields_packet_anomaly(hil_sensor):
    matched, events = _evidence(
        hil_sensor, DEBUG_FLAG_FID_CORRUPT_MULTI, FrameIdPacketAnomaly)
    assert all(len(set(a.frame_ids)) > 1 for a in matched)


def test_timestamp_freeze_yields_repair_input_anomaly(hil_sensor):
    matched, _ = _evidence(
        hil_sensor, DEBUG_FLAG_TIMESTAMP_FREEZE, TimestampRepairInputAnomaly)
    assert all(a.n_frames >= 1 for a in matched)


def test_packet_drop_yields_gap_fill_anomaly(hil_sensor):
    matched, _ = _evidence(
        hil_sensor, DEBUG_FLAG_HISTO_DROP_ONCE, FrameGapFillAnomaly)
    # One dropped packet -> a 1-frame gap on every streaming camera.
    assert all(a.n_filled >= 1 for a in matched)
