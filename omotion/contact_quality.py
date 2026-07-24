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

# Camera count per sensor module, and bloodflow-app's config/app_config.json
# keys that supply CQThresholds.from_sequences its two arrays — named here
# so the wrong-length warning can point a log reader straight at the config
# file.
_CAMERAS_PER_SENSOR = 8
_DARK_CONFIG_KEY = "cq_dark_threshold_per_camera"
_LIGHT_CONFIG_KEY = "cq_light_threshold_per_camera"


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
        for name, config_key, seq in (
            ("dark", _DARK_CONFIG_KEY, dark_t),
            ("light", _LIGHT_CONFIG_KEY, light_t),
        ):
            n = len(seq)
            if n < _CAMERAS_PER_SENSOR:
                logger.warning(
                    "CQ %s thresholds (%s) have %d entries, expected %d — "
                    "cameras with index >= %d will fail open (never flagged)",
                    name, config_key, n, _CAMERAS_PER_SENSOR, n,
                )
            elif n > _CAMERAS_PER_SENSOR:
                logger.warning(
                    "CQ %s thresholds (%s) have %d entries, expected %d — "
                    "entries past index %d are ignored",
                    name, config_key, n, _CAMERAS_PER_SENSOR, _CAMERAS_PER_SENSOR - 1,
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

    Consequence: time-to-transition is unbounded — a spurious disagreeing
    observation arriving more often than once per ``debounce`` postpones the
    edge indefinitely. This matters most on the clear edge.

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

    @property
    def debounce(self) -> int:
        """Effective debounce — always >= 1, whatever was passed in."""
        return self._debounce

    def reset(self) -> None:
        """Silent — no transition is returned. Callers mirroring latch
        state must clear their own view."""
        self._active = False
        self._streak = 0

    def observe(self, bad: bool) -> str:
        """Feed one observation; return activated / cleared / none."""
        bad = bool(bad)
        if bad == self._active:
            self._streak = 0
            return TRANSITION_NONE
        self._streak += 1
        if self._streak < self._debounce:
            return TRANSITION_NONE
        self._active = bad
        self._streak = 0
        return TRANSITION_ACTIVATED if self._active else TRANSITION_CLEARED


def _cams_from_masks(meta) -> set:
    """Set of (side, cam_id) the scan actually uses.

    Empty when ``meta`` is None (or carries no masks), which the monitor
    treats as "evaluate everything".
    """
    if meta is None:
        return set()
    cams = set()
    for side, attr in (("left", "left_camera_mask"), ("right", "right_camera_mask")):
        mask = int(getattr(meta, attr, 0) or 0)
        for cam_id in range(_CAMERAS_PER_SENSOR):
            if mask & (1 << cam_id):
                cams.add((side, cam_id))
    return cams


class ContactQualityMonitor:
    """Live contact-quality sink — reports per-camera transitions mid-scan.

    Attach by appending an instance to ``ScanRequest.sinks``. Subscribes to
    the ``"live"`` channel and reads the same two DN signals as the one-shot
    check:

      * ``frame_type == "dark"`` rows -> ``subtracted_mean``
        (``mean_raw - pedestal``), measuring ambient light against the
        zero-light pedestal.
      * every other non-warmup/non-stale row -> ``mean_dc_rt``
        (``mean_raw - predicted_dark_baseline``), measuring laser-driven
        signal above the just-measured dark. Non-finite values (early light
        frames before the first dark observation) are skipped.

    **Accumulation differs deliberately from the one-shot check.** The check
    keeps the worst value seen across its 1 s window; this keeps the
    *current* value, because a running dark max would latch an ambient
    warning for the remaining hours of a free-run scan.

    ``on_transition(side, cam_id, reason, value, active)`` fires on edges
    only. ``reason`` is ``REASON_AMBIENT_LIGHT`` or ``REASON_POOR_CONTACT``;
    ``REASON_NO_SIGNAL`` is deliberately never reported here — total loss of
    frames is the camera-dropout watchdog's job, and reporting it as poor
    contact would send the operator to fix the wrong thing.

    The callback is invoked on the pipeline runner thread. A GUI consumer
    must marshal to its own thread. Exceptions from the callback are logged
    and swallowed so a broken consumer cannot disable the sink.
    """

    channels = frozenset({"live"})

    def __init__(
        self,
        *,
        thresholds: CQThresholds,
        on_transition,
        rolling_window: int = 10,
        light_debounce: int = 80,
        dark_debounce: int = 1,
    ) -> None:
        self._thresholds = thresholds
        self._on_transition = on_transition
        self._window_size = max(1, int(rolling_window))
        self._light_debounce = max(1, int(light_debounce))
        self._dark_debounce = max(1, int(dark_debounce))
        # (side, cam_id) -> deque[float] of recent light-frame mean_dc_rt
        self._light_window: dict = {}
        # (side, cam_id, reason) -> CameraLatch
        self._latches: dict = {}
        # (side, cam_id) pairs in the scan mask; empty means "all"
        self._active_cams: set = set()

    def on_scan_start(self, meta) -> None:
        self._light_window.clear()
        self._latches.clear()
        self._active_cams = _cams_from_masks(meta)
        # Deliberate tripwire: this line is the evidence that live
        # contact-quality monitoring is actually attached. Its absence is
        # how the feature stayed silently dead for two months in 2026.
        logger.info(
            "live contact-quality monitor attached: %d camera(s), "
            "dark<=%s DN, light>=%s DN, debounce light=%d dark=%d",
            len(self._active_cams) or 2 * _CAMERAS_PER_SENSOR,
            list(self._thresholds.dark) or "n/a",
            list(self._thresholds.light) or "n/a",
            self._light_debounce,
            self._dark_debounce,
        )

    def consume(self, channel: str, batch) -> None:
        if channel != "live":
            return
        if getattr(batch, "subtracted_mean", None) is None:
            return
        if getattr(batch, "mean_dc_rt", None) is None:
            return
        for i, side_idx, cam_id, ft in batch.iter_rows(exclude={"warmup", "stale"}):
            if side_idx < 0 or not (0 <= cam_id < _CAMERAS_PER_SENSOR):
                continue
            side = _SIDE_NAMES[side_idx]
            key = (side, cam_id)
            if self._active_cams and key not in self._active_cams:
                continue
            if ft == "dark":
                value = float(batch.subtracted_mean[i, side_idx, cam_id])
                if not math.isfinite(value):
                    continue
                self._observe(
                    side, cam_id, REASON_AMBIENT_LIGHT,
                    is_ambient_light(value, self._thresholds, cam_id),
                    value, self._dark_debounce,
                )
            else:
                value = float(batch.mean_dc_rt[i, side_idx, cam_id])
                if not math.isfinite(value):
                    continue
                window = self._light_window.get(key)
                if window is None:
                    window = collections.deque(maxlen=self._window_size)
                    self._light_window[key] = window
                window.append(value)
                avg = sum(window) / len(window)
                self._observe(
                    side, cam_id, REASON_POOR_CONTACT,
                    is_poor_contact(avg, self._thresholds, cam_id),
                    avg, self._light_debounce,
                )

    def _observe(self, side, cam_id, reason, bad, value, debounce) -> None:
        latch_key = (side, cam_id, reason)
        latch = self._latches.get(latch_key)
        if latch is None:
            latch = CameraLatch(debounce)
            self._latches[latch_key] = latch
        transition = latch.observe(bad)
        if transition == TRANSITION_NONE:
            return
        active = transition == TRANSITION_ACTIVATED
        logger.info(
            "live CQ %s%d: %s %s (%.2f DN)",
            "L" if side == "left" else "R", cam_id + 1,
            reason, "RAISED" if active else "CLEARED", value,
        )
        try:
            self._on_transition(side, cam_id, reason, value, active)
        except Exception:
            # The runner disables a sink that raises. A broken consumer must
            # not take contact-quality monitoring down with it.
            logger.exception("contact-quality transition callback raised")

    def on_complete(self) -> None:
        pass
