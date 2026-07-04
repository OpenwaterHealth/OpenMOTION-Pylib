"""PulseWaveformAnalyzer — streaming cardiac-pulse analysis of a BFI signal.

Given a stream of ``(timestamp, blood-flow-index)`` samples for ONE side, the
analyzer maintains a rolling window, segments it into individual cardiac beats,
builds an ensemble-average "template" pulse with a min/max envelope, and
computes pulse-shape morphology features (pulsatility / resistivity indices,
area under curve, systolic rise time, augmentation index, heart rate).

Design constraints (see the design spec):

* **numpy only, by choice** — the beat-detection band-limit is pluggable via
  ``band_method``: ``"movavg"`` (default, a moving-average high-pass + light
  smoothing) or ``"modwt"`` (the DCS pulsatility paper's sym4 stationary /
  à-trous wavelet cardiac-band sum, implemented in pure numpy — no PyWavelets
  needed). Three independent band-limits — moving average, a scipy
  Butterworth bandpass + ``find_peaks``, and the sym4 MODWT — all benchmark
  statistically indistinguishable for HR recovery across a noise / heart-rate
  sweep; the residual error is the 40 fps sampling limit at high HR, which no
  filter fixes. So the simple ``movavg`` is the default and scipy is *not* a
  dependency. Don't change the default or add scipy/pywt without
  re-benchmarking (see tests/test_pulse_analyzer.py).
* **40 Hz-friendly** — at 40 fps a 40-180 bpm beat spans 13-60 samples, which
  resolves amplitude/timing/area features well (fine dicrotic structure only
  best-effort).
* **pure & Qt-free** — identical engine drives the live pipeline stage and the
  app's no-hardware demo, so the two are guaranteed consistent.

The heavy computation runs in ``snapshot()`` (lazy); ``add_samples`` only
buffers, so a fast live caller can add often and snapshot at its own cadence.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from .types import PulseAnalysis, PulseFeatures


def _movavg(x: np.ndarray, win: int) -> np.ndarray:
    """Edge-padded box moving average (length-preserving)."""
    win = max(1, int(win))
    if win <= 1 or x.size == 0:
        return x.astype(np.float64, copy=True)
    win = min(win, x.size)
    pad = win // 2
    xp = np.pad(x, pad, mode="edge")
    k = np.ones(win, dtype=np.float64) / win
    y = np.convolve(xp, k, mode="same")
    return y[pad:pad + x.size]


def _nan_interp(v: np.ndarray) -> np.ndarray:
    """Linear-interpolate NaNs so the detection signal stays continuous."""
    v = v.astype(np.float64, copy=True)
    bad = ~np.isfinite(v)
    if not bad.any():
        return v
    if bad.all():
        return np.zeros_like(v)
    idx = np.arange(v.size)
    v[bad] = np.interp(idx[bad], idx[~bad], v[~bad])
    return v


# sym4 (Symlet-4) analysis low-pass (matches PyWavelets ``dec_lo``); the
# high-pass is the quadrature mirror g[k] = (-1)^k · h[N-1-k]. Used for a
# dependency-free stationary ("à trous") MODWT band-limit — the DCS
# pulsatility paper's method (sum of the cardiac-band detail levels), offered
# as the optional ``band_method="modwt"``.
_SYM4_LO = np.array([
    -0.07576571478927333, -0.02963552764599851, 0.49761866763201545,
    0.8037387518059161, 0.29785779560527736, -0.09921954357684722,
    -0.012603967262037833, 0.032223100604042702,
])
_SYM4_HI = _SYM4_LO[::-1].copy()
_SYM4_HI[1::2] *= -1.0


def _atrous(filt: np.ndarray, up: int) -> np.ndarray:
    """Insert ``up-1`` zeros between filter taps (the 'holes' of à trous)."""
    if up <= 1:
        return filt
    out = np.zeros((filt.size - 1) * up + 1, dtype=np.float64)
    out[::up] = filt
    return out


def _conv_reflect(x: np.ndarray, filt: np.ndarray) -> np.ndarray:
    pad = filt.size // 2
    xp = np.pad(x, pad, mode="reflect")
    return np.convolve(xp, filt, mode="same")[pad:pad + x.size]


def _cardiac_levels(fs: float, min_bpm: float, max_bpm: float) -> list[int]:
    """Detail levels whose octave-band geometric centre lands in the cardiac
    band. Level j covers ~[fs/2^(j+1), fs/2^j] Hz."""
    f_lo = min_bpm / 60.0 * 0.7
    f_hi = max_bpm / 60.0 * 1.3
    levels = []
    for j in range(1, 9):
        fc = fs / (2 ** j) / np.sqrt(2.0)
        if f_lo <= fc <= f_hi:
            levels.append(j)
    return levels


def _wavelet_band(x: np.ndarray, fs: float,
                  min_bpm: float, max_bpm: float) -> np.ndarray:
    """Sum of the cardiac-band sym4 stationary-wavelet detail levels (à trous).

    Falls back to a moving-average detrend if no detail level lands in-band
    (degenerate ``fs``)."""
    levels = _cardiac_levels(fs, min_bpm, max_bpm)
    if not levels:
        base = int(round(fs * 60.0 / min_bpm))
        return _movavg(x - _movavg(x, base), 5)
    a = x.astype(np.float64, copy=True)
    band = np.zeros_like(a)
    for j in range(1, max(levels) + 1):
        up = 2 ** (j - 1)
        d = _conv_reflect(a, _atrous(_SYM4_HI, up))
        a = _conv_reflect(a, _atrous(_SYM4_LO, up))
        if j in levels:
            band += d
    return band


def _estimate_period_samples(ac: np.ndarray, fs: float,
                             min_bpm: float, max_bpm: float) -> Optional[int]:
    """Dominant cardiac period (samples) via autocorrelation within the
    plausible RR-interval band. None if it can't be estimated."""
    n = ac.size
    if n < 8:
        return None
    ac = ac - ac.mean()
    corr = np.correlate(ac, ac, mode="full")[n - 1:]     # lags 0..n-1
    lag_min = max(1, int(fs * 60.0 / max_bpm))
    lag_max = min(n - 1, int(fs * 60.0 / min_bpm))
    if lag_max <= lag_min + 1:
        return None
    seg = corr[lag_min:lag_max + 1]
    if seg.size == 0 or not np.any(np.isfinite(seg)):
        return None
    return lag_min + int(np.argmax(seg))


def _detect_peaks(sm: np.ndarray, refractory: int) -> np.ndarray:
    """Indices of systolic peaks (local maxima) at least ``refractory`` samples
    apart; when two are too close the taller one wins."""
    if sm.size < 3:
        return np.empty(0, dtype=int)
    interior = np.where((sm[1:-1] > sm[:-2]) & (sm[1:-1] >= sm[2:]))[0] + 1
    kept: list[int] = []
    refractory = max(1, int(refractory))
    for i in interior:
        if not kept or (i - kept[-1]) >= refractory:
            kept.append(int(i))
        elif sm[i] > sm[kept[-1]]:
            kept[-1] = int(i)
    return np.asarray(kept, dtype=int)


def _segment_feet(sm: np.ndarray, vsig: np.ndarray,
                  refractory: int) -> np.ndarray:
    """Beat onsets = the true foot (minimum of the ORIGINAL signal) between
    consecutive systolic peaks.

    Detecting peaks on the band-limited signal and then locating each foot on
    the original signal keeps beat windows aligned to real end-diastolic
    troughs — the moving-average detrend shifts minima, so trough-detecting the
    detrended signal directly would window beats off-centre."""
    peaks = _detect_peaks(sm, refractory)
    if peaks.size < 2:
        return np.empty(0, dtype=int)
    feet = [a + int(np.argmin(vsig[a:b]))
            for a, b in zip(peaks[:-1], peaks[1:]) if b > a]
    return np.asarray(feet, dtype=int)


def _resample_beat(seg: np.ndarray, bins: int) -> Optional[np.ndarray]:
    """Linear-resample one beat to ``bins`` points on phase [0, 1)."""
    m = seg.size
    if m < 3 or not np.all(np.isfinite(seg)):
        return None
    src = np.linspace(0.0, 1.0, m, endpoint=False)
    dst = np.linspace(0.0, 1.0, bins, endpoint=False)
    return np.interp(dst, src, seg)


def _beat_passes_qc(rb: np.ndarray) -> bool:
    """Reject artifact beats (adapted from the DCS pulsatility QC rules)."""
    bins = rb.size
    peak = int(np.argmax(rb))
    if peak / bins >= 0.6:                       # systolic peak too late
        return False
    amp = float(rb.max() - rb.min())
    if amp <= 0.0:                               # flat / inverted
        return False
    k = max(2, bins // 10)
    if rb[k] - rb[0] <= 0.0:                      # no systolic upstroke
        return False
    onset_val = float(rb[0])
    edf = float(rb[-1])
    mf = float(rb.mean())
    if edf > 2.0 * max(onset_val, 1e-9) or edf > mf:
        return False
    return True


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    denom = np.sqrt(np.sum(a * a) * np.sum(b * b))
    if denom <= 0:
        return 0.0
    return float(np.sum(a * b) / denom)


class PulseWaveformAnalyzer:
    #: Band-limit strategies for beat detection. ``"movavg"`` (default) — a
    #: moving-average high-pass + light smoothing; ``"modwt"`` — a sym4
    #: stationary-wavelet cardiac-band sum (the DCS paper's method).
    #: Benchmarks show them equivalent for HR recovery on 40 fps data; movavg
    #: is the simpler default. See tests/test_pulse_analyzer.py.
    BAND_METHODS = ("movavg", "modwt")

    def __init__(self, *, side: str = "left", fs_hint: float = 40.0,
                 phase_bins: int = 60, history_beats: int = 20,
                 min_bpm: float = 40.0, max_bpm: float = 180.0,
                 smooth_win: int = 5, raw_window_s: float = 12.0,
                 band_method: str = "movavg"):
        if band_method not in self.BAND_METHODS:
            raise ValueError(
                f"band_method must be one of {self.BAND_METHODS}, got {band_method!r}")
        self.band_method = band_method
        self.side = side
        self.fs_hint = float(fs_hint)
        self.phase_bins = int(phase_bins)
        self.history_beats = int(history_beats)
        self.min_bpm = float(min_bpm)
        self.max_bpm = float(max_bpm)
        self.smooth_win = int(smooth_win)
        self.raw_window_s = float(raw_window_s)
        self._phase = np.linspace(0.0, 1.0, self.phase_bins, endpoint=False)
        self._t = np.empty(0, dtype=np.float64)
        self._v = np.empty(0, dtype=np.float64)
        self._last_reported_count = 0

    # ── ingest ─────────────────────────────────────────────────────────────

    def add_samples(self, t, v) -> None:
        t = np.asarray(t, dtype=np.float64).ravel()
        v = np.asarray(v, dtype=np.float64).ravel()
        if t.size == 0:
            return
        n = min(t.size, v.size)
        self._t = np.concatenate([self._t, t[:n]])
        self._v = np.concatenate([self._v, v[:n]])
        # Trim to the rolling window.
        if self._t.size:
            cutoff = self._t[-1] - self.raw_window_s
            keep = self._t >= cutoff
            if not keep.all():
                self._t = self._t[keep]
                self._v = self._v[keep]

    def reset(self) -> None:
        self._t = np.empty(0, dtype=np.float64)
        self._v = np.empty(0, dtype=np.float64)
        self._last_reported_count = 0

    # ── analysis ────────────────────────────────────────────────────────────

    def _estimate_fs(self) -> float:
        if self._t.size < 3:
            return self.fs_hint
        dt = np.median(np.diff(self._t))
        if dt <= 0 or not np.isfinite(dt):
            return self.fs_hint
        return 1.0 / dt

    def _empty_snapshot(self) -> PulseAnalysis:
        nan_bins = np.full(self.phase_bins, np.nan)
        return PulseAnalysis(
            side=self.side, phase=self._phase.copy(),
            template=nan_bins.copy(), env_min=nan_bins.copy(),
            env_max=nan_bins.copy(), env_p25=nan_bins.copy(),
            env_p75=nan_bins.copy(),
            live_phase=np.empty(0), live_value=np.empty(0),
            features=PulseFeatures(), beat_count=0, updated_beat=False,
        )

    def _band_limit(self, vfill: np.ndarray, fs: float) -> np.ndarray:
        """Cardiac-band signal used to locate beats (per ``band_method``)."""
        if self.band_method == "modwt":
            return _wavelet_band(vfill, fs, self.min_bpm, self.max_bpm)
        base_win = int(round(fs * 60.0 / self.min_bpm))     # ~one longest beat
        ac = vfill - _movavg(vfill, base_win)
        return _movavg(ac, self.smooth_win)

    def snapshot(self) -> PulseAnalysis:
        if self._t.size < 8:
            return self._empty_snapshot()

        t = self._t
        v = self._v
        fs = self._estimate_fs()

        # Band-limit for detection (NaN-interpolated so detection stays valid).
        vfill = _nan_interp(v)
        sm = self._band_limit(vfill, fs)

        period = _estimate_period_samples(sm, fs, self.min_bpm, self.max_bpm)
        if period is None:
            refractory = int(fs * 60.0 / self.max_bpm)
        else:
            refractory = max(1, int(0.6 * period))
        onsets = _segment_feet(sm, vfill, refractory)
        if onsets.size < 2:
            snap = self._empty_snapshot()
            self._append_live(snap, t, v, onsets, fs)
            return snap

        # Segment into beats on the ORIGINAL signal, resample, QC.
        beats: list[np.ndarray] = []
        rr: list[float] = []
        onset_times = t[onsets]
        for o0, o1, t0, t1 in zip(onsets[:-1], onsets[1:],
                                  onset_times[:-1], onset_times[1:]):
            interval = t1 - t0
            if not (60.0 / self.max_bpm <= interval <= 60.0 / self.min_bpm):
                continue
            rb = _resample_beat(v[o0:o1], self.phase_bins)
            if rb is None or not _beat_passes_qc(rb):
                continue
            beats.append(rb)
            rr.append(float(interval))

        if not beats:
            snap = self._empty_snapshot()
            self._append_live(snap, t, v, onsets, fs)
            return snap

        beats = beats[-self.history_beats:]
        rr = rr[-self.history_beats:]
        stack = np.vstack(beats)
        template = stack.mean(axis=0)
        env_min = stack.min(axis=0)
        env_max = stack.max(axis=0)
        env_p25 = np.percentile(stack, 25, axis=0)
        env_p75 = np.percentile(stack, 75, axis=0)

        features = self._features(template, stack, rr)
        beat_count = len(beats)
        updated = beat_count != self._last_reported_count
        self._last_reported_count = beat_count

        snap = PulseAnalysis(
            side=self.side, phase=self._phase.copy(), template=template,
            env_min=env_min, env_max=env_max, env_p25=env_p25, env_p75=env_p75,
            live_phase=np.empty(0), live_value=np.empty(0),
            features=features, beat_count=beat_count, updated_beat=updated,
        )
        self._append_live(snap, t, v, onsets, fs, rr=rr)
        return snap

    def _features(self, template: np.ndarray, stack: np.ndarray,
                  rr: list[float]) -> PulseFeatures:
        bins = self.phase_bins
        psf = float(np.nanmax(template))
        peak_idx = int(np.nanargmax(template))
        edf = float(template[-1])
        mf = float(np.nanmean(template))
        amp = psf - edf
        pi = amp / mf if mf != 0 else np.nan
        ri = amp / psf if psf != 0 else np.nan
        auc = float(np.nansum(template - edf) / bins)
        rise_frac = peak_idx / bins
        med_rr = float(np.median(rr)) if rr else np.nan
        hr = 60.0 / med_rr if med_rr and np.isfinite(med_rr) else np.nan
        rise_ms = rise_frac * med_rr * 1000.0 if np.isfinite(med_rr) else np.nan
        consistency = float(np.median([_pearson(b, template) for b in stack]))
        aix = self._augmentation_index(template, peak_idx, psf, edf)
        return PulseFeatures(
            hr_bpm=hr, mean_flow=mf, psf=psf, edf=edf, amp=amp, pi=pi, ri=ri,
            auc=auc, rise_time_frac=rise_frac, rise_time_ms=rise_ms, aix=aix,
            beat_count=len(stack), consistency=consistency,
        )

    def _augmentation_index(self, template: np.ndarray, peak_idx: int,
                            psf: float, edf: float) -> float:
        """AIx ≈ (diastolic-peak height) / (systolic pulse amplitude).

        Best-effort: look for a secondary local maximum in the template after
        the systolic peak. Returns NaN when none is resolvable (common at
        40 Hz)."""
        pp = psf - edf
        if pp <= 0:
            return np.nan
        tail = template[peak_idx + 1:]
        if tail.size < 3:
            return np.nan
        interior = np.where((tail[1:-1] > tail[:-2]) & (tail[1:-1] >= tail[2:]))[0]
        if interior.size == 0:
            return np.nan
        dia_val = float(tail[interior + 1].max())
        return (dia_val - edf) / pp

    def _append_live(self, snap: PulseAnalysis, t: np.ndarray, v: np.ndarray,
                     onsets: np.ndarray, fs: float,
                     rr: Optional[list[float]] = None) -> None:
        """Attach the in-progress (post-last-onset) beat, phase-mapped."""
        if onsets.size == 0:
            return
        last_t = t[onsets[-1]]
        mask = t >= last_t
        if not mask.any():
            return
        rr_est = (float(np.median(rr)) if rr else
                  (onsets.size >= 2 and float(np.median(np.diff(t[onsets]))) or
                   60.0 / 72.0))
        if not rr_est or not np.isfinite(rr_est) or rr_est <= 0:
            rr_est = 60.0 / 72.0
        lt = t[mask]
        lv = v[mask]
        phase = np.clip((lt - last_t) / rr_est, 0.0, 1.0)
        finite = np.isfinite(lv)
        snap.live_phase = phase[finite]
        snap.live_value = lv[finite]
