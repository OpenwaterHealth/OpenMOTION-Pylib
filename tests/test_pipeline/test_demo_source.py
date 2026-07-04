"""DemoScanSource — replay a recorded bfi_results CSV as raw histograms at the
top of the real pipeline (no hardware). Inverts BFI/BVI -> contrast/mean ->
histograms so the pipeline recomputes ~the recorded values, feeding the whole
chain (plots + pulse). The source iterator ends when the file is exhausted, so
the scan auto-stops."""

import csv

import numpy as np

from omotion.pipeline.factory import default_pipeline
from omotion.pipeline.runner import ScanRunner
from omotion.pipeline.sinks import ScanMetadata
from omotion.pipeline.pedestal import SensorPedestals
from omotion.pulse.scan_synth import DemoScanSource
from omotion.pulse.synth import synth_bfi


def _write_bfi_csv(path, *, bpm=72.0, dur=10.0, fs=40.0, cams=(0, 1),
                   baseline=5.0, amp=1.5):
    """Write a small bfi_results CSV (camera,side,time_s,BFI,BVI)."""
    n = int(dur * fs)
    t = np.arange(n) / fs
    _, lb = synth_bfi(duration_s=dur, fs=fs, bpm=bpm, amp=amp, baseline=baseline,
                      noise=0.02, wander=0.0, seed=1)
    _, rb = synth_bfi(duration_s=dur, fs=fs, bpm=bpm, amp=amp * 0.7,
                      baseline=baseline, noise=0.02, wander=0.0, seed=2)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["camera", "side", "time_s", "BFI", "BVI"])
        for cam in cams:
            for side, base in (("left", lb), ("right", rb)):
                for i in range(n):
                    w.writerow([cam, side, round(float(t[i]), 3),
                                round(float(base[i]), 5), 7.5])
    return path[:n] if False else n


class _Capture:
    channels = {"pulse", "live_side"}

    def on_scan_start(self, meta):
        self.pulse = []
        self.bfi = {0: [], 1: []}

    def consume(self, channel, payload):
        if channel == "pulse":
            self.pulse.append(payload)
        elif channel == "live_side" and np.isfinite(payload.bfi):
            self.bfi[int(payload.side)].append(float(payload.bfi))

    def on_complete(self):
        pass


def _meta():
    return ScanMetadata(
        scan_id="demo", subject_id="sim", operator="sim",
        started_at_iso="2026-07-04T00:00:00Z", duration_sec=15,
        left_camera_mask=0x03, right_camera_mask=0x03, reduced_mode=True)


def _run(csv_path, meta):
    src = DemoScanSource(csv_path=csv_path, metadata=meta, left_mask=0x03,
                         right_mask=0x03, dark_interval=200)
    pipe = default_pipeline(
        metadata=meta, calibration=src.calibration,
        pedestals=SensorPedestals(left=src.pedestal, right=src.pedestal),
        dark_interval=src.dark_interval, enable_pulse=True)
    cap = _Capture()
    ScanRunner(source=src, pipeline=pipe, sinks=[cap]).run()
    return cap


def test_demo_source_roundtrips_recorded_bfi(tmp_path):
    p = str(tmp_path / "demo.csv")
    _write_bfi_csv(p, bpm=72.0, dur=10.0, baseline=5.0, amp=1.5)
    cap = _run(p, _meta())
    for side in (0, 1):
        arr = np.asarray(cap.bfi[side])
        assert arr.size > 50
        # Pipeline recomputes ~the recorded BFI (baseline ~5, pulsatile).
        assert 3.5 < arr.mean() < 6.5
        assert arr.max() - arr.min() > 0.8      # pulsatility preserved


def test_demo_source_recovers_heart_rate_and_pulse(tmp_path):
    p = str(tmp_path / "demo.csv")
    _write_bfi_csv(p, bpm=90.0, dur=12.0)
    cap = _run(p, _meta())
    left = [x for x in cap.pulse if x.side == "left" and x.beat_count > 0]
    assert left, "no pulse events from the demo replay"
    assert 82.0 <= left[-1].features.hr_bpm <= 98.0


def test_demo_source_iterator_terminates(tmp_path):
    # The source ends when the file is exhausted (drives auto-stop).
    p = str(tmp_path / "demo.csv")
    _write_bfi_csv(p, dur=6.0)
    src = DemoScanSource(csv_path=p, metadata=_meta(), left_mask=0x03,
                         right_mask=0x03, dark_interval=200)
    n_batches = sum(1 for _ in src)
    assert 0 < n_batches < 10000                # finite


def test_demo_source_respects_masks(tmp_path):
    # Only cameras in the mask are emitted.
    p = str(tmp_path / "demo.csv")
    _write_bfi_csv(p, dur=4.0, cams=(0, 1))
    src = DemoScanSource(csv_path=p, metadata=_meta(), left_mask=0x01,
                         right_mask=0x00, dark_interval=200)
    seen_left, seen_right = set(), set()
    for batch in src:
        for i in range(batch.cam_ids.shape[0]):
            (seen_left if batch.side_ids[i] == 0 else seen_right).add(
                int(batch.cam_ids[i]))
    assert seen_left == {0}          # left mask 0x01 -> only cam 0
    assert seen_right == set()       # right mask 0x00 -> nothing
