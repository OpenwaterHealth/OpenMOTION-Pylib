"""PulseWaveformAnalyzer — streaming beat segmentation + ensemble + features.

Pure numpy; validated against the synthetic generator's known ground truth.
"""

import numpy as np
import pytest

from omotion.pulse.analyzer import PulseWaveformAnalyzer
from omotion.pulse.synth import synth_bfi


def _feed_all(analyzer, t, v):
    analyzer.add_samples(np.asarray(t), np.asarray(v))


def _clean_signal(bpm=72.0, duration_s=14.0, **kw):
    return synth_bfi(duration_s=duration_s, fs=40.0, bpm=bpm, hrv_frac=0.0,
                     noise=0.0, wander=0.0, seed=1, **kw)


# ── segmentation / heart rate ──────────────────────────────────────────────

def test_recovers_heart_rate():
    a = PulseWaveformAnalyzer(side="left")
    _feed_all(a, *_clean_signal(bpm=72.0))
    hr = a.snapshot().features.hr_bpm
    assert 68.0 <= hr <= 76.0


def test_recovers_a_different_heart_rate():
    a = PulseWaveformAnalyzer(side="left")
    _feed_all(a, *_clean_signal(bpm=50.0))
    hr = a.snapshot().features.hr_bpm
    assert 46.0 <= hr <= 54.0


def test_detects_multiple_beats():
    a = PulseWaveformAnalyzer(side="left")
    _feed_all(a, *_clean_signal(bpm=72.0, duration_s=14.0))
    assert a.snapshot().beat_count >= 8


# ── ensemble template + envelope ───────────────────────────────────────────

def test_template_has_phase_bins_length():
    a = PulseWaveformAnalyzer(side="left", phase_bins=60)
    _feed_all(a, *_clean_signal())
    snap = a.snapshot()
    assert snap.template.shape == (60,)
    assert snap.phase.shape == (60,)
    assert snap.env_min.shape == (60,)
    assert snap.env_max.shape == (60,)


def test_envelope_brackets_template():
    a = PulseWaveformAnalyzer(side="left")
    _feed_all(a, *_clean_signal(bpm=72.0, duration_s=16.0))
    snap = a.snapshot()
    # min <= template <= max, elementwise, ignoring any NaN bins.
    assert np.all(snap.env_min <= snap.template + 1e-6)
    assert np.all(snap.template <= snap.env_max + 1e-6)
    assert np.all(snap.env_min <= snap.env_max + 1e-6)


def test_template_systolic_peak_is_early():
    a = PulseWaveformAnalyzer(side="left")
    _feed_all(a, *_clean_signal())
    snap = a.snapshot()
    peak_phase = snap.phase[int(np.nanargmax(snap.template))]
    assert peak_phase < 0.4


# ── morphology features ────────────────────────────────────────────────────

def test_pulsatility_and_resistivity_indices_are_physiological():
    a = PulseWaveformAnalyzer(side="left")
    _feed_all(a, *_clean_signal(bpm=72.0, amp=2.0, baseline=5.0))
    f = a.snapshot().features
    assert f.amp > 0.0
    assert f.pi > 0.0
    assert 0.0 < f.ri < 1.0
    # PSF above EDF above zero for a normal beat.
    assert f.psf > f.edf


def test_higher_amplitude_gives_larger_pulse_amplitude_feature():
    lo = PulseWaveformAnalyzer(side="left")
    hi = PulseWaveformAnalyzer(side="right")
    _feed_all(lo, *_clean_signal(amp=1.0))
    _feed_all(hi, *_clean_signal(amp=3.0))
    assert hi.snapshot().features.amp > lo.snapshot().features.amp


def test_clean_signal_has_high_template_consistency():
    a = PulseWaveformAnalyzer(side="left")
    _feed_all(a, *_clean_signal(bpm=72.0, duration_s=16.0))
    # Identical beats → each beat correlates near-perfectly with the template.
    assert a.snapshot().features.consistency > 0.9


# ── robustness / lifecycle ─────────────────────────────────────────────────

def test_snapshot_before_any_beat_is_wellformed():
    a = PulseWaveformAnalyzer(side="left")
    a.add_samples(np.array([0.0, 0.025]), np.array([5.0, 5.1]))
    snap = a.snapshot()
    assert snap.beat_count == 0
    assert snap.template.shape[0] == a.phase_bins
    assert np.isnan(snap.features.hr_bpm)


def test_handles_nan_samples_without_crashing():
    t, v = _clean_signal(bpm=72.0, duration_s=14.0)
    v = v.copy()
    v[100:110] = np.nan            # a short dropout gap
    a = PulseWaveformAnalyzer(side="left")
    _feed_all(a, t, v)
    snap = a.snapshot()
    assert np.isfinite(snap.features.hr_bpm)
    assert snap.beat_count >= 5


def test_reset_clears_state():
    a = PulseWaveformAnalyzer(side="left")
    _feed_all(a, *_clean_signal())
    assert a.snapshot().beat_count > 0
    a.reset()
    assert a.snapshot().beat_count == 0


def test_streaming_in_chunks_recovers_heart_rate():
    t, v = _clean_signal(bpm=72.0, duration_s=14.0)
    a = PulseWaveformAnalyzer(side="left")
    for i in range(0, len(t), 7):    # small chunks, like live batches
        a.add_samples(t[i:i + 7], v[i:i + 7])
    hr = a.snapshot().features.hr_bpm
    assert 68.0 <= hr <= 76.0


def test_live_partial_beat_is_within_current_cycle():
    a = PulseWaveformAnalyzer(side="left")
    _feed_all(a, *_clean_signal())
    snap = a.snapshot()
    # The in-progress beat's phase is monotonic-ish and bounded to [0, 1].
    assert snap.live_phase.shape == snap.live_value.shape
    if snap.live_phase.size:
        assert snap.live_phase.min() >= 0.0
        assert snap.live_phase.max() <= 1.0 + 1e-9
