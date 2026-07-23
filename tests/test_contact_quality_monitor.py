"""Tests for omotion/contact_quality.py — shared CQ semantics + live monitor.

Pure-software; no hardware, no Qt. Thresholds and values are in
background-subtracted DN, matching ContactQualityWorkflow.
"""

import logging
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


THRESHOLDS = CQThresholds.from_sequences(
    [3.0, 3.0, 3.0, 3.0, 3.0, 9.0, 3.0, 3.0],
    [15.0, 15.0, 15.0, 15.0, 15.0, 40.0, 15.0, 15.0],
)


def test_thresholds_index_per_camera():
    t = CQThresholds.from_sequences([1.0, 2.0], [10.0, 20.0])
    assert t.dark_for(0) == 1.0
    assert t.dark_for(1) == 2.0
    assert t.light_for(1) == 20.0


def test_thresholds_fail_open_out_of_range():
    """Out-of-range indices — including negatives, which the legacy
    _ContactQualitySink lookup silently wrapped instead — must return
    values that can never trip."""
    t = CQThresholds.from_sequences([1.0], [10.0])
    assert t.dark_for(7) == math.inf      # nothing exceeds inf
    assert t.light_for(7) == 0.0          # nothing falls below 0
    # Legacy _ContactQualitySink indexed with a bare `self._dark[cam_id]`,
    # so a negative cam_id would wrap to the last element instead of
    # failing open. This is an intentional divergence, not a bug.
    assert t.dark_for(-1) == math.inf
    assert t.light_for(-1) == 0.0


def test_from_sequences_warns_on_wrong_length_but_fails_open(caplog):
    """A 6-element array must not fail silently — it should warn that
    cameras 6-7 are permanently unflaggable, while still failing open
    (never raising)."""
    with caplog.at_level(logging.WARNING, logger="openmotion.sdk.contact_quality"):
        t = CQThresholds.from_sequences([1.0] * 6, [10.0] * 6)
    assert "6 entries" in caplog.text
    assert "expected 8" in caplog.text
    assert t.dark_for(7) == math.inf
    assert t.light_for(7) == 0.0


def test_thresholds_empty_sequences_fail_open_for_every_camera():
    """Degenerate case: empty arrays are the documented fail-open contract
    taken to its limit — every camera reads back as ok."""
    t = CQThresholds.from_sequences([], [])
    assert evaluate_reason(
        light_avg=1.0, dark_max=99.0, thresholds=t, cam_id=0
    ) == REASON_OK


def test_thresholds_from_sequences_coerces_ints_to_float():
    """Config arrives from JSON as ints; from_sequences must coerce to
    float so comparisons behave consistently downstream."""
    t = CQThresholds.from_sequences([1, 2], [10, 20])
    assert isinstance(t.dark, tuple)
    assert t.dark_for(0) == 1.0
    assert isinstance(t.dark_for(0), float)
    assert t.light_for(1) == 20.0
    assert isinstance(t.light_for(1), float)


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


def test_predicates_use_the_cameras_own_threshold():
    """A mutation that ignores cam_id and always reads camera 0's threshold
    must fail here — per-camera routing is the whole point of the arrays."""
    assert is_ambient_light(5.0, THRESHOLDS, 0) is True    # cam 0 bar = 3.0
    assert is_ambient_light(5.0, THRESHOLDS, 5) is False   # cam 5 bar = 9.0
    assert is_poor_contact(20.0, THRESHOLDS, 0) is False
    assert is_poor_contact(20.0, THRESHOLDS, 5) is True


def test_infinite_reading_is_unusable_not_ambient_light():
    """An infinite reading is treated as unusable data, not as evidence of
    ambient light — matches legacy's math.isfinite guard exactly."""
    assert is_ambient_light(float("inf"), THRESHOLDS, 0) is False
    assert evaluate_reason(
        light_avg=float("inf"), dark_max=0.0, thresholds=THRESHOLDS, cam_id=0
    ) == REASON_NO_SIGNAL


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
