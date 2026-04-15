"""Per-camera contact-quality assessment.

Two warnings are emitted from raw (uncorrected) histogram means:

* AMBIENT_LIGHT -- laser-off ("dark") frame mean exceeds an ambient-light
  threshold, suggesting stray light is leaking into the sensor. This warning
  still fires **per-frame** (latching) as dark frames arrive.
* POOR_CONTACT -- the **average** of all laser-on ("light") frame means
  collected during a check falls below a contact threshold, suggesting the
  laser or sensor is not coupled to the patient. This warning is emitted
  once per camera by :py:meth:`ContactQualityMonitor.finalize`, which the
  caller is expected to invoke after acquisition completes.

Thresholds are stored as **background-subtracted** values and compared against
``raw_mean - pedestal`` at evaluation time. This way, future changes to
``PEDESTAL_HEIGHT`` do not require re-tuning the threshold constants.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

try:
    from omotion import _log_root
except Exception:  # pragma: no cover - avoid circular-import issues at load time
    _log_root = "openmotion.sdk"

logger = logging.getLogger(
    f"{_log_root}.ContactQuality" if _log_root else "ContactQuality"
)


def _label(side: str, camera_id: int) -> str:
    """Human-readable 1-indexed camera label like 'L4' or 'R2' (falls back to '?<id>').

    The raw ``camera_id`` remains 0-indexed everywhere it is reported on
    warning objects; only the user-facing string label is shifted to 1..8.
    """
    prefix = side.upper()[0] if side else "?"
    return f"{prefix}{int(camera_id) + 1}"

# Background-subtracted thresholds in raw DN. Add the current pedestal at
# comparison time to obtain the absolute DN threshold.
DARK_MEAN_THRESHOLD_DN: float = 10.0   # ambient-light warning cutoff
LIGHT_MEAN_THRESHOLD_DN: float = 30.0  # poor-contact warning cutoff
LOW_LIGHT_CONSEC_FRAMES: int = 6       # retained for legacy callers/tests


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
    # Human-readable description of any failure or notable condition. Empty
    # string when the check completed normally.
    error: str = ""


@dataclass
class _CameraState:
    # Ambient-light state (per-frame latching, unchanged behavior).
    ambient_latched: bool = False
    ambient_clear_streak: int = 0
    # Light-frame accumulators for averaged poor-contact evaluation.
    light_sum: float = 0.0
    light_count: int = 0
    # Side stored on first update so ``finalize`` / summaries can label.
    side: str = ""


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
        if side and not s.side:
            s.side = str(side)
        out: List[ContactQualityWarning] = []
        threshold_abs = self._pedestal + DARK_MEAN_THRESHOLD_DN
        above = raw_dark_mean > threshold_abs
        if above:
            s.ambient_clear_streak = 0
            if not s.ambient_latched:
                s.ambient_latched = True
                logger.info(
                    "ContactQuality: AMBIENT_LIGHT fired — %s raw_dark_mean=%.1f DN "
                    "> threshold=%.1f DN (pedestal=%.1f + DARK_MEAN_THRESHOLD_DN=%.1f) "
                    "immediate",
                    _label(side, camera_id),
                    float(raw_dark_mean),
                    threshold_abs,
                    self._pedestal,
                    DARK_MEAN_THRESHOLD_DN,
                )
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
                    logger.debug(
                        "ContactQuality: AMBIENT_LIGHT cleared for %s after %d clear frames",
                        _label(side, camera_id),
                        s.ambient_clear_streak,
                    )
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
        """Accumulate a light-frame mean for this camera.

        Poor-contact detection is no longer per-frame. We accumulate the
        mean here and evaluate the average in :py:meth:`finalize`. Always
        returns an empty list — the return type is preserved so existing
        callers (e.g. ``SciencePipeline``) that iterate/dispatch the result
        continue to work without code changes.
        """
        s = self._state_for(camera_id)
        if side and not s.side:
            s.side = str(side)
        s.light_sum += float(raw_light_mean)
        s.light_count += 1
        return []

    def finalize(self) -> List[ContactQualityWarning]:
        """Emit averaged poor-contact warnings for each camera observed.

        For each camera with at least one light-frame sample, computes the
        mean of ``raw_light_mean`` values and compares to
        ``pedestal + LIGHT_MEAN_THRESHOLD_DN``. Emits one POOR_CONTACT
        warning per camera whose average falls below the threshold.
        """
        out: List[ContactQualityWarning] = []
        threshold_abs = self._pedestal + LIGHT_MEAN_THRESHOLD_DN
        for cam_id, s in self._state.items():
            if s.light_count <= 0:
                continue
            avg = s.light_sum / s.light_count
            if avg < threshold_abs:
                logger.info(
                    "ContactQuality: POOR_CONTACT fired (averaged) — %s "
                    "avg_light_mean=%.1f DN over %d frames < threshold=%.1f DN",
                    _label(s.side, cam_id),
                    float(avg),
                    int(s.light_count),
                    threshold_abs,
                )
                out.append(ContactQualityWarning(
                    camera_id=cam_id,
                    warning_type=ContactQualityWarningType.POOR_CONTACT,
                    value=float(avg),
                    frame_index=int(s.light_count),
                    side=s.side,
                ))
        return out

    def per_camera_summary(self) -> List[dict]:
        """Return per-camera statistics for end-of-check logging.

        Each entry: ``{"label": "L4", "side": "left", "cam_id": 4,
        "light_frames": int, "light_mean_avg": float | None,
        "ambient_latched": bool}``. ``light_mean_avg`` is ``None`` when no
        light frames were observed for that camera.
        """
        rows: List[dict] = []
        for cam_id, s in sorted(self._state.items(), key=lambda kv: (kv[1].side, kv[0])):
            avg: Optional[float] = (
                s.light_sum / s.light_count if s.light_count > 0 else None
            )
            rows.append({
                "label": _label(s.side, cam_id),
                "side": s.side,
                "cam_id": int(cam_id),
                "light_frames": int(s.light_count),
                "light_mean_avg": avg,
                "ambient_latched": bool(s.ambient_latched),
            })
        return rows
