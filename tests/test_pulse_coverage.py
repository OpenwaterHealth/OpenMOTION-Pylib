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
