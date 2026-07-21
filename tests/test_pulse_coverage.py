"""Unit tests for PulseWaveformAnalyzer.beat_coverage + PulseCoverage.

Pure-software (no hardware). These back the SDK contact-quality pulse-validity
criterion (issue #126): a channel is "valid" when a real, regular cardiac pulse
train covers more than min_coverage of the scan.
"""

import math

import numpy as np
import pytest

from omotion.pulse import PulseCoverage, PulseWaveformAnalyzer
from omotion.pulse.synth import synth_bfi


def test_pulse_coverage_defaults_are_not_valid():
    pc = PulseCoverage()
    assert pc.valid is False
    assert pc.beat_count == 0
    assert math.isnan(pc.coverage)


def _analyzer_with(t, v):
    an = PulseWaveformAnalyzer(raw_window_s=1e9)  # never trim in the CQ path
    an.add_samples(t, v)
    return an


def test_beat_coverage_valid_on_synthetic_pulse():
    t, v = synth_bfi(duration_s=15.0, bpm=72, amp=2.0, baseline=5.0,
                     noise=0.05, seed=0)
    pc = _analyzer_with(t, v).beat_coverage(15.0, min_coverage=0.75)
    assert pc.valid is True
    assert pc.coverage > 0.75
    assert pc.periodicity >= 0.45
    assert 60.0 <= pc.hr_bpm <= 90.0


def test_beat_coverage_invalid_on_flat_signal():
    rng = np.random.default_rng(1)
    t = np.arange(600) * 0.025
    v = 5.0 + rng.normal(0.0, 0.02, 600)
    pc = _analyzer_with(t, v).beat_coverage(15.0, min_coverage=0.75)
    assert pc.valid is False
    assert pc.periodicity < 0.45


def test_beat_coverage_high_coverage_noise_still_invalid_via_periodicity():
    # Broadband noise fakes high coverage (~0.8) but low periodicity — the
    # periodicity gate is what rejects it. Regression guard: coverage alone
    # is NOT sufficient.
    rng = np.random.default_rng(1)
    t = np.arange(600) * 0.025
    v = rng.normal(5.0, 0.5, 600)
    pc = _analyzer_with(t, v).beat_coverage(15.0, min_coverage=0.75)
    assert pc.periodicity < 0.45
    assert pc.valid is False


def test_beat_coverage_invalid_on_midscan_dropout():
    # Good pulse for the first ~7.5 s, then frames stop (real dropout).
    # Denominator is the full 15 s scan -> coverage ~0.4 -> invalid.
    t, v = synth_bfi(duration_s=7.5, bpm=72, amp=2.0, baseline=5.0,
                     noise=0.05, seed=0)
    pc = _analyzer_with(t, v).beat_coverage(15.0, min_coverage=0.75)
    assert pc.valid is False
    assert pc.coverage < 0.6


def test_beat_coverage_handles_too_few_samples():
    t = np.arange(5) * 0.025
    v = np.ones(5)
    pc = _analyzer_with(t, v).beat_coverage(15.0)
    assert pc.valid is False
    assert pc.beat_count == 0
    assert pc.coverage == 0.0


def test_beat_coverage_zero_duration_is_invalid():
    t, v = synth_bfi(duration_s=15.0, seed=0)
    pc = _analyzer_with(t, v).beat_coverage(0.0)
    assert pc.valid is False


# ---------------------------------------------------------------------------
# Regression: real 16-channel bench capture (known-good contact, ~85 bpm)
# ---------------------------------------------------------------------------

import csv
from collections import defaultdict
from pathlib import Path

_FIXTURE = Path(__file__).parent / "data" / "cq_pulse_reference_bfi.csv"


def _load_reference_channels():
    """Return {(side, cam): (t_array, bfi_array)} from the bench fixture."""
    rows = defaultdict(list)
    with _FIXTURE.open(newline="") as fh:
        for r in csv.DictReader(fh):
            rows[(r["side"], int(r["camera"]))].append(
                (float(r["time_s"]), float(r["bfi"])))
    out = {}
    for key, samples in rows.items():
        samples.sort()
        t = np.array([s[0] for s in samples])
        v = np.array([s[1] for s in samples])
        out[key] = (t, v)
    return out


def test_reference_capture_all_channels_valid_at_75pct():
    channels = _load_reference_channels()
    assert len(channels) == 16
    all_t = np.concatenate([t for t, _ in channels.values()])
    total = float(all_t.max() - all_t.min())
    failures = []
    for (side, cam), (t, v) in sorted(channels.items()):
        an = PulseWaveformAnalyzer(side=side, raw_window_s=1e9)
        an.add_samples(t, v)
        pc = an.beat_coverage(total, min_coverage=0.75)
        if not pc.valid:
            failures.append((side, cam, round(pc.coverage, 3),
                             round(pc.periodicity, 3)))
    assert not failures, f"channels failed pulse-validity: {failures}"
