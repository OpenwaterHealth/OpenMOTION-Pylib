"""Regression for issue #220's one-camera frame-ID corruption cascade."""

import numpy as np

from omotion.pipeline.batch import FrameBatch, LiveEmit
from omotion.pipeline.stages.classify import FrameClassificationStage
from omotion.pipeline.stages.side_avg import SideAverageStage
from omotion.pipeline.stages.timestamp_repair import TimestampRepairStage


def _batch(rows):
    n = len(rows)
    return FrameBatch(
        cam_ids=np.array([row[0] for row in rows], dtype=np.int8),
        frame_ids=np.array([row[1] for row in rows], dtype=np.uint8),
        side_ids=np.ones(n, dtype=np.int8),
        raw_histograms=np.zeros((n, 2, 8, 1024), dtype=np.uint32),
        temperature_c=np.zeros((n, 2, 8), dtype=np.float32),
        timestamp_s=np.array([row[2] for row in rows], dtype=np.float64),
        pdc=None, tcm=None, tcl=None,
    )


def _stamp_bfi(batch):
    levels = (1.0, 2.0, 3.0, 6.0)
    values = np.full((len(batch.cam_ids), 2, 8), np.nan, dtype=np.float32)
    for i, cam_id in enumerate(batch.cam_ids):
        if batch.frame_type[i] not in ("warmup", "stale"):
            values[i, 1, int(cam_id)] = levels[int(cam_id)]
    batch.bfi_live = values
    batch.bvi_live = values.copy()


def test_issue_220_single_bad_wire_id_does_not_destroy_the_scan():
    classify = FrameClassificationStage()
    repair = TimestampRepairStage()
    side_average = SideAverageStage(
        enabled=True, left_camera_mask=0, right_camera_mask=0x0F,
    )
    corrections = []
    output_rows = []
    samples = []

    wire_rows = []
    for capture in range(1, 221):
        for cam_id in range(4):
            raw_id = capture & 0xFF
            if capture == 198 and cam_id == 3:
                raw_id &= 0x3F  # 0xC6 -> 0x06, the production signature
            wire_rows.append((cam_id, raw_id, capture * 0.025))

    for start in range(0, len(wire_rows), 40):
        batch = classify.process(_batch(wire_rows[start:start + 40]))
        batch = repair.process(batch)
        _stamp_bfi(batch)
        side_average.process(batch)

        corrections.extend(e for e in batch.events
                           if type(e).__name__ == "FrameIdConsensusCorrection")
        samples.extend(e.payload for e in batch.events
                       if isinstance(e, LiveEmit) and e.channel == "live_side")
        output_rows.extend(zip(
            batch.cam_ids.tolist(), batch.frame_ids.tolist(),
            batch.abs_frame_ids.tolist(), batch.frame_type.tolist(),
            batch.quality.tolist(), batch.timestamp_s.tolist(),
        ))

    flush = _batch([])
    side_average.on_scan_stop(flush)
    samples.extend(e.payload for e in flush.events
                   if isinstance(e, LiveEmit) and e.channel == "live_side")

    assert len(corrections) == 1
    assert corrections[0].observed_raw_frame_id == 6
    assert corrections[0].consensus_raw_frame_id == 198
    corrupt_row = next(
        row for row in output_rows
        if row[0] == 3 and row[1] == 6 and np.isclose(row[5], 198 * 0.025)
    )
    assert corrupt_row[2:5] == (198, "light", "ok")
    assert not any(row[3] == "stale" for row in output_rows)
    assert not any(row[4] in ("ts_corrected", "nan_filled")
                   for row in output_rows)

    assert len(samples) == 220
    assert np.all(np.diff([sample.t for sample in samples]) > 0)
    finite_bfi = [sample.bfi for sample in samples if np.isfinite(sample.bfi)]
    assert finite_bfi and np.allclose(finite_bfi, 3.0)
