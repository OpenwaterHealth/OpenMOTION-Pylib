"""Etch-a-sketch repro (openmotion-sdk#220).

Characterization tests that REPRODUCE the 2026-08-06 incident: an EFT
burst clears the top two bits of one camera's frame_id byte (raw
0xC5 -> 0x05 on the wire) while the packet timestamps stay truthful.
These tests assert the CURRENT (defective) pipeline behavior so the
failure is reproducible on command in pure software:

  - the unwrapper accepts the bogus +64/+65 forward step (epoch bump
    included) and then drops the next ~64 REAL frames as stale;
  - TimestampRepairStage sides with the corrupted frame_id, rewrites
    perfectly good timestamps ~1.6 s into the future, and fabricates
    64 synthetic NaN-fill rows for a gap that never existed;
  - condition 2 (in-packet frame_id disagreement) drags the sibling
    cameras' good frames into the misalignment window;
  - SideAverageStage's realtime path (no frame_type/quality filter)
    emits partial-capture averages, NaN samples, and future-dated
    samples with a non-monotonic time axis -- the bloodflow app's
    "etch-a-sketch" zigzags.

When the #220 fixes land, these assertions are EXPECTED TO FAIL.
Rewrite them to the healthy expectations at that point -- they then
become the regression suite for #220.

Simulation shape (mirrors the incident): side 1 (right), 4 cameras at
40 Hz, raw frame_id starting at 1, batched 10 captures x 4 rows like
LiveUsbSource. The victim is cam 3 (last in packet order); the burst
corrupts captures 198-200 (raw 0xC6..0xC8 -> 0x06..0x08, i.e. the
top-two-bits-cleared signature, which only reads as a forward step
when raw is in 0xC0..0xFF -- same as the field event's 0xC5/0xCA).
"""

import logging

import numpy as np
import pytest

from omotion.pipeline.batch import (
    FrameBatch, LiveEmit, TimestampMisalignmentWindow,
)
from omotion.pipeline.stages.classify import FrameClassificationStage
from omotion.pipeline.stages.side_avg import SideAverageStage
from omotion.pipeline.stages.timestamp_repair import TimestampRepairStage

SIDE = 1                      # right, as in the incident
CAMS = (0, 1, 2, 3)
VICTIM = 3                    # last in packet order, like the field event
PERIOD_S = 0.025              # 40 Hz
N_CAPTURES = 320              # 8 s scan
BURST_CAPTURES = (198, 199, 200)   # raw 0xC6..0xC8 -> corrupted 0x06..0x08
CAPTURES_PER_BATCH = 10

# Distinct per-camera BFI levels so a partial average is numerically
# distinguishable from the true 4-camera average.
CAM_BFI = {0: 1.0, 1: 2.0, 2: 3.0, 3: 6.0}
FULL_AVG = sum(CAM_BFI.values()) / len(CAM_BFI)          # 3.0
PARTIAL_AVG_NO_VICTIM = (1.0 + 2.0 + 3.0) / 3            # 2.0


class _StubBfiStage:
    """Stand-in for the moments->dark->shot-noise->BFI chain.

    Mimics what matters for the repro: real light/dark rows get a
    finite per-camera BFI; stale rows and NaN-fill rows get NaN (in
    the real pipeline stale rows are skipped by DarkCorrectionStage
    and nan_filled rows carry zero histograms -> NaN mean).
    """

    name = "stub_bfi"

    def process(self, batch: FrameBatch) -> FrameBatch:
        n = len(batch.cam_ids)
        bfi = np.full((n, 2, 8), np.nan, dtype=np.float32)
        for i in range(n):
            ftype = str(batch.frame_type[i])
            quality = (str(batch.quality[i])
                       if batch.quality is not None else "ok")
            if ftype in ("light", "dark") and quality != "nan_filled":
                s = int(batch.side_ids[i])
                c = int(batch.cam_ids[i])
                bfi[i, s, c] = CAM_BFI[c]
        batch.bfi_live = bfi
        batch.bvi_live = bfi.copy()
        return batch


def _wire_rows():
    """Yield (cam_id, raw_frame_id, timestamp_s) rows as parsed off the
    wire: 4 cameras per capture sharing one packet timestamp, raw ids
    starting at 1, with the victim's top two frame_id bits cleared
    during the burst captures (timestamps untouched -- that is the
    whole point of the incident)."""
    for capture in range(1, N_CAPTURES + 1):
        t = capture * PERIOD_S
        for cam in CAMS:
            raw = capture & 0xFF
            if cam == VICTIM and capture in BURST_CAPTURES:
                raw &= 0x3F
            yield cam, raw, t


def _run_chain():
    """Push the wire rows through the real classify -> repair ->
    side-average chain in LiveUsbSource-sized batches; collect every
    output row, live_side sample, and diagnostics event."""
    classify = FrameClassificationStage(discard_count=9, dark_interval=600)
    repair = TimestampRepairStage()
    stub = _StubBfiStage()
    side_avg = SideAverageStage(
        enabled=True, left_camera_mask=0x00, right_camera_mask=0x0F)

    rows = list(_wire_rows())
    rows_per_batch = CAPTURES_PER_BATCH * len(CAMS)

    out_rows = []       # (cam, abs_fid, ts, frame_type, quality)
    samples = []        # SideAverageSample in emission order
    windows = []        # TimestampMisalignmentWindow events

    for start in range(0, len(rows), rows_per_batch):
        chunk = rows[start:start + rows_per_batch]
        n = len(chunk)
        batch = FrameBatch(
            cam_ids=np.array([r[0] for r in chunk], dtype=np.int8),
            frame_ids=np.array([r[1] for r in chunk], dtype=np.uint8),
            side_ids=np.full(n, SIDE, dtype=np.int8),
            raw_histograms=np.zeros((n, 2, 8, 1024), dtype=np.uint32),
            temperature_c=np.zeros((n, 2, 8), dtype=np.float32),
            timestamp_s=np.array([r[2] for r in chunk], dtype=np.float64),
            pdc=None, tcm=None, tcl=None,
        )
        batch = classify.process(batch)
        batch = repair.process(batch)
        batch = stub.process(batch)
        batch = side_avg.process(batch)

        for i in range(len(batch.cam_ids)):
            out_rows.append((
                int(batch.cam_ids[i]),
                int(batch.abs_frame_ids[i]),
                float(batch.timestamp_s[i]),
                str(batch.frame_type[i]),
                str(batch.quality[i]),
            ))
        for ev in batch.events:
            if isinstance(ev, LiveEmit) and ev.channel == "live_side":
                samples.append(ev.payload)
            elif isinstance(ev, TimestampMisalignmentWindow):
                windows.append(ev)

    return out_rows, samples, windows


@pytest.fixture(scope="module")
def run():
    return _run_chain()


def test_misalignment_window_matches_incident_signature(run, caplog):
    """One coalesced window spanning ~+64 fids with ~64 NaN-fills --
    the incident log's shape ('frames 1221-1285 ... 60 NaN-filled')."""
    _, _, windows = run
    assert len(windows) == 1, windows
    w = windows[0]
    assert w.side == SIDE
    # onset is the first flagged sibling (real fid), end is the
    # victim's corrupted fid one epoch up -- the +64 span fingerprint.
    assert w.onset_fid == BURST_CAPTURES[0]
    assert w.end_fid - w.onset_fid >= 64
    # 3 corrupted victim rows + 9 innocent sibling rows (condition 2).
    assert w.n_corrected == 12
    # 64 fabricated rows for a gap that never existed.
    assert w.n_nan == 64


def test_unwrapper_drops_64_real_frames_as_stale(run):
    """After accepting the bogus jump, the victim's next 64 REAL frames
    are rejected as stale -- 1.6 s of genuine data discarded."""
    out_rows, _, _ = run
    stale = [r for r in out_rows if r[3] == "stale"]
    assert len(stale) == 64
    assert all(r[0] == VICTIM for r in stale)


def test_good_timestamps_rewritten_into_the_future(run):
    """The victim's corrupted-fid frames had TRUTHFUL timestamps
    (~4.95-5.00 s); the 'repair' moved them ~1.6 s into the future."""
    out_rows, _, _ = run
    victim_corrected = [r for r in out_rows
                        if r[0] == VICTIM and r[4] == "ts_corrected"]
    assert len(victim_corrected) == 3
    assert max(r[2] for r in victim_corrected) > 6.5   # device time was ~5.0

    # And 9 innocent sibling frames were dragged in by condition 2.
    sibling_corrected = [r for r in out_rows
                         if r[0] != VICTIM and r[4] == "ts_corrected"]
    assert len(sibling_corrected) == 9


def test_synthetic_rows_fabricated_for_phantom_gap(run):
    """64 nan_filled rows typed 'light' are inserted for frame ids the
    camera never skipped, timestamped across the fabricated 1.6 s."""
    out_rows, _, _ = run
    fills = [r for r in out_rows if r[4] == "nan_filled"]
    assert len(fills) == 64
    assert all(r[0] == VICTIM for r in fills)
    assert all(r[3] == "light" for r in fills)
    fill_ts = [r[2] for r in fills]
    assert max(fill_ts) > 6.0    # sweeps into the fabricated future


def test_live_side_trace_zigzags(run):
    """The realtime side-average stream -- what the app plots -- shows
    the three zigzag ingredients: partial-capture averages, a
    future-dated sample, and a non-monotonic time axis."""
    _, samples, _ = run
    assert len(samples) > N_CAPTURES + 100   # ~2x emission during blackout

    healthy = [s.bfi for s in samples if 1.0 < s.t < 4.8]
    assert healthy and all(abs(b - FULL_AVG) < 1e-6 for b in healthy)

    # During the stale blackout the victim never contributes, and its
    # interleaved bogus-fid rows chop each real capture into partial
    # emissions: 3-camera averages where 4 cameras are enabled.
    partial = [s for s in samples
               if np.isfinite(s.bfi)
               and abs(s.bfi - PARTIAL_AVG_NO_VICTIM) < 1e-6]
    assert len(partial) >= 60

    # The re-timestamped victim frame surfaces as a single-camera
    # "side average" a second and a half in the future.
    future = [s for s in samples if s.t > 6.4 and np.isfinite(s.bfi)]
    assert any(abs(s.bfi - CAM_BFI[VICTIM]) < 1e-6 for s in future)

    # Non-monotonic time axis: after sweeping to ~6.5 s the stream
    # jumps back to real time (~5.0 s) -- the plot draws backwards.
    ts = [s.t for s in samples]
    max_backjump = max(
        (ts[i - 1] - ts[i] for i in range(1, len(ts))), default=0.0)
    assert max_backjump > 1.0
