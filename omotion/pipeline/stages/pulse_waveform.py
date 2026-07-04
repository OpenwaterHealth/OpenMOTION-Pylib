"""PulseWaveformStage — cardiac pulse-waveform analysis in the live pipeline.

Consumes the per-side realtime averages that ``SideAverageStage`` emits as
``LiveEmit("live_side", SideAverageSample)`` (reduced/clinical mode), feeds each
side's blood-flow index into a ``PulseWaveformAnalyzer``, and periodically
appends ``LiveEmit("pulse", PulseAnalysis)`` for the pulse-view sink to consume.

Additive only: it reads existing events and appends new ones; it never mutates
the batch's per-frame arrays. Must run AFTER ``SideAverageStage`` (whose
live_side events it depends on) and before the final ``Tee("live")``.
"""

from __future__ import annotations

from ..batch import LiveEmit, SideAverageSample
from ...pulse.analyzer import PulseWaveformAnalyzer

_SIDE_INT_TO_STR = ("left", "right")


class PulseWaveformStage:
    name = "pulse_waveform"

    def __init__(self, *, enabled: bool = True, emit_every: int = 8,
                 phase_bins: int = 60, history_beats: int = 20,
                 min_bpm: float = 40.0, max_bpm: float = 180.0):
        self.enabled = bool(enabled)
        self.emit_every = max(1, int(emit_every))
        self._kw = dict(phase_bins=phase_bins, history_beats=history_beats,
                        min_bpm=min_bpm, max_bpm=max_bpm)
        self._reset_state()

    def _reset_state(self) -> None:
        self._analyzers = {
            0: PulseWaveformAnalyzer(side="left", **self._kw),
            1: PulseWaveformAnalyzer(side="right", **self._kw),
        }
        self._since_emit = {0: 0, 1: 0}

    def process(self, batch):
        if not self.enabled:
            return batch
        touched = set()
        # Snapshot the event list: we append our own LiveEmit("pulse", …)
        # events below and must not re-scan them.
        for event in list(getattr(batch, "events", ())):
            if not isinstance(event, LiveEmit) or event.channel != "live_side":
                continue
            sample = event.payload
            if not isinstance(sample, SideAverageSample):
                continue
            side = int(sample.side)
            if side not in self._analyzers:
                continue
            self._analyzers[side].add_samples([float(sample.t)],
                                              [float(sample.bfi)])
            self._since_emit[side] += 1
            touched.add(side)
        for side in sorted(touched):
            if self._since_emit[side] >= self.emit_every:
                self._since_emit[side] = 0
                self._emit(side, batch)
        return batch

    def _emit(self, side: int, batch) -> None:
        snap = self._analyzers[side].snapshot()
        batch.events.append(LiveEmit(channel="pulse", payload=snap))

    def on_scan_stop(self, batch) -> None:
        if not self.enabled:
            return
        for side in (0, 1):
            self._emit(side, batch)

    def reset(self) -> None:
        self._reset_state()
