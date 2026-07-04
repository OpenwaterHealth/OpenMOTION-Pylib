"""omotion.pulse — cardiac pulse-waveform analysis for the blood-flow index.

The blood-flow index (BFI) from OpenMotion's speckle system is pulsatile at
the cardiac frequency, analogous to diffuse-correlation-spectroscopy /
speckle-contrast cerebral-blood-flow waveforms and to photoplethysmography.

This package provides:

* ``synth``   — a pure-numpy generator of realistic pulsatile BFI time series
                (two-Gaussian beat model + HRV + noise) for tests and the
                app's no-hardware demo.
* ``analyzer``— ``PulseWaveformAnalyzer``: streaming beat segmentation, an
                ensemble-average "template" pulse, a min/max envelope, and
                pulse-shape morphology features (PI, RI, AUC, rise time, …).
* ``types``   — the ``PulseAnalysis`` / ``PulseFeatures`` snapshot dataclasses.

Everything here is numpy-only (no scipy) and Qt-free.
"""

from .synth import synth_beat, synth_bfi, synth_pair, SHAPE_PRESETS
from .analyzer import PulseWaveformAnalyzer
from .types import PulseAnalysis, PulseFeatures

__all__ = [
    "synth_beat", "synth_bfi", "synth_pair", "SHAPE_PRESETS",
    "PulseWaveformAnalyzer", "PulseAnalysis", "PulseFeatures",
]
