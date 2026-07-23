"""Contact-quality semantics — thresholds, verdict, and the live monitor.

Two consumers share this module:

* :class:`~omotion.ContactQualityWorkflow.ContactQualityWorkflow` — the
  one-shot pre-scan check. Accumulates the *worst* value seen per camera
  across a short scan and rolls the two conditions into a single
  precedence-ordered verdict via :func:`evaluate_reason`.
* :class:`ContactQualityMonitor` — the live sink attached to a full scan.
  Tracks the *current* value per camera, debounces each condition
  independently, and reports edges through a callback.

Both read the same two DN-scale signals off the ``"live"`` channel and
compare them with the same two predicates, so a camera can never be judged
one way by the preflight check and another by the live monitor.

Thresholds are background-subtracted DN. See docs/SciencePipeline.md §11.2
and §11.3.
"""

from __future__ import annotations

import collections
import logging
import math
from dataclasses import dataclass

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

    Indices past the end of a sequence return a value that can never trip,
    reproducing the legacy ``_ContactQualitySink`` lookup exactly.
    """

    dark:  tuple[float, ...]
    light: tuple[float, ...]

    @classmethod
    def from_sequences(cls, dark, light) -> "CQThresholds":
        return cls(
            dark=tuple(float(v) for v in dark),
            light=tuple(float(v) for v in light),
        )

    def dark_for(self, cam_id: int) -> float:
        return self.dark[cam_id] if 0 <= cam_id < len(self.dark) else math.inf

    def light_for(self, cam_id: int) -> float:
        return self.light[cam_id] if 0 <= cam_id < len(self.light) else 0.0


def is_ambient_light(dark_max: float, thresholds: CQThresholds, cam_id: int) -> bool:
    """True when a dark frame's DN exceeds the camera's dark threshold."""
    return math.isfinite(dark_max) and dark_max > thresholds.dark_for(cam_id)


def is_poor_contact(light_avg: float, thresholds: CQThresholds, cam_id: int) -> bool:
    """True when the light DN average falls below the camera's light threshold."""
    return math.isfinite(light_avg) and light_avg < thresholds.light_for(cam_id)


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
