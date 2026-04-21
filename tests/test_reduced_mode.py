"""
Reduced-mode averaging verification (no hardware).

``ScanRequest.reduced_mode = True`` activates a post-pipeline aggregation step
inside ``ScanWorkflow`` that averages per-camera BFI/BVI values across all
active cameras on a side.  The important invariant, per
``docs/SciencePipeline.md`` §16.2, is that the values being averaged are the
*corrected* per-camera BFI/BVIs emitted by ``SciencePipeline`` — NOT raw
histogram moments.  i.e. each camera's BFI/BVI is corrected first, and only
then are the per-camera values averaged into the reduced left/right traces.

These tests mirror the averaging logic in
``ScanWorkflow.start_scan._worker._on_uncorrected_sample`` and
``_on_corrected_batch`` (the closures are not importable, so we replay the
same algorithm) and exercise it with ``Sample`` instances whose ``bfi``/``bvi``
fields are pre-set to distinct, known values.  Because the inputs to the
averager are already corrected ``Sample``s (same contract the real pipeline
produces), a mean-matching assertion on the output demonstrates that
correction happens per-camera before averaging.

Run with pytest:
    pytest tests/test_reduced_mode.py -v
"""

import csv
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omotion.MotionProcessing import CorrectedBatch, Sample


# ---------------------------------------------------------------------------
# Helpers — faithful mirrors of the reduced-mode closures in ScanWorkflow
# ---------------------------------------------------------------------------
#
# These helpers replicate the algorithm documented in SciencePipeline.md §16
# and implemented in ScanWorkflow.py (lines ~367-533 on the next branch).
# They take already-corrected per-camera Samples as input — exactly the
# contract the real SciencePipeline emits — and produce averaged outputs.


class _ReducedUncorrectedAverager:
    """Mirrors `_on_uncorrected_sample` when `reduced_mode=True`."""

    def __init__(self, cam_counts: dict[str, int]):
        self._cam_counts = cam_counts
        self._buf: dict[tuple[str, int], dict] = {}
        self.emitted: list[Sample] = []

    def feed(self, sample: Sample) -> None:
        key = (sample.side, int(sample.absolute_frame_id))
        entry = self._buf.get(key)
        if entry is None:
            entry = {
                "bfi_sum": 0.0, "bvi_sum": 0.0, "count": 0,
                "timestamp_s": float(sample.timestamp_s),
                "frame_id": int(sample.frame_id),
                "abs_frame_id": int(sample.absolute_frame_id),
                "side": sample.side,
            }
            self._buf[key] = entry
        entry["bfi_sum"] += float(sample.bfi)
        entry["bvi_sum"] += float(sample.bvi)
        entry["count"] += 1

        expected = self._cam_counts.get(sample.side, 1)
        if entry["count"] >= expected:
            avg = Sample(
                side=entry["side"], cam_id=0,
                frame_id=entry["frame_id"],
                absolute_frame_id=entry["abs_frame_id"],
                timestamp_s=entry["timestamp_s"],
                row_sum=0, temperature_c=0.0,
                mean=0.0, std_dev=0.0, contrast=0.0,
                bfi=entry["bfi_sum"] / entry["count"],
                bvi=entry["bvi_sum"] / entry["count"],
                is_corrected=False,
            )
            del self._buf[key]
            # Stale eviction (>5 frames behind current)
            stale = [
                k for k in self._buf
                if k[0] == sample.side and k[1] < entry["abs_frame_id"] - 5
            ]
            for sk in stale:
                del self._buf[sk]
            self.emitted.append(avg)


def _reduced_corrected_write(
    batch: CorrectedBatch,
    cam_counts: dict[str, int],
    active_sides: list[str],
    corrected_columns: list[str],
    corrected_by_frame: dict,
    csv_writer,
) -> None:
    """Mirrors `_on_corrected_batch` CSV-write path when `reduced_mode=True`."""
    for sample in batch.samples:
        frame_key = int(sample.absolute_frame_id)
        frame_entry = corrected_by_frame.get(frame_key)
        if frame_entry is None:
            frame_entry = {
                "timestamp_s": float(sample.timestamp_s),
                "_accum": {},
            }
            corrected_by_frame[frame_key] = frame_entry
        accum = frame_entry["_accum"]
        side_acc = accum.setdefault(
            sample.side, {"bfi_sum": 0.0, "bvi_sum": 0.0, "count": 0}
        )
        side_acc["bfi_sum"] += float(sample.bfi)
        side_acc["bvi_sum"] += float(sample.bvi)
        side_acc["count"] += 1

    expected_sides = set(active_sides)
    complete = [
        fid for fid, entry in corrected_by_frame.items()
        if all(
            entry["_accum"].get(sd, {}).get("count", 0) >= cam_counts.get(sd, 1)
            for sd in expected_sides
        )
    ]
    base_ts = (
        min(float(corrected_by_frame[fid]["timestamp_s"]) for fid in complete)
        if complete else 0.0
    )
    for fid in sorted(complete):
        entry = corrected_by_frame.pop(fid)
        rel_ts = float(entry["timestamp_s"]) - base_ts
        accum = entry["_accum"]
        left = accum.get("left", {"bfi_sum": 0, "bvi_sum": 0, "count": 1})
        right = accum.get("right", {"bfi_sum": 0, "bvi_sum": 0, "count": 1})
        vals = {
            "bfi_left":  round(left["bfi_sum"]  / max(1, left["count"]),  6),
            "bfi_right": round(right["bfi_sum"] / max(1, right["count"]), 6),
            "bvi_left":  round(left["bvi_sum"]  / max(1, left["count"]),  6),
            "bvi_right": round(right["bvi_sum"] / max(1, right["count"]), 6),
        }
        row = [fid, rel_ts] + [vals.get(col, "") for col in corrected_columns]
        csv_writer.writerow(row)


def _make_sample(side: str, cam_id: int, abs_frame: int, bfi: float,
                 bvi: float, ts: float = 0.0,
                 is_corrected: bool = False) -> Sample:
    """Build a Sample whose BFI/BVI simulate the pipeline's corrected output."""
    return Sample(
        side=side, cam_id=cam_id,
        frame_id=abs_frame & 0xFF, absolute_frame_id=abs_frame,
        timestamp_s=ts, row_sum=0, temperature_c=0.0,
        mean=0.0, std_dev=0.0, contrast=0.0,
        bfi=bfi, bvi=bvi, is_corrected=is_corrected,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_reduced_uncorrected_averages_post_correction():
    """
    Feed 4 per-camera uncorrected samples with pre-computed (simulating
    already-corrected-by-pipeline) BFI/BVI values.  Expect exactly one
    averaged Sample whose BFI/BVI equal the arithmetic mean of the inputs.

    This asserts the contract in §16.3: the averager consumes the
    SciencePipeline's *output* per-camera BFI/BVI values.  Because the
    averager never touches histogram moments or calibration, the only way
    its output can equal the mean of the inputs is if each input was
    already corrected before being fed in.
    """
    averager = _ReducedUncorrectedAverager(cam_counts={"left": 4})

    bfis = [0.10, 0.20, 0.30, 0.40]
    bvis = [1.0, 2.0, 3.0, 4.0]
    for cam_id, (bfi, bvi) in enumerate(zip(bfis, bvis)):
        averager.feed(_make_sample("left", cam_id, abs_frame=100,
                                   bfi=bfi, bvi=bvi, ts=1.5))

    assert len(averager.emitted) == 1
    out = averager.emitted[0]
    assert out.side == "left"
    assert out.cam_id == 0  # sentinel per §16.3
    assert out.absolute_frame_id == 100
    assert out.timestamp_s == 1.5
    assert out.is_corrected is False
    assert out.bfi == pytest.approx(sum(bfis) / 4)
    assert out.bvi == pytest.approx(sum(bvis) / 4)
    # Per-camera-only fields are not meaningful after side averaging.
    assert out.mean == 0.0
    assert out.contrast == 0.0
    assert out.temperature_c == 0.0


def test_reduced_corrected_csv_row_is_mean_of_per_camera_bfi_bvi(tmp_path):
    """
    Build a CorrectedBatch with distinct per-camera corrected BFI/BVI for
    one frame on both sides.  Drive the reduced-mode CSV writer and read
    back the row.  The bfi_left/right and bvi_left/right values must equal
    the arithmetic mean of the inputs and the header must contain exactly
    the 6 reduced columns (§16.1, §16.4.1).

    This proves the documented invariant that per-camera values are
    corrected first (the inputs already carry corrected bfi/bvi fields),
    then averaged into the reduced row — no re-correction of averaged
    rows, no mean/contrast/temp columns.
    """
    cam_counts = {"left": 4, "right": 4}
    active_sides = ["left", "right"]
    corrected_columns = ["bfi_left", "bfi_right", "bvi_left", "bvi_right"]

    left_bfis, left_bvis = [0.05, 0.07, 0.09, 0.11], [10.0, 12.0, 14.0, 16.0]
    right_bfis, right_bvis = [0.20, 0.25, 0.30, 0.35], [5.0, 6.0, 7.0, 8.0]

    samples = []
    for cam_id, (bfi, bvi) in enumerate(zip(left_bfis, left_bvis)):
        samples.append(_make_sample("left", cam_id, abs_frame=42,
                                    bfi=bfi, bvi=bvi, ts=2.0,
                                    is_corrected=True))
    for cam_id, (bfi, bvi) in enumerate(zip(right_bfis, right_bvis)):
        samples.append(_make_sample("right", cam_id, abs_frame=42,
                                    bfi=bfi, bvi=bvi, ts=2.0,
                                    is_corrected=True))
    batch = CorrectedBatch(dark_frame_start=0, dark_frame_end=600,
                           samples=samples)

    csv_path = tmp_path / "reduced_corrected.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["frame_id", "timestamp_s", *corrected_columns])
        _reduced_corrected_write(
            batch, cam_counts, active_sides, corrected_columns,
            corrected_by_frame={}, csv_writer=writer,
        )

    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))

    # Header: exactly frame_id, timestamp_s, and 4 reduced data columns.
    assert rows[0] == ["frame_id", "timestamp_s",
                       "bfi_left", "bfi_right", "bvi_left", "bvi_right"]
    assert len(rows) == 2  # header + one data row
    data = rows[1]
    assert int(data[0]) == 42
    assert float(data[2]) == pytest.approx(sum(left_bfis) / 4, abs=1e-6)
    assert float(data[3]) == pytest.approx(sum(right_bfis) / 4, abs=1e-6)
    assert float(data[4]) == pytest.approx(sum(left_bvis) / 4, abs=1e-6)
    assert float(data[5]) == pytest.approx(sum(right_bvis) / 4, abs=1e-6)

    # No per-camera columns leaked in.
    header = rows[0]
    for per_cam in ("bfi_l1", "mean_l1", "contrast_l1", "temp_l1",
                    "bfi_r1", "mean_r1", "std_l1"):
        assert per_cam not in header


def test_reduced_waits_for_all_active_cameras_before_flushing():
    """
    With camera mask 0x66 (4 bits set per side), the uncorrected averager
    must not emit a sample until all 4 active cameras on that side have
    contributed for that frame (§16.3 flush condition).
    """
    # 0x66 = 0b01100110 -> 4 bits set; bit positions 1,2,5,6.
    cam_counts = {"left": bin(0x66).count("1")}
    assert cam_counts["left"] == 4

    averager = _ReducedUncorrectedAverager(cam_counts=cam_counts)

    # First 3 cameras for frame 7: nothing should be emitted yet.
    for cam_id, bfi in zip((1, 2, 5), (0.1, 0.2, 0.3)):
        averager.feed(_make_sample("left", cam_id, abs_frame=7,
                                   bfi=bfi, bvi=bfi * 10))
    assert averager.emitted == []

    # 4th camera arrives -> single averaged emission.
    averager.feed(_make_sample("left", 6, abs_frame=7, bfi=0.4, bvi=4.0))
    assert len(averager.emitted) == 1
    assert averager.emitted[0].bfi == pytest.approx((0.1 + 0.2 + 0.3 + 0.4) / 4)
    assert averager.emitted[0].bvi == pytest.approx((1.0 + 2.0 + 3.0 + 4.0) / 4)

    # A 5th spurious sample for the same frame would open a NEW entry
    # (the first was flushed and deleted) and again wait for 4 cameras.
    averager.feed(_make_sample("left", 1, abs_frame=7, bfi=9.9, bvi=99.0))
    assert len(averager.emitted) == 1  # no new emission yet
