# Missed-Dark Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a scheduled dark frame never arrives, flag every corrected frame in the resulting over-wide interval as `wide_interval` and emit a diagnostic, instead of silently interpolating the dark baseline across a doubled span.

**Architecture:** The dark schedule predicate moves out of `FrameClassificationStage` into a shared module. `DarkCorrectionStage` gains the schedule config, and on each interval close counts scheduled dark positions falling strictly inside `[left_abs, right_abs]`. One or more means darks were missed: every frame in the interval gets its `quality` escalated, and a `MissedDarkWarning` is appended. No changes to `timestamp_repair`, the sinks, or the runner.

**Tech Stack:** Python 3.12+, NumPy, pytest. Package `omotion.pipeline`.

**Spec:** `docs/superpowers/specs/2026-07-21-missed-dark-policy-design.md`

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `omotion/pipeline/dark_schedule.py` | create | Sole definition of the positional dark schedule: `is_dark_frame`, `missed_dark_ids` |
| `omotion/pipeline/quality.py` | create | Sole definition of `QUALITY_RANK` + `worse_of` escalation helper |
| `omotion/pipeline/batch.py` | modify | Add `MissedDarkWarning` event |
| `omotion/pipeline/stages/classify.py` | modify | Delegate `_is_dark` to the shared predicate |
| `omotion/pipeline/stages/side_avg.py` | modify | Import the shared rank instead of declaring its own |
| `omotion/pipeline/stages/dark.py` | modify | Accept schedule config; detect, mark, and report missed darks |
| `omotion/pipeline/factory.py` | modify | Pass `discard_count` / `dark_interval` to `DarkCorrectionStage` |
| `tests/test_pipeline/test_dark_schedule.py` | create | Schedule predicate + missed-id enumeration |
| `tests/test_pipeline/test_quality.py` | create | Rank ordering + escalation |
| `tests/test_pipeline/test_dark_correction_stage.py` | modify | Missed-dark detection, marking, event |
| `tests/test_pipeline/test_corrected_side_avg_stage.py` | modify | Side-average inherits `wide_interval` |

Two new modules rather than one: the dark schedule and the quality vocabulary are unrelated concerns. Both sit alongside the existing non-stage modules (`batch.py`, `pedestal.py`, `tee.py`).

---

### Task 1: Shared dark-schedule module

**Files:**
- Create: `omotion/pipeline/dark_schedule.py`
- Test: `tests/test_pipeline/test_dark_schedule.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_pipeline/test_dark_schedule.py`:

```python
"""The positional dark schedule — single source of truth for both
FrameClassificationStage (which types frames) and DarkCorrectionStage
(which detects darks that never arrived)."""

from omotion.pipeline.dark_schedule import is_dark_frame, missed_dark_ids


DEFAULTS = {"discard_count": 9, "dark_interval": 600}


def test_first_dark_is_at_discard_count_plus_one():
    assert is_dark_frame(10, **DEFAULTS)


def test_warmup_frames_are_not_dark():
    for abs_id in range(1, 10):
        assert not is_dark_frame(abs_id, **DEFAULTS)


def test_subsequent_darks_land_every_dark_interval():
    assert is_dark_frame(601, **DEFAULTS)
    assert is_dark_frame(1201, **DEFAULTS)


def test_ordinary_light_frames_are_not_dark():
    for abs_id in (11, 300, 600, 602, 1200):
        assert not is_dark_frame(abs_id, **DEFAULTS)


def test_missed_dark_ids_finds_the_gap():
    """Interval 10..1201 should have closed at 601; that dark never arrived."""
    assert missed_dark_ids(10, 1201, **DEFAULTS) == [601]


def test_missed_dark_ids_empty_for_a_nominal_interval():
    assert missed_dark_ids(601, 1201, **DEFAULTS) == []
    assert missed_dark_ids(10, 601, **DEFAULTS) == []


def test_missed_dark_ids_excludes_both_endpoints():
    """The bounding darks themselves are not 'missed'."""
    assert 10 not in missed_dark_ids(10, 1201, **DEFAULTS)
    assert 1201 not in missed_dark_ids(10, 1201, **DEFAULTS)


def test_missed_dark_ids_reports_every_gap():
    """A short interval makes multiple consecutive misses easy to construct:
    with dark_interval=3, darks fall at 10, 13, 16, 19."""
    cfg = {"discard_count": 9, "dark_interval": 3}
    assert missed_dark_ids(10, 19, **cfg) == [13, 16]


def test_adjacent_boundaries_have_nothing_between():
    assert missed_dark_ids(10, 11, **DEFAULTS) == []


def test_non_advancing_range_is_empty():
    """Stated contract, not an accident of range() — a swapped-argument bug at
    a call site would otherwise silently report 'nothing missed'."""
    assert missed_dark_ids(1201, 10, **DEFAULTS) == []


def test_zero_discard_count_puts_the_first_dark_at_frame_one():
    assert is_dark_frame(1, discard_count=0, dark_interval=600)
    assert not is_dark_frame(0, discard_count=0, dark_interval=600)


def test_non_positive_dark_interval_is_rejected():
    """Fail loudly: a bare ZeroDivisionError from inside a list comprehension
    is a poor diagnostic for a config error, and a negative interval would be
    silently wrong rather than an error at all."""
    for bad in (0, -1):
        with pytest.raises(ValueError):
            is_dark_frame(10, discard_count=9, dark_interval=bad)
```

Note the test module needs `import pytest` for the last test.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pipeline/test_dark_schedule.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'omotion.pipeline.dark_schedule'`

- [ ] **Step 3: Write minimal implementation**

Create `omotion/pipeline/dark_schedule.py`:

```python
"""The positional dark-frame schedule.

Single source of truth, imported by both FrameClassificationStage (which
types incoming frames) and DarkCorrectionStage (which detects scheduled
darks that never arrived). Two independent copies of this rule would drift
and silently corrupt dark correction — see docs/SciencePipeline.md §4.2.
"""

from __future__ import annotations


def is_dark_frame(abs_id: int, *, discard_count: int, dark_interval: int) -> bool:
    """True when abs_id lands on a scheduled dark position.

    Per SciencePipeline.md §4.2:
        n == discard_count + 1
        OR (n > discard_count + 1 AND (n - 1) mod dark_interval == 0)

    Raises ValueError for a non-positive dark_interval — this module is the
    single source of truth for two stages, so a bad config must fail loudly
    rather than as a ZeroDivisionError inside a list comprehension (or, for a
    negative value, as silently wrong output: Python's % takes the sign of the
    divisor).
    """
    if dark_interval <= 0:
        raise ValueError(f"dark_interval must be positive, got {dark_interval}")
    if abs_id == discard_count + 1:
        return True
    if abs_id <= discard_count + 1:
        return False
    return (abs_id - 1) % dark_interval == 0


def missed_dark_ids(left_abs: int, right_abs: int, *,
                    discard_count: int, dark_interval: int) -> list[int]:
    """Scheduled dark positions strictly between two interval boundaries.

    A closed interval should be bounded by consecutive scheduled darks. Any
    scheduled position falling *inside* it is a dark that never arrived, so
    the interval spans wider than nominal and its baseline interpolation is
    stretched across the gap. Both endpoints are excluded — they are the
    darks that did arrive.

    A non-advancing range (left_abs >= right_abs) yields an empty list. That
    is the correct answer, and is stated here so it reads as a contract rather
    than an accident of range() semantics.
    """
    return [
        n for n in range(int(left_abs) + 1, int(right_abs))
        if is_dark_frame(n, discard_count=discard_count,
                         dark_interval=dark_interval)
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_pipeline/test_dark_schedule.py -q`
Expected: PASS, 12 passed

- [ ] **Step 5: Commit**

```bash
git add omotion/pipeline/dark_schedule.py tests/test_pipeline/test_dark_schedule.py
git commit -m "feat: extract the dark schedule into a shared module (#175)"
```

---

### Task 2: Delegate classify.py to the shared predicate

**Files:**
- Modify: `omotion/pipeline/stages/classify.py` (`_is_dark`, around line 175)
- Test: `tests/test_pipeline/test_classify_stage.py` (existing, unchanged)

This is a pure refactor — behaviour must not change. The existing classify suite is the regression test.

- [ ] **Step 1: Run the existing suite to record the baseline**

Run: `python -m pytest tests/test_pipeline/test_classify_stage.py -q`
Expected: PASS (note the count; it must be identical after the change)

- [ ] **Step 2: Replace the inline predicate**

In `omotion/pipeline/stages/classify.py`, add to the imports near the top:

```python
from ..dark_schedule import is_dark_frame
```

Then replace the whole `_is_dark` method body:

```python
    def _is_dark(self, abs_id: int) -> bool:
        """Per SciencePipeline.md §4.2 — delegated to the shared schedule so
        DarkCorrectionStage cannot drift from this definition."""
        return is_dark_frame(abs_id,
                             discard_count=self.discard_count,
                             dark_interval=self.dark_interval)
```

- [ ] **Step 3: Run the suite to verify unchanged behaviour**

Run: `python -m pytest tests/test_pipeline/test_classify_stage.py -q`
Expected: PASS with the same count as Step 1

- [ ] **Step 4: Run the whole pipeline suite**

Run: `python -m pytest tests/test_pipeline/ -q`
Expected: PASS, no failures

- [ ] **Step 5: Commit**

```bash
git add omotion/pipeline/stages/classify.py
git commit -m "refactor: classify delegates to the shared dark schedule (#175)"
```

---

### Task 3: Shared quality rank module

**Files:**
- Create: `omotion/pipeline/quality.py`
- Test: `tests/test_pipeline/test_quality.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_pipeline/test_quality.py`:

```python
"""Quality vocabulary — ranking and worst-wins escalation."""

from omotion.pipeline.quality import QUALITY_RANK, worse_of


def test_rank_order():
    assert (QUALITY_RANK["ok"]
            < QUALITY_RANK["ts_corrected"]
            < QUALITY_RANK["nan_filled"]
            < QUALITY_RANK["wide_interval"])


def test_wide_interval_outranks_nan_filled():
    """A nan_filled camera emits NaN and drops out of the side average on its
    own; a wide-interval camera contributes real-looking numbers built on a
    stretched baseline. The flag that silently biases the aggregate must win,
    or the side-average row would hide it."""
    assert worse_of("nan_filled", "wide_interval") == "wide_interval"
    assert worse_of("wide_interval", "nan_filled") == "wide_interval"


def test_worse_of_picks_the_higher_rank():
    assert worse_of("ok", "ts_corrected") == "ts_corrected"
    assert worse_of("nan_filled", "ok") == "nan_filled"


def test_worse_of_is_stable_for_equal_values():
    assert worse_of("ok", "ok") == "ok"
    assert worse_of("wide_interval", "wide_interval") == "wide_interval"


def test_unknown_value_is_preserved_over_ok():
    """An unrecognised string ranks 0 and so ties with "ok", and a tie returns
    the first argument. That ordering matters: escalating an unknown flag with
    "ok" must not erase it, because we cannot know its severity.

    This is also the documented hazard of the rank table — a stale consumer
    holding its own copy would rank a NEWER known value (like wide_interval)
    as 0, i.e. as the best quality rather than the worst. That is why the rank
    lives in one place and is imported, never re-declared.
    """
    assert worse_of("something_new", "ok") == "something_new"
    assert worse_of("ok", "something_new") == "ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pipeline/test_quality.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'omotion.pipeline.quality'`

- [ ] **Step 3: Write minimal implementation**

Create `omotion/pipeline/quality.py`:

```python
"""Per-frame quality vocabulary.

Single source of truth for the quality values and their severity ordering.
Import QUALITY_RANK — never re-declare it. `worse_of` treats an unrecognised
value as rank 0 ("ok"), so a stale copy of this table would rank a newer value
as the BEST quality rather than the worst, inverting its meaning.

Values, in increasing severity:
    ok             — nothing wrong
    ts_corrected   — timestamp replaced by re-anchoring interpolation
    nan_filled     — synthetic row standing in for a frame that never arrived
    wide_interval  — real measurement, but its dark baseline was interpolated
                     across an interval that spans one or more missing darks
"""

from __future__ import annotations


QUALITY_RANK: dict[str, int] = {
    "ok": 0,
    "ts_corrected": 1,
    "nan_filled": 2,
    "wide_interval": 3,
}


def worse_of(a: str, b: str) -> str:
    """Return whichever quality value is more severe (ties return `a`).

    An unrecognised value ranks 0, so it ties with "ok" and is returned in
    preference to it — an unknown flag must never be silently overwritten
    with "ok", since we cannot know its severity.
    """
    return a if QUALITY_RANK.get(a, 0) >= QUALITY_RANK.get(b, 0) else b
```

Do **not** add special-casing for unknown values. The tie rule already produces
the desired behaviour, and the extra branches are unrequested complexity.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_pipeline/test_quality.py -q`
Expected: PASS, 5 passed

- [ ] **Step 5: Commit**

```bash
git add omotion/pipeline/quality.py tests/test_pipeline/test_quality.py
git commit -m "feat: shared quality rank with wide_interval (#175)"
```

---

### Task 4: Point side_avg.py at the shared rank

**Files:**
- Modify: `omotion/pipeline/stages/side_avg.py:37-39` and `:240`
- Test: `tests/test_pipeline/test_corrected_side_avg_stage.py` (existing, unchanged)

- [ ] **Step 1: Replace the local rank table**

In `omotion/pipeline/stages/side_avg.py`, delete these three lines (37-39):

```python
# Higher rank = worse quality; the side average inherits the worst quality
# of any camera that contributed to it. Mirrors sinks._QUALITY_RANK.
_QUALITY_RANK = {"ok": 0, "ts_corrected": 1, "nan_filled": 2}
```

(The "Mirrors sinks._QUALITY_RANK" claim is stale — `sinks.py` lost that constant in `5c3e107`.)

Add to the imports near the top, next to `from ..batch import ...`:

```python
from ..quality import worse_of
```

- [ ] **Step 2: Use the shared helper**

In `_ingest_corrected`, replace these lines (around 239-241):

```python
        fq = str(getattr(f, "quality", "ok") or "ok")
        if _QUALITY_RANK.get(fq, 0) > _QUALITY_RANK.get(rec["quality"], 0):
            rec["quality"] = fq
```

with:

```python
        # The side average inherits the worst quality of any camera that
        # contributed to it.
        fq = str(getattr(f, "quality", "ok") or "ok")
        rec["quality"] = worse_of(rec["quality"], fq)
```

- [ ] **Step 3: Run the side-average suites**

Run: `python -m pytest tests/test_pipeline/test_corrected_side_avg_stage.py tests/test_pipeline/test_side_avg_stage.py -q`
Expected: PASS, no failures

- [ ] **Step 4: Commit**

```bash
git add omotion/pipeline/stages/side_avg.py
git commit -m "refactor: side_avg imports the shared quality rank (#175)"
```

---

### Task 5: MissedDarkWarning event

**Files:**
- Modify: `omotion/pipeline/batch.py` (after `DarkIntegrityWarning`, around line 54)
- Test: `tests/test_pipeline/test_batch.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline/test_batch.py`:

```python
def test_missed_dark_warning_carries_the_gap():
    from omotion.pipeline.batch import BatchEvent, MissedDarkWarning

    ev = MissedDarkWarning(
        side="left", cam_id=3, expected_abs_ids=[601],
        left_abs=10, right_abs=1201, n_missed=1,
    )
    assert isinstance(ev, BatchEvent)
    assert ev.expected_abs_ids == [601]
    assert ev.n_missed == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pipeline/test_batch.py::test_missed_dark_warning_carries_the_gap -q`
Expected: FAIL — `ImportError: cannot import name 'MissedDarkWarning'`

- [ ] **Step 3: Write minimal implementation**

In `omotion/pipeline/batch.py`, immediately after the `DarkIntegrityWarning` class:

```python
@dataclass
class MissedDarkWarning(BatchEvent):
    """One or more scheduled dark frames never arrived, so the interval that
    closed spans wider than nominal and its baseline was interpolated across
    the gap. Frames are still emitted, flagged quality="wide_interval" — this
    is a diagnostic event, not a drop signal."""
    side: str
    cam_id: int
    expected_abs_ids: list[int]
    left_abs: int
    right_abs: int
    n_missed: int
```

No runner or sink change is needed: `runner.py:159` routes any event that is neither `LiveEmit` nor `IntervalClosed` to the `"diagnostics"` channel, and the diagnostics sinks tally by event type.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_pipeline/test_batch.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add omotion/pipeline/batch.py tests/test_pipeline/test_batch.py
git commit -m "feat: MissedDarkWarning diagnostic event (#175)"
```

---

### Task 6: Detect, mark, and report missed darks

**Files:**
- Modify: `omotion/pipeline/stages/dark.py` — imports (line 20), `DarkCorrectionStage.__init__`, `_emit_interval` (line 423)
- Test: `tests/test_pipeline/test_dark_correction_stage.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline/test_dark_correction_stage.py`:

```python
def _stage_with_schedule(dark_interval):
    """dark_interval=3 puts scheduled darks at abs_id 10, 13, 16, 19 —
    compact enough to construct a missed dark in a handful of frames."""
    return DarkCorrectionStage(
        realtime_estimator=HybridRealtimePredictor(),
        batch_estimator=LinearInterpolation(),
        discard_count=9,
        dark_interval=dark_interval,
    )


def _interval_batch(frame_types, abs_ids):
    n = len(abs_ids)
    mean = np.full((n, 2, 8), 500.0, dtype=np.float32)
    std = np.full((n, 2, 8), 20.0, dtype=np.float32)
    for i, ft in enumerate(frame_types):
        if ft == "dark":
            mean[i, :, :] = 100.0
            std[i, :, :] = 10.0
    return _batch(n, frame_types, abs_ids, mean_raw=mean, std_raw=std)


def test_nominal_interval_leaves_quality_untouched():
    """Darks at 10 and 13 are consecutive scheduled positions — nothing missed."""
    batch = _interval_batch(["dark", "light", "light", "dark"], [10, 11, 12, 13])
    _stage_with_schedule(3).process(batch)

    frames = [f for e in batch.events if isinstance(e, IntervalClosed)
              for f in e.corrected_batch.frames]
    assert frames, "expected an interval to close"
    assert all(f.quality == "ok" for f in frames)


def test_missed_dark_flags_every_frame_in_the_widened_interval():
    """The dark at 13 never arrives, so the interval closes 10..16 instead."""
    batch = _interval_batch(
        ["dark", "light", "light", "light", "light", "dark"],
        [10, 11, 12, 14, 15, 16],
    )
    _stage_with_schedule(3).process(batch)

    frames = [f for e in batch.events if isinstance(e, IntervalClosed)
              for f in e.corrected_batch.frames]
    assert frames, "expected an interval to close"
    assert all(f.quality == "wide_interval" for f in frames)


def test_missed_dark_emits_a_warning_event():
    from omotion.pipeline.batch import MissedDarkWarning

    batch = _interval_batch(
        ["dark", "light", "light", "light", "light", "dark"],
        [10, 11, 12, 14, 15, 16],
    )
    _stage_with_schedule(3).process(batch)

    warnings = [e for e in batch.events if isinstance(e, MissedDarkWarning)]
    assert len(warnings) == 1
    w = warnings[0]
    assert w.expected_abs_ids == [13]
    assert w.n_missed == 1
    assert (w.left_abs, w.right_abs) == (10, 16)
    assert w.side == "left" and w.cam_id == 0


def test_missed_dark_escalates_over_an_existing_quality():
    """A frame already flagged nan_filled ends up wide_interval, since that is
    the flag that biases the aggregate."""
    batch = _interval_batch(
        ["dark", "light", "light", "light", "light", "dark"],
        [10, 11, 12, 14, 15, 16],
    )
    batch.quality = np.array(
        ["ok", "nan_filled", "ok", "ok", "ok", "ok"], dtype="<U14")
    _stage_with_schedule(3).process(batch)

    frames = [f for e in batch.events if isinstance(e, IntervalClosed)
              for f in e.corrected_batch.frames]
    assert all(f.quality == "wide_interval" for f in frames)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_pipeline/test_dark_correction_stage.py -q -k "missed or nominal_interval"`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'discard_count'`

- [ ] **Step 3: Accept the schedule config**

In `omotion/pipeline/stages/dark.py`, extend the import on line 20:

```python
from ..batch import (
    DarkIntegrityWarning, FrameBatch, IntervalClosed, MissedDarkWarning,
    TerminalDarkResult,
)
```

Add two more imports below it:

```python
from ..dark_schedule import missed_dark_ids
from ..quality import worse_of
```

Then extend `DarkCorrectionStage.__init__` — add the two parameters and store them:

```python
    def __init__(self, *,
                 realtime_estimator: HybridRealtimePredictor,
                 batch_estimator: LinearInterpolation,
                 pedestals: Optional[SensorPedestals] = None,
                 realtime_history_size: int = 4,
                 integrity_max_above_pedestal: float = 5.0,
                 discard_count: int = 9,
                 dark_interval: int = 600):
        self._realtime = realtime_estimator
        self._batch = batch_estimator
        self._pedestals = pedestals or SensorPedestals(left=64.0, right=64.0)
        self._discard_count = int(discard_count)
        self._dark_interval = int(dark_interval)
        self._history = DarkHistory(max_darks=realtime_history_size)
```

(Leave the remaining body of `__init__` exactly as it is.)

- [ ] **Step 4: Detect, mark, and report in `_emit_interval`**

Replace the body of `_emit_interval`:

```python
    def _emit_interval(
        self,
        key: "tuple[str, int]",
        interval: "Interval",
        events: list,
    ) -> None:
        """Correct interval and emit IntervalClosed with raw CorrectedInterval.

        Downstream stages handle shot-noise, BFI/BVI, and the dark-frame
        quadratic stencil.

        A closed interval should be bounded by consecutive scheduled darks. Any
        scheduled position falling inside it is a dark that never arrived, so
        the baseline was interpolated across the gap — every frame is flagged
        `wide_interval` and a MissedDarkWarning is raised (#175).
        """
        side, cam_id = key
        corrected = self._batch.correct_interval(interval, side=side, cam_id=cam_id)
        corrected.left_t = interval.left.obs.t

        missed = missed_dark_ids(
            interval.left_abs, interval.right_abs,
            discard_count=self._discard_count,
            dark_interval=self._dark_interval,
        )
        if missed:
            for f in corrected.frames:
                f.quality = worse_of(f.quality, "wide_interval")
            events.append(MissedDarkWarning(
                side=side, cam_id=int(cam_id),
                expected_abs_ids=list(missed),
                left_abs=int(interval.left_abs),
                right_abs=int(interval.right_abs),
                n_missed=len(missed),
            ))
            logger.warning(
                "missed dark: side=%s cam=%d — scheduled dark(s) at %s never "
                "arrived; interval %d..%d spans the gap, baseline interpolated "
                "across %d frames instead of the nominal %d. Frames flagged "
                "wide_interval.",
                side, int(cam_id), missed,
                int(interval.left_abs), int(interval.right_abs),
                int(interval.right_abs) - int(interval.left_abs),
                self._dark_interval,
            )

        events.append(IntervalClosed(corrected_batch=corrected))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_pipeline/test_dark_correction_stage.py -q`
Expected: PASS, no failures

- [ ] **Step 6: Run the whole pipeline suite**

Run: `python -m pytest tests/test_pipeline/ -q`
Expected: PASS, no failures. In particular `test_golden_replay.py` must still pass — its fixture has no missing darks, so no frame should acquire a `wide_interval` flag.

- [ ] **Step 7: Commit**

```bash
git add omotion/pipeline/stages/dark.py tests/test_pipeline/test_dark_correction_stage.py
git commit -m "feat: flag intervals that span a missed dark (#175)"
```

---

### Task 7: Wire the schedule through the factory

**Files:**
- Modify: `omotion/pipeline/factory.py` (the `DarkCorrectionStage(...)` call)
- Test: `tests/test_pipeline/test_factory.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline/test_factory.py`:

```python
def test_dark_correction_receives_the_dark_schedule():
    """DarkCorrectionStage must see the same schedule as the classifier, or it
    cannot tell a missed dark from a nominal interval."""
    from omotion.pipeline.stages.dark import DarkCorrectionStage

    meta = ScanMetadata(
        scan_id="x", subject_id="y", operator="z",
        started_at_iso="2026-05-22T00:00:00Z", duration_sec=60,
        left_camera_mask=0xFF, right_camera_mask=0xFF, reduced_mode=False,
    )
    pipeline = default_pipeline(
        metadata=meta, calibration=_trivial_calibration(),
        pedestals=SensorPedestals(left=64.0, right=64.0),
        discard_count=9, dark_interval=123,
    )
    dark = next(s for s in pipeline.stages
                if isinstance(s, DarkCorrectionStage))
    assert dark._discard_count == 9
    assert dark._dark_interval == 123
```

This mirrors the construction the other tests in the file use — there is no
`_default_pipeline` helper; they call `default_pipeline(...)` directly.
`ScanMetadata`, `default_pipeline`, `SensorPedestals` and `_trivial_calibration`
are all already imported/defined in this module.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pipeline/test_factory.py::test_dark_correction_receives_the_dark_schedule -q`
Expected: FAIL — `assert 600 == 123` (the stage is still on its default)

- [ ] **Step 3: Pass the schedule through**

In `omotion/pipeline/factory.py`, extend the `DarkCorrectionStage(...)` construction:

```python
        DarkCorrectionStage(
            realtime_estimator=HybridRealtimePredictor(),
            batch_estimator=LinearInterpolation(),
            pedestals=pedestals,
            realtime_history_size=realtime_dark_history_size,
            discard_count=discard_count,
            dark_interval=dark_interval,
        ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_pipeline/test_factory.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add omotion/pipeline/factory.py tests/test_pipeline/test_factory.py
git commit -m "feat: pass the dark schedule to DarkCorrectionStage (#175)"
```

---

### Task 8: Side average inherits the flag

**Files:**
- Test: `tests/test_pipeline/test_corrected_side_avg_stage.py`

No production change expected — this proves the flag reaches the reduced-mode record, which is the only artifact carrying `quality` since `5c3e107` dropped the corrected-CSV column.

- [ ] **Step 1: Write the test**

Append to `tests/test_pipeline/test_corrected_side_avg_stage.py`:

```python
def test_side_average_inherits_wide_interval_from_one_camera():
    """One degraded camera flags the whole side-average row — it contributes
    real-looking numbers built on a stretched baseline, so the aggregate is
    biased even though the other camera is clean."""
    stage = _stage()
    good = _ef(12, 5.0, "left", 0, 2.0, 20.0)
    degraded = _ef(12, 5.0, "left", 1, 6.0, 60.0)
    degraded.quality = "wide_interval"
    b = _batch([_interval(10, 20, [good]), _interval(10, 20, [degraded])])
    stage.process(b)

    frames = _avg_frames(b)
    assert frames, "expected a side-average frame"
    assert frames[0].quality == "wide_interval"
```

- [ ] **Step 2: Run the test**

Run: `python -m pytest tests/test_pipeline/test_corrected_side_avg_stage.py -q`
Expected: PASS (Task 4 already routed this through `worse_of`)

If it FAILS, the escalation in `_ingest_corrected` is wrong — revisit Task 4 Step 2 before continuing.

- [ ] **Step 3: Commit**

```bash
git add tests/test_pipeline/test_corrected_side_avg_stage.py
git commit -m "test: side average inherits wide_interval (#175)"
```

---

### Task 9: Documentation

**Files:**
- Modify: `docs/SciencePipeline.md` — §2.2 BatchEvent table, §4.2 dark schedule, and the quality-value list

- [ ] **Step 1: Add the event to the BatchEvent table (§2.2)**

Add a row after the `DarkIntegrityWarning` row:

```markdown
| `MissedDarkWarning(...)` | `DarkCorrectionStage` (on interval close) | `"diagnostics"` |
```

- [ ] **Step 2: Document the schedule's new home (§4.2)**

Add after the dark-schedule definition:

```markdown
The predicate lives in `omotion/pipeline/dark_schedule.py` and is imported by
both `FrameClassificationStage` (typing frames) and `DarkCorrectionStage`
(detecting scheduled darks that never arrived). It is defined once — two copies
would drift and silently corrupt dark correction.
```

- [ ] **Step 3: Add a quality-vocabulary subsection**

`SciencePipeline.md` currently has **no** list of `quality` values — the only
mention is line 582, describing `session_data` columns. Add a new subsection
immediately before that `session_data` description:

```markdown
#### Per-frame quality values

`quality` is a single string per corrected frame, defined once in
`omotion/pipeline/quality.py`. In increasing severity:

| Value | Meaning |
|---|---|
| `ok` | Nothing wrong. |
| `ts_corrected` | Timestamp replaced by re-anchoring interpolation. |
| `nan_filled` | Synthetic row standing in for a frame that never arrived. |
| `wide_interval` | Real measurement, but its dark baseline was interpolated across an interval spanning one or more darks that never arrived. |

Frames are aggregated worst-wins: `SideAverageStage` gives a side-average row
the most severe quality of any camera that contributed to it.

`wide_interval` frames are **kept, not discarded** — valid darks still bound the
widened interval on both sides, so the data is degraded rather than
uncorrectable. One missed dark degrades roughly `2 x dark_interval` frames,
about 30 s at the defaults. It ranks above `nan_filled` because it is the only
value that silently biases an aggregate: a NaN frame drops out of the side
average on its own, whereas this one contributes real-looking numbers built on a
stretched baseline.

Note the value reaches only `session_data.quality` in the scan DB — the
corrected CSV's quality column was removed in `5c3e107`. The `MissedDarkWarning`
diagnostic event and the scan-summary tally are the surfaces independent of that.
```

- [ ] **Step 4: Commit**

```bash
git add docs/SciencePipeline.md
git commit -m "docs: missed-dark policy and the wide_interval quality (#175)"
```

---

### Task 10: Full verification

- [ ] **Step 1: Run the software-only suite**

Run: `python -m pytest tests/ -m "not console and not sensor and not destructive and not slow" -q`
Expected: PASS. Baseline before this work was 575 passed; expect that plus the new tests, with no failures.

- [ ] **Step 2: Confirm the golden replay is untouched**

Run: `git status --porcelain tests/test_pipeline/data/`
Expected: empty. The golden fixture has no missing darks, so it must not have been regenerated. If it shows as modified, the detection is firing on a nominal interval — stop and diagnose.

- [ ] **Step 3: Push and open the PR**

```bash
git push -u origin feature/175-missed-dark-policy

cat > /tmp/pr175.md <<'BODY'
Refs #175

## Problem

A scheduled dark frame that never arrives (dropped frame, camera dropout,
stale-rejected frame) does not close its interval. The next dark, one full
`dark_interval` later, closes it instead — so the interval spans roughly double
its nominal width and every light frame in it has its dark baseline
interpolated across the gap. At the defaults that silently degrades ~1200
frames, about 30 s of a scan, with nothing recorded.

## Change

`DarkCorrectionStage` now knows the dark schedule. When an interval closes it
counts scheduled dark positions falling strictly inside `[left_abs, right_abs]`;
one or more means darks were missed. Those frames are **kept and flagged**
`quality="wide_interval"`, and a `MissedDarkWarning` diagnostic is emitted.

Frames are kept rather than discarded because valid darks still bound the
widened interval on both sides — the data is degraded, not uncorrectable. (The
terminal-dark-missing case, which has no right boundary at all, keeps its
existing discard behaviour.)

`wide_interval` ranks above `nan_filled`: a NaN frame drops out of the side
average on its own, whereas a wide-interval frame contributes real-looking
numbers built on a stretched baseline, so it is the value that actually biases
the aggregate.

## Notes for review

- The dark-schedule predicate moved to `omotion/pipeline/dark_schedule.py` and
  is imported by both the classifier and the dark stage. Two copies would drift
  and regrow this bug.
- `_QUALITY_RANK` moved to `omotion/pipeline/quality.py`. `side_avg.py`'s comment
  claiming it mirrored `sinks._QUALITY_RANK` was stale — `5c3e107` removed that
  constant along with the corrected-CSV quality column.
- Detection deliberately does **not** re-type synthetic NaN-fill rows as `"dark"`
  (the approach originally sketched in #175). That would risk a NaN
  `DarkObservation` poisoning `DarkHistory` and both adjacent interval
  boundaries. Schedule-based detection also catches missed darks from any cause,
  not just the NaN-fill path.
- No sink or runner changes: `runner.py` already routes unrecognised events to
  `"diagnostics"`, and the diagnostics sinks tally by type.
- Since `5c3e107`, `quality` reaches only `session_data.quality` in the scan DB.
  Consumers reading the corrected CSV see degraded data unflagged; the
  `MissedDarkWarning` and scan-summary tally are the independent surfaces.

Design: `docs/superpowers/specs/2026-07-21-missed-dark-policy-design.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
BODY

gh pr create -R OpenwaterHealth/openmotion-sdk --base next \
  --head feature/175-missed-dark-policy \
  --title "feat: flag corrected intervals that span a missed dark" \
  --body-file /tmp/pr175.md
```

`Refs #175`, never `Closes` — merge must not auto-close the ticket, which lives on
through pre-release validation. **Use `--body-file`, never `--body @-`**: `gh` does
not support `@-`, and will silently post the literal two-character string while
still returning a success URL. Verify after posting with
`gh pr view <n> --json body`.

---

## Hardware verification (post-merge, optional)

Nothing in this plan requires hardware. The one open empirical question — how much
accuracy a doubled interval actually costs — is a follow-up, not a gate, since the
policy keeps and flags unconditionally. Answering it needs a ~2 minute scan
(`dark_interval` 600 gives 5-6 darks in that time, versus only two in a 20 s scan),
then a comparison of per-camera dark baselines across consecutive intervals.
