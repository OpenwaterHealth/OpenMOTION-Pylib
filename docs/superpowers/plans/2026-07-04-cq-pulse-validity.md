# CQ per-channel pulse-validity criterion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-camera "valid pulse train present for >75% of the CQ scan" criterion to the SDK's `ContactQualityWorkflow`, reusing the `#124` `PulseWaveformAnalyzer`.

**Architecture:** Add an additive `PulseWaveformAnalyzer.beat_coverage()` returning a `PulseCoverage` (coverage fraction + autocorrelation periodicity). The CQ sink buffers per-`(side,cam)` `bfi_live` from the `live` channel and, at `result()`, marks a channel `no_pulse` unless `coverage > min_coverage AND periodicity >= 0.45`. Default-off via a new `check(evaluate_pulse=False)` param, so existing behavior is untouched.

**Tech Stack:** Python 3.12+, numpy, pytest. Pure-software (no hardware). Work in worktree `C:\Users\ethan\Projects\openmotion-sdk\.claude\worktrees\cq-pulse-validity` on branch `feature/126-cq-pulse-validity`.

**Spec:** `docs/superpowers/specs/2026-07-04-cq-pulse-validity-design.md`

---

## Conventions for every task

- Run all commands **from the worktree root**: `C:\Users\ethan\Projects\openmotion-sdk\.claude\worktrees\cq-pulse-validity`.
- Test runner (this repo pins `-m 'not fpga and not imu'` and `log_cli`): use the module path directly, e.g. `python -m pytest tests/test_pulse_coverage.py -v`. Do **not** pipe pytest through `Select-Object` (per SDK memory).
- Line length: match surrounding code (~88–90 cols is fine here; flake8 is not enforced).
- Commit after each task with the shown message. `Refs #126` in each commit body.

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `omotion/pulse/types.py` | `PulseCoverage` dataclass (numpy-free snapshot type) | Modify |
| `omotion/pulse/analyzer.py` | `beat_coverage()` method on `PulseWaveformAnalyzer` | Modify |
| `omotion/pulse/__init__.py` | export `PulseCoverage` | Modify |
| `omotion/ContactQualityWorkflow.py` | buffer `bfi_live`, per-channel pulse verdict, new `CamCQResult` fields + `check()` params | Modify |
| `tests/test_pulse_coverage.py` | `beat_coverage` unit tests + CSV regression | Create |
| `tests/test_contact_quality_workflow.py` | sink pulse tests + backward-compat guards | Modify |
| `tests/data/cq_pulse_reference_bfi.csv` | 16-channel bench BFI fixture (already generated) | (present) |

---

## Task 1: `PulseCoverage` dataclass + export

**Files:**
- Modify: `omotion/pulse/types.py`
- Modify: `omotion/pulse/__init__.py`
- Test: `tests/test_pulse_coverage.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_pulse_coverage.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pulse_coverage.py::test_pulse_coverage_defaults_are_not_valid -v`
Expected: FAIL — `ImportError: cannot import name 'PulseCoverage'`.

- [ ] **Step 3: Add the dataclass**

In `omotion/pulse/types.py`, after the `PulseFeatures` dataclass (before `PulseAnalysis`), add:

```python
@dataclass
class PulseCoverage:
    """Per-channel pulse-presence verdict from
    ``PulseWaveformAnalyzer.beat_coverage`` — used by the contact-quality check
    (issue #126), NOT the live pulse view.

    ``coverage`` is the fraction of the scan spanned by in-band cardiac beats
    (Σ in-band inter-onset intervals / total scan duration). ``periodicity`` is
    the autocorrelation strength (0..1) that rejects band-limited noise. A
    channel is ``valid`` only when ``coverage > min_coverage`` AND
    ``periodicity >= PulseWaveformAnalyzer.PERIODICITY_MIN``.
    """
    coverage:    float = NAN   # 0..1, NaN when unmeasurable
    periodicity: float = 0.0   # autocorrelation strength 0..1
    hr_bpm:      float = NAN   # 60 / median in-band RR (NaN if <1 beat)
    beat_count:  int = 0       # in-band beats counted
    valid:       bool = False
```

(`NAN` is already defined at the top of `types.py`.)

- [ ] **Step 4: Export it**

In `omotion/pulse/__init__.py`, update the `types` import and `__all__`:

```python
from .types import PulseAnalysis, PulseFeatures, PulseCoverage
```
```python
__all__ = [
    "synth_beat", "synth_bfi", "synth_pair", "SHAPE_PRESETS",
    "PulseWaveformAnalyzer", "PulseAnalysis", "PulseFeatures", "PulseCoverage",
]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_pulse_coverage.py::test_pulse_coverage_defaults_are_not_valid -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add omotion/pulse/types.py omotion/pulse/__init__.py tests/test_pulse_coverage.py
git commit -m "feat(pulse): add PulseCoverage dataclass for CQ pulse-validity (Refs #126)"
```

---

## Task 2: `beat_coverage()` method + behavior tests

**Files:**
- Modify: `omotion/pulse/analyzer.py`
- Test: `tests/test_pulse_coverage.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pulse_coverage.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_pulse_coverage.py -v -k beat_coverage`
Expected: FAIL — `AttributeError: 'PulseWaveformAnalyzer' object has no attribute 'beat_coverage'`.

- [ ] **Step 3: Implement `beat_coverage`**

In `omotion/pulse/analyzer.py`:

First extend the import at the top of the file:
```python
from .types import PulseAnalysis, PulseFeatures, PulseCoverage
```

Then add this method to `PulseWaveformAnalyzer`, immediately after `snapshot(self)` returns (i.e. after the `snapshot` method, before `_features`):

```python
    def beat_coverage(self, total_duration_s: float, *,
                      min_coverage: float = 0.75) -> PulseCoverage:
        """Fraction of ``total_duration_s`` spanned by in-band cardiac beats.

        Reuses the same band-limit + autocorrelation + foot segmentation as
        ``snapshot`` but deliberately skips the per-beat morphology QC
        (``_beat_passes_qc``): for a contact-quality *presence* gate that QC
        wrongly rejects individual beats on noisier channels, punching gaps in
        the coverage. Noise rejection is instead delegated to ``periodicity``
        (the autocorrelation strength), exactly as the ``reliable`` flag does.

        ``total_duration_s`` must be the scan light-capture span (supplied by
        the caller), NOT the analyzer's own buffer span — otherwise a channel
        that drops out mid-scan is measured only against its short buffer and
        passes spuriously.

        Returns a :class:`~omotion.pulse.types.PulseCoverage`. Never raises on
        degenerate input (too few samples, no beats, non-positive duration).
        """
        t = self._t
        v = self._v
        if t.size < 8 or not (total_duration_s > 0):
            return PulseCoverage(coverage=0.0, periodicity=0.0,
                                 hr_bpm=float("nan"), beat_count=0, valid=False)
        fs = self._estimate_fs()
        vfill = _nan_interp(v)
        sm = self._band_limit(vfill, fs)
        period, periodicity = _autocorr_period(
            sm, fs, self.min_bpm, self.max_bpm)
        if period is None:
            refractory = int(fs * 60.0 / self.max_bpm)
        else:
            refractory = max(1, int(0.6 * period))
        onsets = _segment_feet(sm, vfill, refractory)
        if onsets.size < 2:
            return PulseCoverage(coverage=0.0, periodicity=float(periodicity),
                                 hr_bpm=float("nan"), beat_count=0, valid=False)
        onset_t = t[onsets]
        lo = 60.0 / self.max_bpm
        hi = 60.0 / self.min_bpm
        rr = [float(t1 - t0) for t0, t1 in zip(onset_t[:-1], onset_t[1:])
              if lo <= (t1 - t0) <= hi]
        coverage = float(sum(rr)) / float(total_duration_s)
        hr_bpm = 60.0 / float(np.median(rr)) if rr else float("nan")
        valid = coverage > min_coverage and periodicity >= self.PERIODICITY_MIN
        return PulseCoverage(coverage=coverage, periodicity=float(periodicity),
                             hr_bpm=hr_bpm, beat_count=len(rr),
                             valid=bool(valid))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_pulse_coverage.py -v -k beat_coverage`
Expected: PASS (6 tests).

- [ ] **Step 5: Confirm the live pulse path is unregressed**

Run: `python -m pytest tests/test_pulse_analyzer.py -v`
Expected: PASS (no change to `snapshot`/`_features`).

- [ ] **Step 6: Commit**

```bash
git add omotion/pulse/analyzer.py tests/test_pulse_coverage.py
git commit -m "feat(pulse): PulseWaveformAnalyzer.beat_coverage for pulse-presence gate (Refs #126)"
```

---

## Task 3: CSV regression fixture test

**Files:**
- Present: `tests/data/cq_pulse_reference_bfi.csv` (tidy `camera,side,time_s,bfi`, 16 channels, ~15 s bench capture)
- Test: `tests/test_pulse_coverage.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pulse_coverage.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails first for the right reason, then passes**

Run: `python -m pytest tests/test_pulse_coverage.py::test_reference_capture_all_channels_valid_at_75pct -v`
Expected: PASS immediately (the fixture is real known-good data; `beat_coverage` already exists). If it FAILS, print `failures` — any channel below 0.75 indicates a metric regression, not a test bug.

- [ ] **Step 3: Commit**

```bash
git add tests/data/cq_pulse_reference_bfi.csv tests/test_pulse_coverage.py
git commit -m "test(pulse): vendor bench BFI fixture; assert 16/16 channels valid (Refs #126)"
```

---

## Task 4: `CamCQResult` pulse fields (backward-compatible)

**Files:**
- Modify: `omotion/ContactQualityWorkflow.py:32-43`
- Test: `tests/test_contact_quality_workflow.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_contact_quality_workflow.py`:

```python
def test_cam_cq_result_has_pulse_fields_defaulting_unevaluated():
    from omotion.ContactQualityWorkflow import CamCQResult
    r = CamCQResult(
        side="left", cam_id=0, passed=True,
        light_avg_dn=20.0, light_std_dn=2.5, dark_max_dn=1.0, dark_std_dn=2.5,
        reason="ok",
    )
    assert r.pulse_valid is False
    assert math.isnan(r.pulse_coverage)
    assert math.isnan(r.pulse_hr_bpm)
    assert math.isnan(r.pulse_periodicity)
```

Add `import math` at the top of the test file if not already present.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_contact_quality_workflow.py::test_cam_cq_result_has_pulse_fields_defaulting_unevaluated -v`
Expected: FAIL — `TypeError`/`AttributeError` (fields absent).

- [ ] **Step 3: Add the fields**

In `omotion/ContactQualityWorkflow.py`, extend `CamCQResult` (fields have defaults so existing positional/keyword construction keeps working):

```python
@dataclass
class CamCQResult:
    """Per-camera contact-quality verdict (DN-scale)."""

    side:        str    # "left" or "right"
    cam_id:      int    # 0-based camera index within module
    passed:      bool
    light_avg_dn: float # mean of rolling-window light-frame subtracted_mean (NaN when no data)
    light_std_dn: float # mean of rolling-window light-frame std_raw (NaN when no data)
    dark_max_dn:  float # max of dark-frame subtracted_mean (NaN when no data)
    dark_std_dn:  float # std_raw recorded with dark_max_dn (NaN when no data)
    reason:      str    # "ok" | "poor_contact" | "ambient_light" | "no_signal" | "no_pulse"
    # ── pulse-validity criterion (issue #126); NaN/False when not evaluated ──
    pulse_valid:       bool = False
    pulse_coverage:    float = float("nan")   # Σ in-band RR / scan span, 0..1
    pulse_hr_bpm:      float = float("nan")
    pulse_periodicity: float = float("nan")
```

- [ ] **Step 4: Run test to verify it passes; confirm existing CQ tests unaffected**

Run: `python -m pytest tests/test_contact_quality_workflow.py -v`
Expected: PASS (new test + all pre-existing tests green — the new fields are defaulted).

- [ ] **Step 5: Commit**

```bash
git add omotion/ContactQualityWorkflow.py tests/test_contact_quality_workflow.py
git commit -m "feat(cq): add pulse_* fields to CamCQResult (default unevaluated) (Refs #126)"
```

---

## Task 5: Sink pulse evaluation + `check()` params

**Files:**
- Modify: `omotion/ContactQualityWorkflow.py` (`_ContactQualitySink`, `ContactQualityWorkflow.check`)
- Test: `tests/test_contact_quality_workflow.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_contact_quality_workflow.py`. First a helper that builds a run of light frames carrying per-`(side,cam)` `bfi_live` and a passing `mean_dc_rt`:

```python
import numpy as np
from omotion.pulse.synth import synth_bfi


def _pulse_light_batch(t, bfi_2x8, mean_dc=20.0):
    """FrameBatch of len(t) light frames.

    ``bfi_2x8`` is (n_frames, 2, 8) per-(side,cam) bfi_live. mean_dc_rt and
    subtracted_mean are set to a constant that passes the signal-level checks,
    so the pulse criterion is what decides the verdict.
    """
    from omotion.pipeline.batch import FrameBatch
    n = len(t)
    rows = n * 16
    row_frame = np.repeat(np.arange(n), 16)
    cam_ids = np.tile(np.arange(8, dtype=np.int8), n * 2)
    side_ids = np.tile(np.repeat(np.array([0, 1], dtype=np.int8), 8), n)
    mean = np.full((rows, 2, 8), mean_dc, dtype=np.float32)
    bfi_rows = bfi_2x8[row_frame].astype(np.float32)      # (rows, 2, 8)
    return FrameBatch(
        cam_ids=cam_ids,
        frame_ids=np.repeat(np.arange(n, dtype=np.uint8), 16),
        side_ids=side_ids,
        raw_histograms=None, temperature_c=None,
        timestamp_s=np.repeat(np.asarray(t, dtype=np.float64), 16),
        pdc=None, tcm=None, tcl=None,
        frame_type=np.repeat(np.array(["light"], dtype="<U8"), rows),
        subtracted_mean=mean.copy(),
        mean_dc_rt=mean.copy(),
        bfi_live=bfi_rows,
        std_raw=np.full((rows, 2, 8), 2.5, dtype=np.float32),
    )


def _pulse_bfi_2x8(duration_s=15.0, seed=0):
    t, v = synth_bfi(duration_s=duration_s, bpm=72, amp=2.0, baseline=5.0,
                     noise=0.05, seed=seed)
    bfi = np.repeat(v[:, None, None], 8, axis=2)          # broadcast to (n,1,8)
    bfi = np.repeat(bfi, 2, axis=1)                        # (n, 2, 8)
    return t, bfi


def test_cq_sink_marks_channel_ok_with_valid_pulse():
    from omotion.ContactQualityWorkflow import _ContactQualitySink
    sink = _ContactQualitySink(
        dark_thresholds=[3.0] * 8, light_thresholds=[15.0] * 8,
        evaluate_pulse=True, pulse_min_coverage=0.75,
    )
    sink.on_scan_start(None)
    t, bfi = _pulse_bfi_2x8()
    sink.consume("live", _pulse_light_batch(t, bfi))
    res = sink.result(left_mask=0x01, right_mask=0, duration_sec=15.0)
    cam = res.per_camera[("left", 0)]
    assert cam.reason == "ok"
    assert cam.passed is True
    assert cam.pulse_valid is True
    assert cam.pulse_coverage > 0.75


def test_cq_sink_marks_no_pulse_when_signal_ok_but_flat():
    from omotion.ContactQualityWorkflow import _ContactQualitySink
    sink = _ContactQualitySink(
        dark_thresholds=[3.0] * 8, light_thresholds=[15.0] * 8,
        evaluate_pulse=True, pulse_min_coverage=0.75,
    )
    sink.on_scan_start(None)
    n = 600
    t = np.arange(n) * 0.025
    flat = np.full((n, 2, 8), 5.0)                        # signal ok, no pulse
    sink.consume("live", _pulse_light_batch(t, flat))
    res = sink.result(left_mask=0x01, right_mask=0, duration_sec=15.0)
    cam = res.per_camera[("left", 0)]
    assert cam.reason == "no_pulse"
    assert cam.passed is False
    assert cam.pulse_valid is False


def test_cq_sink_poor_contact_takes_precedence_over_pulse():
    from omotion.ContactQualityWorkflow import _ContactQualitySink
    sink = _ContactQualitySink(
        dark_thresholds=[3.0] * 8, light_thresholds=[15.0] * 8,
        evaluate_pulse=True, pulse_min_coverage=0.75,
    )
    sink.on_scan_start(None)
    t, bfi = _pulse_bfi_2x8()
    # mean_dc below the light threshold -> poor_contact, regardless of pulse.
    sink.consume("live", _pulse_light_batch(t, bfi, mean_dc=5.0))
    res = sink.result(left_mask=0x01, right_mask=0, duration_sec=15.0)
    cam = res.per_camera[("left", 0)]
    assert cam.reason == "poor_contact"
    assert math.isnan(cam.pulse_coverage)   # pulse not evaluated on a failed channel


def test_cq_sink_evaluate_pulse_off_leaves_pulse_unevaluated():
    from omotion.ContactQualityWorkflow import _ContactQualitySink
    sink = _ContactQualitySink(
        dark_thresholds=[3.0] * 8, light_thresholds=[15.0] * 8,
    )  # evaluate_pulse defaults False
    sink.on_scan_start(None)
    n = 600
    t = np.arange(n) * 0.025
    flat = np.full((n, 2, 8), 5.0)
    sink.consume("live", _pulse_light_batch(t, flat))
    res = sink.result(left_mask=0x01, right_mask=0, duration_sec=15.0)
    cam = res.per_camera[("left", 0)]
    assert cam.reason == "ok"                # unchanged legacy behavior
    assert cam.passed is True
    assert cam.pulse_valid is False
    assert math.isnan(cam.pulse_coverage)


def test_cq_workflow_check_evaluate_pulse_end_to_end():
    from unittest.mock import MagicMock
    from omotion.ContactQualityWorkflow import ContactQualityWorkflow
    t, bfi = _pulse_bfi_2x8()

    def _drive(request):
        sink = request.sinks[0]
        sink.on_scan_start(None)
        sink.consume("live", _pulse_light_batch(t, bfi))
        sink.on_complete()
        return True

    fake = MagicMock()
    fake.await_complete = MagicMock()
    fake.start_scan.side_effect = _drive
    cq = ContactQualityWorkflow(scan_workflow=fake)
    res = cq.check(
        duration_sec=15.0, rolling_window=10,
        dark_threshold_per_camera=[3.0] * 8,
        light_threshold_per_camera=[15.0] * 8,
        left_camera_mask=0x01, right_camera_mask=0,
        evaluate_pulse=True,
    )
    cam = res.per_camera[("left", 0)]
    assert cam.pulse_valid is True
    assert cam.reason == "ok"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_contact_quality_workflow.py -v -k "pulse or no_pulse"`
Expected: FAIL — `_ContactQualitySink.__init__() got an unexpected keyword argument 'evaluate_pulse'`.

- [ ] **Step 3: Implement the sink changes**

In `omotion/ContactQualityWorkflow.py`:

(a) Add the import near the top (after `from omotion.ScanWorkflow import run_collection_scan`):
```python
from omotion.pulse.analyzer import PulseWaveformAnalyzer
```

(b) Extend `_ContactQualitySink.__init__` signature and state:
```python
    def __init__(
        self,
        dark_thresholds: list[float],
        light_thresholds: list[float],
        rolling_window: int = 10,
        evaluate_pulse: bool = False,
        pulse_min_coverage: float = 0.75,
    ) -> None:
        self._dark = list(dark_thresholds)
        self._light = list(light_thresholds)
        self._window_size = max(1, int(rolling_window))
        self._evaluate_pulse = bool(evaluate_pulse)
        self._pulse_min_coverage = float(pulse_min_coverage)
```
Then, alongside the existing per-channel dicts, add:
```python
        # (side, cam_id) -> list[float]   light-frame (timestamp_s, bfi_live)
        self._pulse_t: dict = {}
        self._pulse_bfi: dict = {}
        # global light-frame timestamp span (coverage denominator)
        self._light_t_min: float = float("inf")
        self._light_t_max: float = float("-inf")
```

(c) Clear them in `on_scan_start` (append after the existing `.clear()` calls):
```python
        self._pulse_t.clear()
        self._pulse_bfi.clear()
        self._light_t_min = float("inf")
        self._light_t_max = float("-inf")
```

(d) In `consume`, inside the **light-frame `else` branch**, after the existing
`self._light_count[key] = ...` line, add pulse buffering:
```python
                if (self._evaluate_pulse
                        and getattr(batch, "bfi_live", None) is not None):
                    bfi_v = float(batch.bfi_live[i, side_idx, cam_id])
                    ts = float(batch.timestamp_s[i])
                    if math.isfinite(bfi_v) and math.isfinite(ts):
                        self._pulse_t.setdefault(key, []).append(ts)
                        self._pulse_bfi.setdefault(key, []).append(bfi_v)
                        if ts < self._light_t_min:
                            self._light_t_min = ts
                        if ts > self._light_t_max:
                            self._light_t_max = ts
```

(e) In `result`, compute the shared denominator once before the per-camera loop:
```python
        span = self._light_t_max - self._light_t_min
        pulse_total_s = span if (math.isfinite(span) and span > 0) else duration_sec
```
Then, inside the per-camera loop, **after** the existing signal-level
`reason, passed = ...` branch computes the verdict and **before** the
`per_cam[key] = CamCQResult(...)` construction, insert:
```python
                pulse_valid = False
                pulse_cov = float("nan")
                pulse_hr = float("nan")
                pulse_per = float("nan")
                if self._evaluate_pulse and reason == "ok":
                    tbuf = self._pulse_t.get(key)
                    bbuf = self._pulse_bfi.get(key)
                    if tbuf is not None and len(tbuf) >= 8:
                        an = PulseWaveformAnalyzer(
                            side=side, raw_window_s=pulse_total_s + 1.0)
                        an.add_samples(tbuf, bbuf)
                        pc = an.beat_coverage(
                            total_duration_s=pulse_total_s,
                            min_coverage=self._pulse_min_coverage)
                        pulse_valid = pc.valid
                        pulse_cov = pc.coverage
                        pulse_hr = pc.hr_bpm
                        pulse_per = pc.periodicity
                        if not pc.valid:
                            reason, passed = "no_pulse", False
                    # <8 pulse samples: leave "ok"/passed as-is (fail-open on
                    # missing pulse data — cannot evaluate what wasn't captured).
```
Finally extend the `CamCQResult(...)` construction with the four new fields:
```python
                per_cam[key] = CamCQResult(
                    side=side,
                    cam_id=cam_id,
                    passed=passed,
                    light_avg_dn=light_avg,
                    light_std_dn=light_std,
                    dark_max_dn=dark_max,
                    dark_std_dn=dark_std,
                    reason=reason,
                    pulse_valid=pulse_valid,
                    pulse_coverage=pulse_cov,
                    pulse_hr_bpm=pulse_hr,
                    pulse_periodicity=pulse_per,
                )
```

- [ ] **Step 4: Implement the `check()` params**

Extend `ContactQualityWorkflow.check` signature with the two new keyword args (after `right_camera_mask`):
```python
        left_camera_mask: int,
        right_camera_mask: int,
        evaluate_pulse: bool = False,
        pulse_min_coverage: float = 0.75,
    ) -> ContactQualityResult:
```
Add to the docstring Parameters section:
```
        evaluate_pulse:
            When True, additionally evaluate a per-camera cardiac pulse-validity
            criterion on ``bfi_live``: a channel that passes the signal-level
            checks is marked ``no_pulse`` unless a valid pulse train covers more
            than ``pulse_min_coverage`` of the scan. Requires a long enough
            ``duration_sec`` (~15 s) and a valid calibration loaded (so
            ``bfi_live`` is meaningful). Default False keeps legacy behavior.
        pulse_min_coverage:
            Minimum beat-coverage fraction (0..1) for ``pulse_valid``. Default 0.75.
```
And pass them into the sink construction:
```python
        sink = _ContactQualitySink(
            dark_thresholds=dark_threshold_per_camera,
            light_thresholds=light_threshold_per_camera,
            rolling_window=rolling_window,
            evaluate_pulse=evaluate_pulse,
            pulse_min_coverage=pulse_min_coverage,
        )
```

- [ ] **Step 5: Run the new tests, then the whole CQ file**

Run: `python -m pytest tests/test_contact_quality_workflow.py -v`
Expected: PASS — new pulse tests green AND all pre-existing tests still green (backward compatibility).

- [ ] **Step 6: Commit**

```bash
git add omotion/ContactQualityWorkflow.py tests/test_contact_quality_workflow.py
git commit -m "feat(cq): per-channel pulse-validity criterion via beat_coverage (Refs #126)"
```

---

## Task 6: Full pure-software suite + ticket update

- [ ] **Step 1: Run the full no-hardware suite touched by this change**

Run:
```bash
python -m pytest tests/test_pulse_coverage.py tests/test_pulse_analyzer.py tests/test_contact_quality_workflow.py -v
```
Expected: all PASS. No hardware markers involved.

- [ ] **Step 2: Sanity-check the broader pure-software set didn't regress**

Run: `python -m pytest tests/test_pipeline/ -q -m "not console and not sensor and not destructive"`
Expected: PASS (this change is additive to `pulse` + CQ; pipeline suite should be untouched).

- [ ] **Step 3: Push the branch and open a draft PR into `feature/124`**

```bash
git push -u origin feature/126-cq-pulse-validity
gh pr create -R OpenwaterHealth/openmotion-sdk \
  --base feature/124-pulse-waveform-analysis \
  --head feature/126-cq-pulse-validity \
  --title "feat(cq): per-channel pulse-validity criterion (beat coverage >75%)" \
  --body "Refs #126. Adds PulseWaveformAnalyzer.beat_coverage + a per-camera pulse-validity criterion in ContactQualityWorkflow (default off via check(evaluate_pulse=False)). Bench-validated: 16/16 known-good channels pass at 0.75; flat/noise fail. SDK-only; bloodflow-app wiring is a separate follow-up. Targets feature/124 because it reuses the #124 PulseWaveformAnalyzer.

Design: docs/superpowers/specs/2026-07-04-cq-pulse-validity-design.md" \
  --draft
```
(If `feature/124` has merged to `next` by execution time, retarget `--base next`.)

- [ ] **Step 4: Update issue #126 + board**

```bash
gh issue comment 126 -R OpenwaterHealth/openmotion-sdk --body "PR opened (draft). All pulse-coverage + CQ tests green; 16/16 bench channels valid at 0.75; existing CQ tests unchanged with evaluate_pulse=False."
```
Move board item `PVTI_lADOAif52c4BVgTuzgxue3k` to **In review**:
```bash
gh project item-edit --id PVTI_lADOAif52c4BVgTuzgxue3k --project-id PVT_kwDOAif52c4BVgTu \
  --field-id PVTSSF_lADOAif52c4BVgTuzhQ7qcU --single-select-option-id 5ef0dc97
```

- [ ] **Step 5: Note the follow-up**

The bloodflow-app wiring (config keys `cq_pulse_enabled` / `cq_pulse_duration_sec` / `cq_pulse_min_coverage`, lengthen the CQ capture when enabled, pass `evaluate_pulse=True` in `motion_connector.runContactQualityCheck`, surface `no_pulse` in the CQ modal) is a **separate** ticket/PR in `openmotion-bloodflow-app`. Do not implement it here.

---

## Self-review (completed by plan author)

- **Spec coverage:** analyzer method → Task 2; `PulseCoverage` → Task 1; sink buffering + denominator + result eval → Task 5; `CamCQResult` fields + `no_pulse` → Tasks 4–5; `check()` params + backward-compat → Tasks 4–5; tests incl. broadband-noise guard, dropout, `<8` samples, CSV regression → Tasks 2–3, 5; out-of-scope app wiring noted → Task 6.
- **Placeholder scan:** none — every code step shows full code; every command has expected output.
- **Type consistency:** `PulseCoverage(coverage, periodicity, hr_bpm, beat_count, valid)` identical across Tasks 1/2/5; `beat_coverage(total_duration_s, *, min_coverage)` identical in Tasks 2/5; `CamCQResult.pulse_valid/pulse_coverage/pulse_hr_bpm/pulse_periodicity` identical in Tasks 4/5; `_ContactQualitySink(..., evaluate_pulse, pulse_min_coverage)` identical in Task 5 tests + impl.
