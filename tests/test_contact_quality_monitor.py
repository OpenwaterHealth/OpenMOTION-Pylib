"""Tests for omotion/contact_quality.py — shared CQ semantics + live monitor.

Pure-software; no hardware, no Qt. Thresholds and values are in
background-subtracted DN, matching ContactQualityWorkflow.
"""

import math

import numpy as np
import pytest

from omotion.contact_quality import (
    REASON_AMBIENT_LIGHT,
    REASON_NO_SIGNAL,
    REASON_OK,
    REASON_POOR_CONTACT,
    CQThresholds,
    evaluate_reason,
    is_ambient_light,
    is_poor_contact,
)


THRESHOLDS = CQThresholds.from_sequences([3.0] * 8, [15.0] * 8)


def test_thresholds_index_per_camera():
    t = CQThresholds.from_sequences([1.0, 2.0], [10.0, 20.0])
    assert t.dark_for(0) == 1.0
    assert t.dark_for(1) == 2.0
    assert t.light_for(1) == 20.0


def test_thresholds_fail_open_out_of_range():
    """Out-of-range indices must return values that can never trip, matching
    the legacy _ContactQualitySink lookup."""
    t = CQThresholds.from_sequences([1.0], [10.0])
    assert t.dark_for(7) == math.inf      # nothing exceeds inf
    assert t.light_for(7) == 0.0          # nothing falls below 0


def test_is_ambient_light_only_above_threshold():
    assert is_ambient_light(3.5, THRESHOLDS, 0) is True
    assert is_ambient_light(3.0, THRESHOLDS, 0) is False   # strict >
    assert is_ambient_light(-1.0, THRESHOLDS, 0) is False


def test_is_ambient_light_false_for_non_finite():
    assert is_ambient_light(float("nan"), THRESHOLDS, 0) is False


def test_is_poor_contact_only_below_threshold():
    assert is_poor_contact(5.0, THRESHOLDS, 0) is True
    assert is_poor_contact(15.0, THRESHOLDS, 0) is False    # strict <
    assert is_poor_contact(60.0, THRESHOLDS, 0) is False


def test_is_poor_contact_false_for_non_finite():
    assert is_poor_contact(float("nan"), THRESHOLDS, 0) is False


def test_evaluate_reason_precedence_matches_legacy_order():
    """no_signal > ambient_light > poor_contact > ok."""
    nan = float("nan")
    assert evaluate_reason(
        light_avg=nan, dark_max=99.0, thresholds=THRESHOLDS, cam_id=0
    ) == REASON_NO_SIGNAL
    # ambient wins over poor contact when both conditions hold
    assert evaluate_reason(
        light_avg=1.0, dark_max=99.0, thresholds=THRESHOLDS, cam_id=0
    ) == REASON_AMBIENT_LIGHT
    assert evaluate_reason(
        light_avg=1.0, dark_max=0.0, thresholds=THRESHOLDS, cam_id=0
    ) == REASON_POOR_CONTACT
    assert evaluate_reason(
        light_avg=60.0, dark_max=0.0, thresholds=THRESHOLDS, cam_id=0
    ) == REASON_OK
