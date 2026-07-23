"""Contact-quality semantics — thresholds, verdict, and the live monitor.

Two consumers share this module:

* :class:`~omotion.ContactQualityWorkflow.ContactQualityWorkflow` — the
  one-shot pre-scan check. Accumulates the *worst* value seen per camera
  across a short scan and rolls the two conditions into a single
  precedence-ordered verdict via :func:`evaluate_reason`.
* :class:`ContactQualityMonitor` — the live sink attached to a full scan.
  Tracks the *current* value per camera, debounces each condition
  independently, and reports edges through a callback.

Both read the same two DN-scale signals off the ``"live"`` channel and apply
the same two predicates, so the *ambient-light* and *poor-contact* verdicts
cannot drift apart between the preflight check and the live monitor.
``REASON_NO_SIGNAL`` is deliberately preflight-only: the live monitor skips
non-finite readings, because total loss of frames belongs to the consumer's
camera-dropout watchdog rather than to contact quality.

Thresholds are background-subtracted DN. See docs/SciencePipeline.md §11.2
and §11.3.
"""

from __future__ import annotations

import collections
import logging
import math
from dataclasses import dataclass
from typing import Sequence

logger = logging.getLogger("openmotion.sdk.contact_quality")

# Verdict vocabulary. These strings cross the SDK/app boundary — the
# bloodflow-app maps them to operator-facing text — so they are API.
REASON_OK            = "ok"
REASON_POOR_CONTACT  = "poor_contact"
REASON_AMBIENT_LIGHT = "ambient_light"
REASON_NO_SIGNAL     = "no_signal"

# CameraLatch.observe() return values.
TRANSITION_NONE      = "none"
TRANSITION_ACTIVATED = "activated"
TRANSITION_CLEARED   = "cleared"

_SIDE_NAMES = ("left", "right")


@dataclass(frozen=True)
class CQThresholds:
    """Per-camera DN thresholds with fail-open out-of-range behavior.

    ``dark`` is an upper bound — exceeding it means ambient light is leaking
    onto the sensor. ``light`` is a lower bound — falling below it means the
    laser is not coupling into tissue.

    Out-of-range indices — including negatives, which the legacy
    ``_ContactQualitySink`` lookup silently wrapped to the end of the list —
    return a value that can never trip.
    """

    dark:  tuple[float, ...]
    light: tuple[float, ...]

    @classmethod
    def from_sequences(cls, dark: Sequence[float], light: Sequence[float]) -> CQThresholds:
        dark_t = tuple(float(v) for v in dark)
        light_t = tuple(float(v) for v in light)
        for name, seq in (("dark", dark_t), ("light", light_t)):
            n = len(seq)
            if n < 8:
                logger.warning(
                    "CQ %s thresholds have %d entries, expected 8 — cameras "
                    "%d-7 will fail open (never flagged)",
                    name, n, n,
                )
            elif n > 8:
                logger.warning(
                    "CQ %s thresholds have %d entries, expected 8 — entries "
                    "past index 7 are ignored",
                    name, n,
                )
        return cls(dark=dark_t, light=light_t)

    def dark_for(self, cam_id: int) -> float:
        return self.dark[cam_id] if 0 <= cam_id < len(self.dark) else math.inf

    def light_for(self, cam_id: int) -> float:
        return self.light[cam_id] if 0 <= cam_id < len(self.light) else 0.0


def is_ambient_light(dark_dn: float, thresholds: CQThresholds, cam_id: int) -> bool:
    """True when a dark-frame DN reading exceeds the camera's dark threshold."""
    return math.isfinite(dark_dn) and dark_dn > thresholds.dark_for(cam_id)


def is_poor_contact(light_dn: float, thresholds: CQThresholds, cam_id: int) -> bool:
    """True when a light-frame DN reading falls below the camera's light threshold."""
    return math.isfinite(light_dn) and light_dn < thresholds.light_for(cam_id)


def evaluate_reason(
    *,
    light_avg: float,
    dark_max: float,
    thresholds: CQThresholds,
    cam_id: int,
) -> str:
    """Single precedence-ordered verdict for one camera.

    no_signal > ambient_light > poor_contact > ok. Used by the one-shot
    check; the live monitor uses the two predicates directly because a
    camera can be ambient-lit and poorly coupled at the same time and the
    UI renders those as separate rows.
    """
    if not math.isfinite(light_avg):
        return REASON_NO_SIGNAL
    if is_ambient_light(dark_max, thresholds, cam_id):
        return REASON_AMBIENT_LIGHT
    if is_poor_contact(light_avg, thresholds, cam_id):
        return REASON_POOR_CONTACT
    return REASON_OK


class CameraLatch:
    """Debounced edge detector for one camera / one condition.

    Flips only after ``debounce`` consecutive agreeing observations; any
    disagreeing observation resets the counter. ``debounce=1`` reproduces
    the legacy immediate latch/clear behavior.

    Returns a transition only on an edge — steady state returns
    ``TRANSITION_NONE`` so callers emit one event per genuine change
    rather than once per frame.
    """

    __slots__ = ("_debounce", "_active", "_streak")

    def __init__(self, debounce: int = 1) -> None:
        self._debounce = max(1, int(debounce))
        self._active = False
        self._streak = 0

    @property
    def active(self) -> bool:
        return self._active

    def reset(self) -> None:
        self._active = False
        self._streak = 0

    def observe(self, bad: bool) -> str:
        """Feed one observation; return activated / cleared / none."""
        if bool(bad) == self._active:
            self._streak = 0
            return TRANSITION_NONE
        self._streak += 1
        if self._streak < self._debounce:
            return TRANSITION_NONE
        self._active = bool(bad)
        self._streak = 0
        return TRANSITION_ACTIVATED if self._active else TRANSITION_CLEARED
