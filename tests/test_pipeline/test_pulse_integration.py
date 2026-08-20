"""End-to-end: synthetic cardiac-modulated speckle histograms → the real
science pipeline (default_pipeline with enable_pulse=True) → pulse events.

Proves the whole live path — moments, pedestal, dark correction, shot-noise,
BFI/BVI, side-average, and PulseWaveformStage + runner dispatch — not just the
analyzer in isolation. No hardware; numpy only.
"""

import numpy as np

from omotion.pipeline.factory import default_pipeline
from omotion.pipeline.runner import ScanRunner
from omotion.pipeline.sinks import ScanMetadata
from omotion.pipeline.pedestal import SensorPedestals
from omotion.pulse.scan_synth import SyntheticPulseScanSource


class _Capture:
    channels = {"pulse", "live_side"}

    def on_scan_start(self, meta):
        self.pulse = []
        self.bfi = {0: [], 1: []}

    def consume(self, channel, payload):
        if channel == "pulse":
            self.pulse.append(payload)
        elif channel == "live_side":
            b = float(payload.bfi)
            if np.isfinite(b):
                self.bfi[int(payload.side)].append(b)

    def on_complete(self):
        pass


def _run(left_bpm=72.0, right_bpm=90.0, right_amp_ratio=0.6, seed=1,
         n_frames=520):
    meta = ScanMetadata(
        scan_id="pulse_integ", subject_id="sim", operator="sim",
        started_at_iso="2026-07-03T00:00:00Z", duration_sec=15,
        left_camera_mask=0x01, right_camera_mask=0x01, reduced_mode=True,
    )
    src = SyntheticPulseScanSource(
        metadata=meta, n_frames=n_frames, left_bpm=left_bpm,
        right_bpm=right_bpm, right_amp_ratio=right_amp_ratio, seed=seed,
    )
    pipe = default_pipeline(
        metadata=meta, calibration=src.calibration,
        pedestals=SensorPedestals(left=src.pedestal, right=src.pedestal),
        dark_interval=src.dark_interval, enable_pulse=True,
    )
    cap = _Capture()
    ScanRunner(source=src, pipeline=pipe, sinks=[cap]).run()
    return cap


def _last(cap, side):
    ss = [p for p in cap.pulse if p.side == side and p.beat_count > 0]
    return ss[-1] if ss else None


def test_full_pipeline_produces_pulse_events():
    cap = _run()
    assert cap.pulse, "no pulse events emitted by the pipeline"
    assert {p.side for p in cap.pulse} == {"left", "right"}


def test_full_pipeline_bfi_is_pulsatile():
    # Side-averaged BFI must carry real cardiac variation (not pegged/flat).
    cap = _run()
    for side in (0, 1):
        arr = np.asarray(cap.bfi[side])
        assert arr.size > 100
        assert arr.max() - arr.min() > 1.0        # clear pulsatility in BFI units


def test_full_pipeline_recovers_both_heart_rates():
    cap = _run(left_bpm=72.0, right_bpm=90.0)
    left, right = _last(cap, "left"), _last(cap, "right")
    assert left is not None and right is not None
    assert 66.0 <= left.features.hr_bpm <= 78.0
    assert 84.0 <= right.features.hr_bpm <= 96.0


def test_amplitude_asymmetry_survives_pipeline():
    cap = _run(left_bpm=72.0, right_bpm=72.0, right_amp_ratio=0.5)
    left, right = _last(cap, "left"), _last(cap, "right")
    assert left.features.amp > right.features.amp


def test_pulse_features_are_physiological():
    cap = _run()
    for side in ("left", "right"):
        f = _last(cap, side).features
        assert f.pi > 0.0
        assert 0.0 < f.ri < 1.0
        assert f.psf > f.edf
        assert f.consistency > 0.5
