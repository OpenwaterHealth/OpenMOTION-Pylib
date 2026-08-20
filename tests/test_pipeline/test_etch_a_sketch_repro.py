"""Etch-a-sketch regression suite (openmotion-sdk#220).

The 2026-08-06 field event corrupted one camera's frame_id byte
(top two bits cleared, raw 0xC5 -> 0x05) while the packet timestamps
stayed truthful. The pipeline used to treat the frame_id as ground
truth and amplify the one-byte lie: the unwrapper accepted the bogus
+64 step then dropped the next 64 REAL frames as stale,
TimestampRepairStage rewrote truthful timestamps ~1.6 s into the
future and fabricated ~60 synthetic rows, condition 2 condemned the
innocent packet-mates, and the realtime side-average stream fed the
app partial averages on a non-monotonic time axis — the
"etch-a-sketch" zigzags.

This file started as the characterization repro asserting that broken
behavior (see its history for the defect signature). With the #220
fixes it asserts the HEALTHY contract for the same wire stream:

  - a frame whose counter step is inconsistent with the clock is
    quarantined (one frame lost per corrupted byte, nothing else);
  - timestamps that were truthful are never rewritten;
  - the only synthetic rows are honest per-gap placeholders;
  - the live_side stream stays monotonic and full-rate;
  - sustained corruption (hits landing all scan long) produces no
    misalignment windows and no warning flood.

Simulation shape (mirrors the incident): side 1 (right), 4 cameras at
40 Hz, raw frame_id starting at 1, batched 10 captures x 4 rows like
LiveUsbSource. The victim is cam 3 (last in packet order); the burst
corrupts captures 198-200 (raw 0xC6..0xC8 -> 0x06..0x08 — the
top-two-bits-cleared signature).
"""

import random

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
WARMUP = 9                    # classification discard_count

# Distinct per-camera BFI levels so a partial average is numerically
# distinguishable from the true 4-camera average.
CAM_BFI = {0: 1.0, 1: 2.0, 2: 3.0, 3: 6.0}
FULL_AVG = sum(CAM_BFI.values()) / len(CAM_BFI)          # 3.0
PARTIAL_AVG_NO_VICTIM = (1.0 + 2.0 + 3.0) / 3            # 2.0


class _StubBfiStage:
    """Stand-in for the moments->dark->shot-noise->BFI chain.

    Real light/dark rows get a finite per-camera BFI; stale rows and
    nan_filled rows get NaN (in the real pipeline stale rows are skipped
    by DarkCorrectionStage and nan_filled rows carry zero histograms).
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


def _burst_rows():
    """The incident stream: the victim's frame_id byte loses its top two
    bits for 3 consecutive captures; timestamps stay truthful."""
    rows = []
    for capture in range(1, N_CAPTURES + 1):
        t = capture * PERIOD_S
        for cam in CAMS:
            raw = capture & 0xFF
            if cam == VICTIM and capture in BURST_CAPTURES:
                raw &= 0x3F
            rows.append((cam, raw, t))
    return rows, len(BURST_CAPTURES)


def _sustained_rows(p_corrupt: float = 0.10, seed: int = 56):
    """Sustained in-flight corruption: every camera has an independent
    per-frame chance of the same corruption, all scan long. Returns the
    rows and the number of hits that actually changed the byte (hits on
    raw < 0x40 are no-ops and invisible by construction)."""
    rng = random.Random(seed)
    rows = []
    n_effective = 0
    for capture in range(1, N_CAPTURES + 1):
        t = capture * PERIOD_S
        for cam in CAMS:
            raw = capture & 0xFF
            if rng.random() < p_corrupt:
                corrupted = raw & 0x3F
                if corrupted != raw:
                    n_effective += 1
                raw = corrupted
            rows.append((cam, raw, t))
    return rows, n_effective


def _run_chain(rows):
    """Push wire rows through the real classify -> repair -> side-average
    chain in LiveUsbSource-sized batches; collect every output row,
    live_side sample, and diagnostics event."""
    classify = FrameClassificationStage(discard_count=WARMUP, dark_interval=600)
    repair = TimestampRepairStage()
    stub = _StubBfiStage()
    side_avg = SideAverageStage(
        enabled=True, left_camera_mask=0x00, right_camera_mask=0x0F)

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
def burst_run():
    rows, n_corrupted = _burst_rows()
    return _run_chain(rows) + (n_corrupted,)


@pytest.fixture(scope="module")
def sustained_run():
    rows, n_effective = _sustained_rows()
    return _run_chain(rows) + (n_effective,)


def test_corrupt_frames_quarantined_one_for_one(burst_run):
    """A frame whose counter claims +65 frames in 25 ms is quarantined —
    each corrupted byte costs exactly its own frame, and the unwrapper
    resumes on the victim's next honest frame instead of discarding the
    following 64 real ones."""
    out_rows, _, _, n_corrupted = burst_run
    stale = [r for r in out_rows if r[3] == "stale"]
    assert len(stale) == n_corrupted           # was 64 before the fix
    assert all(r[0] == VICTIM for r in stale)

    # The victim's accepted post-warmup sequence skips only the corrupted
    # captures (warmup frames are typed "warmup", not light/dark).
    victim_abs = [r[1] for r in out_rows
                  if r[0] == VICTIM and r[3] in ("light", "dark")
                  and r[4] != "nan_filled"]
    assert (set(range(WARMUP + 1, N_CAPTURES + 1)) - set(victim_abs)
            == set(BURST_CAPTURES))


def test_truthful_timestamps_never_rewritten(burst_run):
    """No frame is re-timestamped and no misalignment window opens: the
    timestamps were honest, and with the corrupt counter quarantined
    upstream there is nothing left to 'repair'."""
    out_rows, _, windows, _ = burst_run
    assert windows == []
    assert all(r[4] != "ts_corrected" for r in out_rows)
    # Every surviving real row keeps its wire timestamp: t = abs_fid x 25 ms.
    for cam, abs_fid, ts, ftype, quality in out_rows:
        if ftype in ("light", "dark") and quality == "ok":
            assert abs(ts - abs_fid * PERIOD_S) < 1e-9


def test_gap_backfilled_with_honest_placeholders(burst_run):
    """The quarantined captures leave a 3-frame gap, backfilled with
    nan_filled placeholders whose timestamps interpolate the REAL gap —
    not a fabricated 1.6 s future (was 64 future-dated rows)."""
    out_rows, _, _, n_corrupted = burst_run
    fills = [r for r in out_rows if r[4] == "nan_filled"]
    assert len(fills) == n_corrupted
    assert {r[1] for r in fills} == set(BURST_CAPTURES)
    for _, abs_fid, ts, _, _ in fills:
        assert abs(ts - abs_fid * PERIOD_S) < PERIOD_S  # inside the real gap


def test_innocent_siblings_left_alone(burst_run):
    """Condition 2 no longer condemns the packet-mates: with the corrupt
    rows quarantined before the repair stage, the siblings' frames pass
    through untouched (was 9 innocent frames rewritten)."""
    out_rows, _, _, _ = burst_run
    sibling_rows = [r for r in out_rows if r[0] != VICTIM]
    assert all(r[4] in ("ok", "nan_filled") for r in sibling_rows)
    assert all(r[4] == "ok" for r in sibling_rows
               if r[3] in ("light", "dark"))


def test_live_side_trace_stays_sane(burst_run):
    """The stream the app plots: one sample per capture, monotonic time
    axis, full-rate averages — with an honest 3-camera partial average
    for the three captures whose victim frame was quarantined (that is
    what a genuinely missing camera should look like)."""
    _, samples, _, _ = burst_run
    # One sample per capture after warmup; the final capture stays open
    # (no on_scan_stop in this harness).
    assert len(samples) == N_CAPTURES - WARMUP - 1

    ts = [s.t for s in samples]
    assert all(b >= a for a, b in zip(ts, ts[1:]))      # was 1.6 s backjumps

    for s in samples:
        assert np.isfinite(s.bfi)
        expected = (PARTIAL_AVG_NO_VICTIM
                    if s.frame_id in BURST_CAPTURES else FULL_AVG)
        assert abs(s.bfi - expected) < 1e-6


def test_sustained_corruption_no_flood(sustained_run):
    """A continuous burst train (10%/cam/frame, whole scan) — the field
    'kill the session' presentation — now degrades gracefully: every
    effective hit costs its own frame, no misalignment windows open, no
    frames are re-timestamped, and the time axis never runs backwards.
    (Before the fix: 157 windows, ~2.6 WARNINGs/s, half the scan
    fabricated or discarded.)"""
    out_rows, samples, windows, n_effective = sustained_run
    assert n_effective > 50                      # the scenario has teeth
    assert windows == []
    assert all(r[4] != "ts_corrected" for r in out_rows)

    stale = [r for r in out_rows if r[3] == "stale"]
    assert len(stale) == n_effective             # one-for-one, no amplification

    ts = [s.t for s in samples]
    assert all(b >= a for a, b in zip(ts, ts[1:]))
    # Nearly every capture still yields a sample (a capture disappears
    # only if all four cameras were hit at once — vanishingly rare).
    assert len(samples) >= N_CAPTURES - WARMUP - 1 - 3
