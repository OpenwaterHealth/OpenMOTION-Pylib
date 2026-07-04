"""Snapshot dataclasses returned by PulseWaveformAnalyzer.

Kept dependency-free (numpy only) so the pipeline stage, the app sink, and the
unit tests can all share them. ``to_qvariant`` flattens arrays to plain Python
lists for crossing the PyQt signal boundary into QML.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np

NAN = float("nan")


@dataclass
class PulseFeatures:
    """Pulse-shape morphology metrics (see docs/SciencePipeline-style refs).

    All in BFI units except the indices (unitless), rise time (fractional /
    ms), heart rate (bpm) and consistency (Pearson r in [-1, 1]). NaN means
    "not enough data yet".
    """
    hr_bpm: float = NAN          # heart rate, 60 / median RR interval
    mean_flow: float = NAN       # MF — mean BFI across the template beat
    psf: float = NAN             # peak systolic flow (template max)
    edf: float = NAN             # end-diastolic flow (template end value)
    amp: float = NAN             # AMP = PSF - EDF
    pi: float = NAN              # pulsatility index = AMP / MF
    ri: float = NAN              # resistivity index = AMP / PSF
    auc: float = NAN             # area under the (baseline-subtracted) beat
    rise_time_frac: float = NAN  # phase of the systolic peak, 0..1
    rise_time_ms: float = NAN    # systolic upstroke time in ms
    aix: float = NAN             # augmentation index (best-effort at 40 Hz)
    beat_count: int = 0          # accepted beats in the current window
    consistency: float = NAN     # median Pearson r of beats vs the template
    periodicity: float = 0.0     # normalized autocorrelation peak, 0..1
    reliable: bool = False       # a genuine, regular cardiac pulse is present

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class PulseAnalysis:
    """One analysis snapshot for a single side."""
    side: str
    phase: np.ndarray            # (bins,) cardiac phase axis 0..1
    template: np.ndarray         # (bins,) ensemble-average pulse, BFI units
    env_min: np.ndarray          # (bins,) per-phase min over recent beats
    env_max: np.ndarray          # (bins,) per-phase max over recent beats
    env_p25: np.ndarray          # (bins,) per-phase 25th percentile
    env_p75: np.ndarray          # (bins,) per-phase 75th percentile
    live_phase: np.ndarray       # (k,) phase of the in-progress beat
    live_value: np.ndarray       # (k,) BFI of the in-progress beat
    features: PulseFeatures = field(default_factory=PulseFeatures)
    beat_count: int = 0
    updated_beat: bool = False   # a beat closed since the previous snapshot

    def to_qvariant(self) -> dict:
        """Plain-Python payload for a PyQt signal → QML (lists, floats)."""
        def _l(a):
            return [float(x) for x in np.asarray(a, dtype=float).ravel()]
        return {
            "side": self.side,
            "phase": _l(self.phase),
            "template": _l(self.template),
            "envMin": _l(self.env_min),
            "envMax": _l(self.env_max),
            "envP25": _l(self.env_p25),
            "envP75": _l(self.env_p75),
            "livePhase": _l(self.live_phase),
            "liveValue": _l(self.live_value),
            "features": self.features.as_dict(),
            "beatCount": int(self.beat_count),
            "updatedBeat": bool(self.updated_beat),
        }
