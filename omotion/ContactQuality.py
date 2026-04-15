"""Per-camera contact-quality assessment.

Two warnings are emitted from raw (uncorrected) histogram means:

* AMBIENT_LIGHT -- laser-off ("dark") frame mean exceeds an ambient-light
  threshold, suggesting stray light is leaking into the sensor.
* POOR_CONTACT -- laser-on ("light") frame mean stays below a contact threshold
  for ``LOW_LIGHT_CONSEC_FRAMES`` consecutive frames, suggesting the laser or
  sensor is not coupled to the patient.

Thresholds are stored as **background-subtracted** values and compared against
``raw_mean - pedestal`` at evaluation time. This way, future changes to
``PEDESTAL_HEIGHT`` do not require re-tuning the threshold constants.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List

# Background-subtracted thresholds in raw DN. Add the current pedestal at
# comparison time to obtain the absolute DN threshold.
DARK_MEAN_THRESHOLD_DN: float = 10.0   # ambient-light warning cutoff
LIGHT_MEAN_THRESHOLD_DN: float = 30.0  # poor-contact warning cutoff
LOW_LIGHT_CONSEC_FRAMES: int = 6       # consecutive low-light frames to fire


class ContactQualityWarningType(str, Enum):
    AMBIENT_LIGHT = "ambient_light"
    POOR_CONTACT = "poor_contact"


@dataclass(frozen=True)
class ContactQualityWarning:
    camera_id: int
    warning_type: ContactQualityWarningType
    value: float        # the raw DN that triggered the warning
    frame_index: int
    # Sensor side this camera belongs to ("left" / "right"). Default empty
    # string keeps the warning usable in contexts where side is not known
    # (e.g. bare unit tests of the monitor).
    side: str = ""


@dataclass
class ContactQualityResult:
    """Aggregated result returned by ``run_contact_quality_check``."""
    ok: bool
    warnings: List[ContactQualityWarning] = field(default_factory=list)


@dataclass
class _CameraState:
    ambient_latched: bool = False
    ambient_clear_streak: int = 0
    low_light_streak: int = 0
    contact_latched: bool = False
    contact_clear_streak: int = 0


class ContactQualityMonitor:
    """Stateful per-camera contact-quality monitor."""

    def __init__(self, pedestal: float) -> None:
        self._pedestal = float(pedestal)
        self._state: Dict[int, _CameraState] = {}

    def reset(self, camera_id: int | None = None) -> None:
        if camera_id is None:
            self._state.clear()
        else:
            self._state.pop(camera_id, None)

    def _state_for(self, camera_id: int) -> _CameraState:
        s = self._state.get(camera_id)
        if s is None:
            s = _CameraState()
            self._state[camera_id] = s
        return s

    def update_dark(
        self,
        camera_id: int,
        raw_dark_mean: float,
        frame_index: int,
        side: str = "",
    ) -> List[ContactQualityWarning]:
        s = self._state_for(camera_id)
        out: List[ContactQualityWarning] = []
        threshold_abs = self._pedestal + DARK_MEAN_THRESHOLD_DN
        above = raw_dark_mean > threshold_abs
        if above:
            s.ambient_clear_streak = 0
            if not s.ambient_latched:
                s.ambient_latched = True
                out.append(ContactQualityWarning(
                    camera_id=camera_id,
                    warning_type=ContactQualityWarningType.AMBIENT_LIGHT,
                    value=float(raw_dark_mean),
                    frame_index=int(frame_index),
                    side=str(side),
                ))
        else:
            if s.ambient_latched:
                s.ambient_clear_streak += 1
                if s.ambient_clear_streak >= LOW_LIGHT_CONSEC_FRAMES:
                    s.ambient_latched = False
                    s.ambient_clear_streak = 0
        return out

    def update_light(
        self,
        camera_id: int,
        raw_light_mean: float,
        frame_index: int,
        side: str = "",
    ) -> List[ContactQualityWarning]:
        s = self._state_for(camera_id)
        out: List[ContactQualityWarning] = []
        threshold_abs = self._pedestal + LIGHT_MEAN_THRESHOLD_DN
        below = raw_light_mean < threshold_abs
        if below:
            s.low_light_streak += 1
            s.contact_clear_streak = 0
            if (
                not s.contact_latched
                and s.low_light_streak >= LOW_LIGHT_CONSEC_FRAMES
            ):
                s.contact_latched = True
                out.append(ContactQualityWarning(
                    camera_id=camera_id,
                    warning_type=ContactQualityWarningType.POOR_CONTACT,
                    value=float(raw_light_mean),
                    frame_index=int(frame_index),
                    side=str(side),
                ))
        else:
            s.low_light_streak = 0
            if s.contact_latched:
                s.contact_clear_streak += 1
                if s.contact_clear_streak >= LOW_LIGHT_CONSEC_FRAMES:
                    s.contact_latched = False
                    s.contact_clear_streak = 0
        return out
