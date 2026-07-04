"""Synthetic BFI pulse-waveform generator (omotion.pulse.synth).

Pure numpy; no hardware. Generates realistic pulsatile blood-flow-index
time series for tests and the app's no-hardware demo. Model: a single beat
is the sum of two Gaussians (systolic + diastolic waves) on a baseline, with
Normal-distributed beat-to-beat intervals (HRV) and optional noise/wander.
"""

import numpy as np

from omotion.pulse.synth import synth_beat, synth_bfi, synth_pair


def _count_peaks(v):
    """Crude interior local-maxima count for HR sanity checks."""
    return int(np.sum((v[1:-1] > v[:-2]) & (v[1:-1] >= v[2:])))


# ── synth_beat ────────────────────────────────────────────────────────────

def test_synth_beat_length_matches_phase():
    phase = np.linspace(0.0, 1.0, 60, endpoint=False)
    beat = synth_beat(phase, sys_amp=1.0, sys_pos=0.18, sys_wid=0.08,
                      dia_amp=0.4, dia_pos=0.45, dia_wid=0.12)
    assert beat.shape == phase.shape


def test_synth_beat_systolic_peak_is_early_and_dominant():
    phase = np.linspace(0.0, 1.0, 100, endpoint=False)
    beat = synth_beat(phase, sys_amp=1.0, sys_pos=0.18, sys_wid=0.07,
                      dia_amp=0.35, dia_pos=0.5, dia_wid=0.12)
    peak_phase = phase[int(np.argmax(beat))]
    # Systolic peak sits in the first third of the cardiac cycle.
    assert peak_phase < 0.33


# ── synth_bfi ─────────────────────────────────────────────────────────────

def test_synth_bfi_returns_arrays_at_requested_rate():
    t, v = synth_bfi(duration_s=10.0, fs=40.0, bpm=72.0, seed=1)
    assert t.shape == v.shape
    assert len(t) == 400
    dt = np.diff(t)
    assert np.all(dt > 0)
    assert np.allclose(dt, 0.025, atol=1e-9)


def test_synth_bfi_is_finite_and_positive_baseline():
    t, v = synth_bfi(duration_s=8.0, fs=40.0, bpm=72.0, baseline=5.0,
                     amp=2.0, noise=0.0, wander=0.0, seed=2)
    assert np.all(np.isfinite(v))
    # Baseline 5, amplitude 2 → stays comfortably positive.
    assert v.min() > 0.0


def test_synth_bfi_heart_rate_matches_bpm():
    # No HRV/noise → clean pulses; peak count should track the requested rate.
    t, v = synth_bfi(duration_s=20.0, fs=40.0, bpm=60.0, hrv_frac=0.0,
                     noise=0.0, wander=0.0, seed=3)
    # 60 bpm over 20 s ≈ 20 beats.
    assert 18 <= _count_peaks(v) <= 22


def test_synth_bfi_deterministic_for_seed():
    a = synth_bfi(duration_s=5.0, bpm=72.0, noise=0.2, hrv_frac=0.08, seed=7)[1]
    b = synth_bfi(duration_s=5.0, bpm=72.0, noise=0.2, hrv_frac=0.08, seed=7)[1]
    assert np.array_equal(a, b)


def test_synth_bfi_seed_changes_signal():
    a = synth_bfi(duration_s=5.0, bpm=72.0, noise=0.2, hrv_frac=0.08, seed=7)[1]
    b = synth_bfi(duration_s=5.0, bpm=72.0, noise=0.2, hrv_frac=0.08, seed=8)[1]
    assert not np.array_equal(a, b)


def test_synth_bfi_high_pi_shape_is_more_pulsatile_than_damped():
    def pulsatility(shape):
        t, v = synth_bfi(duration_s=20.0, bpm=72.0, hrv_frac=0.0, noise=0.0,
                         wander=0.0, pulse_shape=shape, seed=5)
        return (v.max() - v.min()) / v.mean()
    assert pulsatility("high_pi") > pulsatility("damped")


# ── synth_pair ────────────────────────────────────────────────────────────

def test_synth_pair_returns_two_aligned_sides():
    t, left, right = synth_pair(duration_s=10.0, fs=40.0, bpm=72.0, seed=1)
    assert t.shape == left.shape == right.shape
    assert len(t) == 400


def test_synth_pair_amplitude_asymmetry_lowers_right_pulsatility():
    # right_amp_ratio < 1 damps the right side's pulse amplitude.
    t, left, right = synth_pair(duration_s=20.0, fs=40.0, bpm=72.0,
                                hrv_frac=0.0, noise=0.0, wander=0.0,
                                right_amp_ratio=0.5, seed=4)
    left_amp = left.max() - left.min()
    right_amp = right.max() - right.min()
    assert right_amp < left_amp
