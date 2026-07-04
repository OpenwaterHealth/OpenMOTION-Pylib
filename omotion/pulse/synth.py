"""Synthetic blood-flow-index pulse-waveform generator (numpy-only).

A single cardiac beat is modelled as the sum of two Gaussians on a phase axis
[0, 1): a tall early **systolic** wave and a lower, later **diastolic**
(dicrotic) wave — the standard synthetic-PPG construction (Nature Sci. Rep.
2020, doi:10.1038/s41598-020-69076-x). A full signal strings beats together
with Normal-distributed beat-to-beat (RR) intervals for heart-rate variability,
plus optional additive noise and slow baseline wander.

Used by the pulse-analysis unit tests (known ground truth) and by the app's
no-hardware "Demo" mode, which streams these samples through the same
``PulseWaveformAnalyzer`` the live pipeline uses.
"""

from __future__ import annotations

from typing import Dict, Tuple

import numpy as np

# Per-preset two-Gaussian parameters: (amp, pos, wid) for the systolic and
# diastolic waves on the [0, 1) phase axis. Amplitudes are relative; the
# caller's ``amp`` scales the whole beat, so presets differ in intrinsic
# pulse morphology (peak-to-trough, dicrotic prominence), not just size.
SHAPE_PRESETS: Dict[str, dict] = {
    # Textbook resting waveform: dominant systolic peak, dicrotic shoulder.
    "normal":  dict(sys_amp=1.00, sys_pos=0.18, sys_wid=0.09,
                    dia_amp=0.32, dia_pos=0.42, dia_wid=0.14),
    # High pulsatility: tall, narrow systolic spike, faint diastole.
    "high_pi": dict(sys_amp=1.20, sys_pos=0.15, sys_wid=0.06,
                    dia_amp=0.15, dia_pos=0.45, dia_wid=0.10),
    # Low pulsatility: shorter systole, relatively prominent diastole.
    "low_pi":  dict(sys_amp=0.60, sys_pos=0.20, sys_wid=0.12,
                    dia_amp=0.40, dia_pos=0.46, dia_wid=0.16),
    # Damped / rounded: low-amplitude, smeared, little dicrotic structure.
    "damped":  dict(sys_amp=0.50, sys_pos=0.22, sys_wid=0.16,
                    dia_amp=0.30, dia_pos=0.52, dia_wid=0.20),
    # Morphologically normal; callers add heavy noise for a low-SNR case.
    "noisy":   dict(sys_amp=1.00, sys_pos=0.18, sys_wid=0.09,
                    dia_amp=0.32, dia_pos=0.42, dia_wid=0.14),
}


def _gauss(x: np.ndarray, amp: float, pos: float, wid: float) -> np.ndarray:
    return amp * np.exp(-0.5 * ((x - pos) / wid) ** 2)


def synth_beat(phase, *, sys_amp: float, sys_pos: float, sys_wid: float,
               dia_amp: float, dia_pos: float, dia_wid: float) -> np.ndarray:
    """Evaluate one two-Gaussian beat over ``phase`` (array in [0, 1))."""
    phase = np.asarray(phase, dtype=np.float64)
    return (_gauss(phase, sys_amp, sys_pos, sys_wid)
            + _gauss(phase, dia_amp, dia_pos, dia_wid))


def _beat_onsets(duration_s: float, bpm: float, hrv_frac: float,
                 rng: np.random.Generator) -> np.ndarray:
    """RR-interval onset times covering [0, duration_s] (+ one past the end)."""
    rr0 = 60.0 / float(bpm)
    onsets = [0.0]
    while onsets[-1] < duration_s + rr0:
        jitter = rng.normal(0.0, hrv_frac * rr0) if hrv_frac > 0 else 0.0
        rr = max(0.3, rr0 + jitter)      # physiological floor (200 bpm)
        onsets.append(onsets[-1] + rr)
    return np.asarray(onsets, dtype=np.float64)


def _render(t: np.ndarray, onsets: np.ndarray, shape: dict, *,
            amp: float, baseline: float, noise: float, wander: float,
            rng: np.random.Generator) -> np.ndarray:
    """Render a BFI signal at times ``t`` given beat ``onsets`` and a shape."""
    idx = np.searchsorted(onsets, t, side="right") - 1
    idx = np.clip(idx, 0, len(onsets) - 2)
    beat_start = onsets[idx]
    beat_len = onsets[idx + 1] - onsets[idx]
    phase = np.clip((t - beat_start) / beat_len, 0.0, 0.999999)
    v = baseline + amp * synth_beat(phase, **shape)
    if wander:
        v = v + wander * np.sin(2.0 * np.pi * 0.1 * t)
    if noise:
        v = v + rng.normal(0.0, noise, size=t.shape[0])
    return v.astype(np.float64)


def _resolve_shape(pulse_shape) -> dict:
    if isinstance(pulse_shape, dict):
        return pulse_shape
    try:
        return SHAPE_PRESETS[pulse_shape]
    except KeyError:
        raise ValueError(
            f"unknown pulse_shape {pulse_shape!r}; "
            f"choose one of {sorted(SHAPE_PRESETS)} or pass a param dict"
        )


def synth_bfi(*, duration_s: float, fs: float = 40.0, bpm: float = 72.0,
              hrv_frac: float = 0.05, amp: float = 2.0, baseline: float = 5.0,
              pulse_shape="normal", noise: float = 0.05, wander: float = 0.1,
              seed: int = 0) -> Tuple[np.ndarray, np.ndarray]:
    """Generate a pulsatile BFI time series.

    Returns ``(t, v)`` float64 arrays sampled at ``fs`` Hz for ``duration_s``
    seconds. ``bpm`` sets the mean heart rate; ``hrv_frac`` the RR-interval
    coefficient of variation; ``amp`` the pulse amplitude in BFI units on top
    of ``baseline``; ``pulse_shape`` a preset name (see ``SHAPE_PRESETS``) or a
    param dict. ``noise`` / ``wander`` add measurement noise and slow drift.
    Deterministic for a given ``seed``.
    """
    rng = np.random.default_rng(seed)
    n = int(round(duration_s * fs))
    t = np.arange(n, dtype=np.float64) / fs
    onsets = _beat_onsets(duration_s, bpm, hrv_frac, rng)
    v = _render(t, onsets, _resolve_shape(pulse_shape),
                amp=amp, baseline=baseline, noise=noise, wander=wander, rng=rng)
    return t, v


def synth_pair(*, duration_s: float, fs: float = 40.0, bpm: float = 72.0,
               hrv_frac: float = 0.05, amp: float = 2.0, baseline: float = 5.0,
               pulse_shape="normal", noise: float = 0.05, wander: float = 0.1,
               right_amp_ratio: float = 1.0, right_shape=None,
               seed: int = 0) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Generate a correlated left/right BFI pair sharing one beat train.

    Both sides use the same RR-interval (onset) sequence so the two waveforms
    stay cardiac-aligned; asymmetry is introduced by ``right_amp_ratio``
    (scales the right pulse amplitude) and optionally ``right_shape`` (a
    different preset/param dict for the right side). Returns ``(t, left,
    right)``. Deterministic for a given ``seed``.
    """
    rng = np.random.default_rng(seed)
    n = int(round(duration_s * fs))
    t = np.arange(n, dtype=np.float64) / fs
    onsets = _beat_onsets(duration_s, bpm, hrv_frac, rng)
    left_shape = _resolve_shape(pulse_shape)
    r_shape = _resolve_shape(right_shape) if right_shape is not None else left_shape
    left = _render(t, onsets, left_shape, amp=amp, baseline=baseline,
                   noise=noise, wander=wander, rng=rng)
    right = _render(t, onsets, r_shape, amp=amp * right_amp_ratio,
                    baseline=baseline, noise=noise, wander=wander, rng=rng)
    return t, left, right
