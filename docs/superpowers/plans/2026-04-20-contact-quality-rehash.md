# Contact Quality Rehash Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two general-purpose primitives to the `omotion` SDK data pipeline — a per-dark-frame callback and a toggleable rolling average over the last N uncorrected light samples — so contact-quality logic can live in app code instead of the SDK.

**Architecture:** Both primitives live in `SciencePipeline` (`omotion/MotionProcessing.py`). The dark-frame callback fires inside the existing dark-branch at the schedule check (no toggle — registration-gated). The rolling-average stage adds a per-`(side, cam_id)` `collections.deque(maxlen=N)` populated only on the light branch after `on_uncorrected_fn` fires. Both are plumbed up through the `create_science_pipeline` factory and `ScanWorkflow.start_scan`; the rolling-avg toggle and window size live on `ScanRequest`, consistent with existing per-scan config fields like `reduced_mode`.

**Tech Stack:** Python 3.12, `dataclasses`, `collections.deque`, `pytest` (no hardware required — tests drive `SciencePipeline` via `create_science_pipeline` + the hardware-free `feed_pipeline_from_csv` helper with the existing `tests/fixtures/single_cam_basic_left.csv` fixture).

**Spec:** [docs/superpowers/specs/2026-04-20-contact-quality-rehash-design.md](../specs/2026-04-20-contact-quality-rehash-design.md)

**Pipeline invariants this plan relies on** (from `tests/test_pipeline_csv.py`):
- Fixture `single_cam_basic_left.csv` has 12 frames with `DISCARD_COUNT=2`, `DARK_INTERVAL=5` → dark frames at absolute positions `{3, 6, 11}`, light frames at `{4, 5, 7, 8, 9, 10, 12}`.
- At frame 3 (first dark), there is no `_last_uncorrected` entry yet — the dark-repeat `on_uncorrected_fn` branch at `MotionProcessing.py:1182` therefore does NOT fire. Dark-repeat uncorrected samples only appear at frames `{6, 11}` for this fixture.
- Regular histogram bins `[400, 500)` → raw mean ≈ 449.5; after pedestal subtraction (`PEDESTAL_HEIGHT=64`), uncorrected `Sample.mean` ≈ 385.5.
- Dark histogram bins `[10, 20)` → raw u1 ≈ 14.5. Dark-frame samples are NOT pedestal-subtracted (`u1` is used directly as `mean`).

---

## Task 1: Add `is_dark` field to `Sample`

Foundational — later tasks depend on this field existing.

**Files:**
- Modify: `omotion/MotionProcessing.py:197-211` (Sample dataclass)
- Create: `tests/test_dark_frame_callback.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dark_frame_callback.py` with:

```python
"""
Dark-frame callback verification (no hardware).

Drives a real SciencePipeline via create_science_pipeline +
feed_pipeline_from_csv using the single_cam_basic fixture
(DISCARD_COUNT=2, DARK_INTERVAL=5 → darks at absolute frames {3, 6, 11}).

Run with pytest:
    pytest tests/test_dark_frame_callback.py -v
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omotion.MotionProcessing import (
    CorrectedBatch,
    Sample,
    create_science_pipeline,
    feed_pipeline_from_csv,
)

# Keep these in sync with tests/test_pipeline_csv.py / generate_fixtures.py.
DISCARD_COUNT = 2
DARK_INTERVAL = 5
DARK_FRAMES_5 = {3, 6, 11}

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

_ZERO = np.zeros((2, 8), dtype=np.float64)
_ONE = np.ones((2, 8), dtype=np.float64)
BFI_C_MIN = _ZERO.copy()
BFI_C_MAX = _ONE.copy()
BFI_I_MIN = _ZERO.copy()
BFI_I_MAX = np.full((2, 8), 1000.0)


def _fixture(name: str) -> str:
    return os.path.join(FIXTURES_DIR, name)


class TestSampleIsDarkField:
    """Foundational: Sample has an is_dark field defaulting to False."""

    def test_sample_is_dark_defaults_to_false(self):
        s = Sample(
            side="left", cam_id=0,
            frame_id=10, absolute_frame_id=10, timestamp_s=0.1,
            row_sum=1000, temperature_c=25.0,
            mean=100.0, std_dev=10.0, contrast=0.1,
            bfi=1.0, bvi=1.0,
        )
        assert s.is_dark is False

    def test_sample_is_dark_can_be_set_true(self):
        s = Sample(
            side="left", cam_id=0,
            frame_id=10, absolute_frame_id=10, timestamp_s=0.1,
            row_sum=1000, temperature_c=25.0,
            mean=100.0, std_dev=10.0, contrast=0.1,
            bfi=1.0, bvi=1.0,
            is_dark=True,
        )
        assert s.is_dark is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dark_frame_callback.py::TestSampleIsDarkField -v`

Expected: both tests FAIL with `TypeError: Sample.__init__() got an unexpected keyword argument 'is_dark'` (second test) and `AttributeError` on the first.

- [ ] **Step 3: Add the field**

Edit `omotion/MotionProcessing.py:197-211`. Add `is_dark: bool = False` after `is_corrected`:

```python
@dataclass
class Sample:
    side: str
    cam_id: int
    frame_id: int           # raw u8 from the wire
    absolute_frame_id: int  # monotonic counter with rollover handled
    timestamp_s: float
    row_sum: int
    temperature_c: float
    mean: float
    std_dev: float
    contrast: float
    bfi: float
    bvi: float
    is_corrected: bool = False  # True when dark-frame interpolation has been applied
    is_dark: bool = False       # True when this sample represents a laser-off (dark) frame
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dark_frame_callback.py::TestSampleIsDarkField -v`

Expected: both tests PASS.

Also run the full existing test suite to make sure no existing test regresses:

`pytest tests/test_pipeline_csv.py tests/test_reduced_mode.py tests/test_corrected_csv_output.py tests/test_sequences.py -v`

Expected: all existing tests still PASS (new optional field defaults to False, backward-compatible).

- [ ] **Step 5: Commit**

```bash
git add omotion/MotionProcessing.py tests/test_dark_frame_callback.py
git commit -m "feat: add is_dark field to Sample dataclass

Foundational field for the upcoming on_dark_frame callback. Default
False preserves behaviour for all existing Sample emission sites.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `on_dark_frame_fn` callback — `SciencePipeline` + factory

Add the callback to `SciencePipeline.__init__`, fire it inside the dark branch, and expose it through the `create_science_pipeline` factory.

**Files:**
- Modify: `omotion/MotionProcessing.py:1004-1029` (SciencePipeline.__init__)
- Modify: `omotion/MotionProcessing.py:1160-1172` (dark branch firing site)
- Modify: `omotion/MotionProcessing.py:1498-1548` (create_science_pipeline factory)
- Test: `tests/test_dark_frame_callback.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dark_frame_callback.py`:

```python
def _make_pipeline_with_dark(
    left_mask: int = 0x01,
    right_mask: int = 0x00,
    dark_interval: int = DARK_INTERVAL,
):
    """Build a pipeline that collects uncorrected, corrected batches, and dark samples."""
    uncorrected: list[Sample] = []
    batches: list[CorrectedBatch] = []
    dark_samples: list[Sample] = []

    pipeline = create_science_pipeline(
        left_camera_mask=left_mask,
        right_camera_mask=right_mask,
        bfi_c_min=BFI_C_MIN,
        bfi_c_max=BFI_C_MAX,
        bfi_i_min=BFI_I_MIN,
        bfi_i_max=BFI_I_MAX,
        on_uncorrected_fn=uncorrected.append,
        on_corrected_batch_fn=batches.append,
        on_dark_frame_fn=dark_samples.append,
        dark_interval=dark_interval,
        discard_count=DISCARD_COUNT,
        expected_row_sum=None,
    )
    return pipeline, uncorrected, batches, dark_samples


class TestOnDarkFrameCallback:
    """on_dark_frame_fn fires once per scheduled dark frame with a Sample
    whose is_dark=True and whose mean/std_dev come from the raw dark
    histogram statistics."""

    def setup_method(self):
        self.pipeline, self.uncorrected, self.batches, self.darks = \
            _make_pipeline_with_dark(left_mask=0x01)
        feed_pipeline_from_csv(
            _fixture("single_cam_basic_left.csv"), "left", self.pipeline
        )
        self.pipeline.stop(timeout=5.0)

    def test_dark_callback_fires_at_every_dark_position(self):
        abs_ids = sorted(s.absolute_frame_id for s in self.darks)
        # DISCARD_COUNT=2, DARK_INTERVAL=5 → darks at 3, 6, 11
        assert abs_ids == [3, 6, 11], (
            f"Expected dark callback at frames [3, 6, 11], got {abs_ids}"
        )

    def test_dark_callback_does_not_fire_on_light_frames(self):
        for s in self.darks:
            assert s.absolute_frame_id in DARK_FRAMES_5, (
                f"Frame {s.absolute_frame_id} is not a scheduled dark position"
            )

    def test_dark_samples_have_is_dark_true(self):
        for s in self.darks:
            assert s.is_dark is True, (
                f"Dark sample at frame {s.absolute_frame_id} must have is_dark=True"
            )

    def test_dark_samples_have_is_corrected_false(self):
        for s in self.darks:
            assert s.is_corrected is False

    def test_dark_sample_mean_matches_raw_u1(self):
        # Fixture dark bins are [10, 20) → raw u1 ≈ 14.5.
        # Dark samples are NOT pedestal-subtracted — the callback reports
        # the raw histogram mean so consumers see the true dark baseline.
        for s in self.darks:
            assert 10.0 < s.mean < 20.0, (
                f"Dark frame {s.absolute_frame_id} mean {s.mean:.2f} "
                f"not in expected raw-u1 range (10, 20)"
            )

    def test_dark_sample_std_dev_nonnegative_and_finite(self):
        for s in self.darks:
            assert s.std_dev >= 0.0 and np.isfinite(s.std_dev)

    def test_dark_sample_bfi_bvi_are_zero(self):
        # BFI/BVI are not meaningful on dark frames — the callback leaves them 0.
        for s in self.darks:
            assert s.bfi == 0.0
            assert s.bvi == 0.0

    def test_dark_sample_contrast_is_std_over_mean(self):
        for s in self.darks:
            if s.mean > 0:
                expected_contrast = s.std_dev / s.mean
                assert abs(s.contrast - expected_contrast) < 1e-9, (
                    f"Dark frame {s.absolute_frame_id} contrast {s.contrast:.6f} "
                    f"does not match std/mean = {expected_contrast:.6f}"
                )

    def test_missing_dark_callback_is_a_noop(self):
        """Pipeline must still run correctly when on_dark_frame_fn is None."""
        uncorrected: list[Sample] = []
        batches: list[CorrectedBatch] = []
        pipeline = create_science_pipeline(
            left_camera_mask=0x01,
            right_camera_mask=0x00,
            bfi_c_min=BFI_C_MIN, bfi_c_max=BFI_C_MAX,
            bfi_i_min=BFI_I_MIN, bfi_i_max=BFI_I_MAX,
            on_uncorrected_fn=uncorrected.append,
            on_corrected_batch_fn=batches.append,
            on_dark_frame_fn=None,          # explicit None
            dark_interval=DARK_INTERVAL,
            discard_count=DISCARD_COUNT,
            expected_row_sum=None,
        )
        feed_pipeline_from_csv(
            _fixture("single_cam_basic_left.csv"), "left", pipeline
        )
        pipeline.stop(timeout=5.0)
        # At least one corrected batch proves the pipeline processed frames.
        assert len(batches) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dark_frame_callback.py::TestOnDarkFrameCallback -v`

Expected: every test FAILs at `_make_pipeline_with_dark` with `TypeError: create_science_pipeline() got an unexpected keyword argument 'on_dark_frame_fn'`.

- [ ] **Step 3: Implement `on_dark_frame_fn` in `SciencePipeline`**

Edit `omotion/MotionProcessing.py:1004-1019` — add `on_dark_frame_fn` to `SciencePipeline.__init__` signature:

```python
def __init__(
    self,
    *,
    left_camera_mask: int = 0xFF,
    right_camera_mask: int = 0xFF,
    bfi_c_min,
    bfi_c_max,
    bfi_i_min,
    bfi_i_max,
    on_uncorrected_fn: Callable[[Sample], None] | None = None,
    on_corrected_batch_fn: Callable[[CorrectedBatch], None] | None = None,
    on_dark_frame_fn: Callable[[Sample], None] | None = None,
    dark_interval: int = 600,
    discard_count: int = 9,
    expected_row_sum: int | None = None,
    noise_floor: int = 10,
):
```

In the `__init__` body (`omotion/MotionProcessing.py:1024-1025`), store the callback alongside the existing ones:

```python
    self._on_uncorrected_fn = on_uncorrected_fn
    self._on_corrected_batch_fn = on_corrected_batch_fn
    self._on_dark_frame_fn = on_dark_frame_fn
```

Edit the dark-branch in `_science_worker` at `omotion/MotionProcessing.py:1162-1172`. Immediately after `variance` is computed and before the `_dark_history.setdefault` line, build and fire a dark `Sample`. Use `np.sqrt` (numpy is already imported at the top of the file) rather than adding a `math` import:

```python
            if self._is_dark_frame(absolute_frame):
                variance = max(0.0, u2 - u1 * u1)

                # Fire on_dark_frame_fn with raw dark-baseline statistics
                # so consumers (e.g. contact-quality logic) can observe
                # per-camera dark levels in real time.  BFI/BVI are 0 —
                # they're not meaningful on a dark frame.  mean is the
                # raw u1 (NOT pedestal-subtracted); std_dev = sqrt(var);
                # contrast = std_dev / mean.
                if self._on_dark_frame_fn is not None:
                    dark_std = float(np.sqrt(variance))
                    dark_contrast = (dark_std / u1) if u1 > 0 else 0.0
                    dark_sample = Sample(
                        side=side,
                        cam_id=cam_id,
                        frame_id=raw_frame_id,
                        absolute_frame_id=absolute_frame,
                        timestamp_s=ts,
                        row_sum=row_sum,
                        temperature_c=temp,
                        mean=u1,
                        std_dev=dark_std,
                        contrast=dark_contrast,
                        bfi=0.0,
                        bvi=0.0,
                        is_corrected=False,
                        is_dark=True,
                    )
                    try:
                        self._on_dark_frame_fn(dark_sample)
                    except Exception:
                        logger.exception("Error in on_dark_frame_fn callback")

                dark_list = self._dark_history.setdefault(key, [])
                dark_list.append((absolute_frame, raw_frame_id, ts, u1, variance))
                logger.debug(
                    "Dark frame %d for %s cam %d (dark #%d): "
                    "u1=%.2f var=%.4f",
                    absolute_frame, side, cam_id, len(dark_list),
                    u1, variance,
                )
```


- [ ] **Step 4: Plumb `on_dark_frame_fn` through `create_science_pipeline`**

Edit the factory at `omotion/MotionProcessing.py:1498-1548`. Add the parameter and pass it through:

```python
def create_science_pipeline(
    *,
    left_camera_mask: int = 0xFF,
    right_camera_mask: int = 0xFF,
    bfi_c_min,
    bfi_c_max,
    bfi_i_min,
    bfi_i_max,
    on_uncorrected_fn: Callable[[Sample], None] | None = None,
    on_corrected_batch_fn: Callable[[CorrectedBatch], None] | None = None,
    on_dark_frame_fn: Callable[[Sample], None] | None = None,
    dark_interval: int = 600,
    discard_count: int = 9,
    expected_row_sum: int | None = None,
    noise_floor: int = 10,
) -> SciencePipeline:
    ...
    pipeline = SciencePipeline(
        left_camera_mask=left_camera_mask,
        right_camera_mask=right_camera_mask,
        bfi_c_min=bfi_c_min,
        bfi_c_max=bfi_c_max,
        bfi_i_min=bfi_i_min,
        bfi_i_max=bfi_i_max,
        on_uncorrected_fn=on_uncorrected_fn,
        on_corrected_batch_fn=on_corrected_batch_fn,
        on_dark_frame_fn=on_dark_frame_fn,
        dark_interval=dark_interval,
        discard_count=discard_count,
        ...
    )
```

Also update the factory's docstring (around `omotion/MotionProcessing.py:1518`) to document the new parameter:

```python
    on_dark_frame_fn
        Fires once per scheduled dark frame with a ``Sample`` whose
        ``is_dark=True``.  ``mean`` is the raw histogram mean (u1, not
        pedestal-subtracted); ``std_dev = sqrt(variance)``;
        ``contrast = std_dev / mean``; ``bfi`` and ``bvi`` are 0 (not
        meaningful on a dark frame).  Registration-gated — pass None to
        disable (default).
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_dark_frame_callback.py -v`

Expected: all tests (Task 1 + Task 2) PASS.

Also run the broader suite to catch regressions in the dark-branch logic:

`pytest tests/test_pipeline_csv.py tests/test_reduced_mode.py tests/test_corrected_csv_output.py tests/test_sequences.py -v`

Expected: all existing tests still PASS.

- [ ] **Step 6: Commit**

```bash
git add omotion/MotionProcessing.py tests/test_dark_frame_callback.py
git commit -m "feat: add on_dark_frame_fn callback to SciencePipeline

Fires once per scheduled dark frame with a Sample carrying the raw u1,
sqrt(variance), and std/mean contrast for that camera's dark baseline.
Registration-gated; no ScanRequest toggle (pure observability hook).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Mark the dark-repeat uncorrected sample with `is_dark=True`

The existing `on_uncorrected_fn` branch at `MotionProcessing.py:1182-1201` emits a "repeat previous uncorrected value" `Sample` at dark positions so the live plot sees no gap. That sample represents a dark-frame slot — it should now carry `is_dark=True` so consumers can filter.

**Files:**
- Modify: `omotion/MotionProcessing.py:1180-1202` (dark-repeat Sample construction)
- Test: `tests/test_dark_frame_callback.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dark_frame_callback.py`:

```python
class TestDarkRepeatUncorrectedIsMarked:
    """The on_uncorrected_fn also fires at dark-frame slots with a
    repeat-previous-value Sample so live plots see no gap.  That sample
    should now be tagged is_dark=True."""

    def setup_method(self):
        self.pipeline, self.uncorrected, self.batches, self.darks = \
            _make_pipeline_with_dark(left_mask=0x01)
        feed_pipeline_from_csv(
            _fixture("single_cam_basic_left.csv"), "left", self.pipeline
        )
        self.pipeline.stop(timeout=5.0)

    def test_light_uncorrected_samples_are_not_marked_dark(self):
        # Frame 3 is the first dark — no prev exists yet, so no repeat sample.
        # Frames 6 and 11 ARE dark-repeat slots.  All other emitted
        # uncorrected samples (4, 5, 7, 8, 9, 10, 12) are genuine light.
        dark_repeat_slots = {6, 11}
        for s in self.uncorrected:
            if s.absolute_frame_id in dark_repeat_slots:
                assert s.is_dark is True, (
                    f"Dark-repeat uncorrected sample at frame "
                    f"{s.absolute_frame_id} must have is_dark=True"
                )
            else:
                assert s.is_dark is False, (
                    f"Light uncorrected sample at frame "
                    f"{s.absolute_frame_id} must have is_dark=False"
                )

    def test_dark_repeat_sample_actually_emitted(self):
        # Confirm the fixture produces the expected dark-repeat emissions.
        abs_ids = {s.absolute_frame_id for s in self.uncorrected
                   if s.is_dark}
        assert abs_ids == {6, 11}, (
            f"Expected dark-repeat uncorrected samples at {{6, 11}}, got {abs_ids}"
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dark_frame_callback.py::TestDarkRepeatUncorrectedIsMarked -v`

Expected: tests FAIL because every emitted `Sample.is_dark` is currently `False` (the default) regardless of whether the frame is a dark slot.

- [ ] **Step 3: Set `is_dark=True` on the dark-repeat Sample**

Edit `omotion/MotionProcessing.py:1180-1201`. The existing dark-repeat Sample construction inside `if prev is not None:` currently passes `is_corrected=False`. Add `is_dark=True`:

```python
                # Rule 1: emit an uncorrected sample for the dark frame that
                # repeats the last known good (non-dark) values so the live
                # plot sees no blip at the dark-frame position.  The sample
                # carries is_dark=True so consumers can filter it out.
                prev = self._last_uncorrected.get(key)
                if prev is not None:
                    dark_uncorrected = Sample(
                        side=prev.side,
                        cam_id=prev.cam_id,
                        frame_id=raw_frame_id,
                        absolute_frame_id=absolute_frame,
                        timestamp_s=ts,
                        row_sum=prev.row_sum,
                        temperature_c=prev.temperature_c,
                        mean=prev.mean,
                        std_dev=prev.std_dev,
                        contrast=prev.contrast,
                        bfi=prev.bfi,
                        bvi=prev.bvi,
                        is_corrected=False,
                        is_dark=True,
                    )
                    if self._on_uncorrected_fn:
                        try:
                            self._on_uncorrected_fn(dark_uncorrected)
                        except Exception:
                            pass
                continue
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dark_frame_callback.py -v`

Expected: all tests PASS.

Also check no regressions in `tests/test_pipeline_csv.py` — its existing assertions on uncorrected samples are range-based, not `is_dark`-dependent:

`pytest tests/test_pipeline_csv.py -v`

Expected: all existing tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add omotion/MotionProcessing.py tests/test_dark_frame_callback.py
git commit -m "feat: tag dark-repeat uncorrected samples with is_dark=True

Consumers of on_uncorrected_fn can now filter out the repeat-previous
samples emitted at dark-frame slots (used to hide gaps in live plots).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Plumb `on_dark_frame_fn` through `ScanWorkflow.start_scan`

Mechanical: expose the callback as a `start_scan` kwarg (matching the existing 8 callback kwargs) and forward it into `create_science_pipeline` inside `_worker`.

**Files:**
- Modify: `omotion/ScanWorkflow.py:178-191` (start_scan signature)
- Modify: `omotion/ScanWorkflow.py:601-610` (science pipeline creation inside _worker)

No test — existing tests don't unit-test `ScanWorkflow.start_scan` (it requires a full `MOTIONInterface` with hardware). The Task 2 + 3 tests cover the behaviour at the layer where it's testable; this step is pure plumbing.

- [ ] **Step 1: Add `on_dark_frame_fn` kwarg to `start_scan`**

Edit `omotion/ScanWorkflow.py:178-191`:

```python
    def start_scan(
        self,
        request: ScanRequest,
        *,
        extra_cols_fn: Callable[[], list] | None = None,
        on_log_fn: Callable[[str], None] | None = None,
        on_progress_fn: Callable[[int], None] | None = None,
        on_trigger_state_fn: Callable[[str], None] | None = None,
        on_uncorrected_fn: Callable[[object], None] | None = None,
        on_corrected_batch_fn: Callable[[object], None] | None = None,
        on_dark_frame_fn: Callable[[object], None] | None = None,
        on_error_fn: Callable[[Exception], None] | None = None,
        on_side_stream_fn: Callable[[str, str], None] | None = None,
        on_complete_fn: Callable[[ScanResult], None] | None = None,
    ) -> bool:
```

- [ ] **Step 2: Forward into `create_science_pipeline`**

Edit `omotion/ScanWorkflow.py:601-610`. Add `on_dark_frame_fn=on_dark_frame_fn` to the factory call:

```python
                    science_pipeline = create_science_pipeline(
                        left_camera_mask=left_mask_active,
                        right_camera_mask=right_mask_active,
                        bfi_c_min=self._bfi_c_min,
                        bfi_c_max=self._bfi_c_max,
                        bfi_i_min=self._bfi_i_min,
                        bfi_i_max=self._bfi_i_max,
                        on_uncorrected_fn=_on_uncorrected_sample,
                        on_corrected_batch_fn=_on_corrected_batch,
                        on_dark_frame_fn=on_dark_frame_fn,
                    )
```

- [ ] **Step 3: Run existing tests to confirm no regression**

Run: `pytest tests/ -v --ignore=tests/hardware`

Expected: all non-hardware tests PASS (this is a strictly-additive signature change; existing callers pass no `on_dark_frame_fn` and get the `None` default).

- [ ] **Step 4: Commit**

```bash
git add omotion/ScanWorkflow.py
git commit -m "feat: plumb on_dark_frame_fn through ScanWorkflow.start_scan

Forwards the new callback into create_science_pipeline. Matches the
existing pattern used by on_uncorrected_fn / on_corrected_batch_fn.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Rolling-average stage — `SciencePipeline` + factory

Add the rolling-average state and emission to `SciencePipeline`, expose it through the factory. This is the biggest task — covers the core algorithm, dark filtering (via placement in the light branch), partial-window emission, and disabled-by-default gating.

**Files:**
- Modify: `omotion/MotionProcessing.py` — add `collections.deque` import if missing
- Modify: `omotion/MotionProcessing.py:1004-1029` (SciencePipeline.__init__ — new params + state init)
- Modify: `omotion/MotionProcessing.py:1235-1241` (emission site after on_uncorrected_fn)
- Modify: `omotion/MotionProcessing.py:1498-1548` (factory)
- Create: `tests/test_rolling_average.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rolling_average.py`:

```python
"""
Rolling-average verification (no hardware).

Drives a real SciencePipeline via create_science_pipeline +
feed_pipeline_from_csv using the single_cam_basic fixture
(DISCARD_COUNT=2, DARK_INTERVAL=5 → light frames at
{4, 5, 7, 8, 9, 10, 12}, darks at {3, 6, 11}).

Run with pytest:
    pytest tests/test_rolling_average.py -v
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omotion.MotionProcessing import (
    CorrectedBatch,
    Sample,
    create_science_pipeline,
    feed_pipeline_from_csv,
)

DISCARD_COUNT = 2
DARK_INTERVAL = 5
LIGHT_FRAMES = {4, 5, 7, 8, 9, 10, 12}
DARK_FRAMES = {3, 6, 11}

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

_ZERO = np.zeros((2, 8), dtype=np.float64)
_ONE = np.ones((2, 8), dtype=np.float64)
BFI_C_MIN = _ZERO.copy()
BFI_C_MAX = _ONE.copy()
BFI_I_MIN = _ZERO.copy()
BFI_I_MAX = np.full((2, 8), 1000.0)


def _fixture(name: str) -> str:
    return os.path.join(FIXTURES_DIR, name)


def _make_pipeline(
    *,
    rolling_avg_enabled: bool,
    rolling_avg_window: int = 3,
    left_mask: int = 0x01,
    right_mask: int = 0x00,
):
    uncorrected: list[Sample] = []
    batches: list[CorrectedBatch] = []
    rolling: list[Sample] = []

    pipeline = create_science_pipeline(
        left_camera_mask=left_mask,
        right_camera_mask=right_mask,
        bfi_c_min=BFI_C_MIN,
        bfi_c_max=BFI_C_MAX,
        bfi_i_min=BFI_I_MIN,
        bfi_i_max=BFI_I_MAX,
        on_uncorrected_fn=uncorrected.append,
        on_corrected_batch_fn=batches.append,
        on_rolling_avg_fn=rolling.append,
        rolling_avg_enabled=rolling_avg_enabled,
        rolling_avg_window=rolling_avg_window,
        dark_interval=DARK_INTERVAL,
        discard_count=DISCARD_COUNT,
        expected_row_sum=None,
    )
    return pipeline, uncorrected, batches, rolling


class TestRollingAverageDisabled:
    """rolling_avg_enabled=False means the callback never fires."""

    def test_disabled_never_fires_callback(self):
        pipeline, _, _, rolling = _make_pipeline(rolling_avg_enabled=False)
        feed_pipeline_from_csv(
            _fixture("single_cam_basic_left.csv"), "left", pipeline
        )
        pipeline.stop(timeout=5.0)
        assert rolling == [], (
            f"Expected no rolling-avg emissions when disabled, got {len(rolling)}"
        )


class TestRollingAverageEnabled:
    """rolling_avg_enabled=True with window=3 emits once per light frame,
    averaging only mean and contrast, zeroing the rest."""

    def setup_method(self):
        self.pipeline, self.uncorrected, self.batches, self.rolling = \
            _make_pipeline(rolling_avg_enabled=True, rolling_avg_window=3)
        feed_pipeline_from_csv(
            _fixture("single_cam_basic_left.csv"), "left", self.pipeline
        )
        self.pipeline.stop(timeout=5.0)

    def test_one_emission_per_light_frame(self):
        # Light frames: {4, 5, 7, 8, 9, 10, 12} → 7 emissions.
        assert len(self.rolling) == 7, (
            f"Expected 7 rolling-avg emissions, got {len(self.rolling)}"
        )

    def test_emissions_only_at_light_frame_positions(self):
        abs_ids = sorted(s.absolute_frame_id for s in self.rolling)
        assert abs_ids == sorted(LIGHT_FRAMES), (
            f"Rolling-avg emissions should occur at light frames "
            f"{sorted(LIGHT_FRAMES)}, got {abs_ids}"
        )

    def test_no_dark_frame_ids_in_emissions(self):
        for s in self.rolling:
            assert s.absolute_frame_id not in DARK_FRAMES, (
                f"Rolling-avg emission at dark frame {s.absolute_frame_id} "
                f"— dark frames must not enter the window"
            )

    def test_emitted_sample_not_corrected_not_dark(self):
        for s in self.rolling:
            assert s.is_corrected is False
            assert s.is_dark is False

    def test_zeroed_fields(self):
        for s in self.rolling:
            assert s.std_dev == 0.0
            assert s.bfi == 0.0
            assert s.bvi == 0.0
            assert s.row_sum == 0
            assert s.temperature_c == 0.0

    def test_rolling_mean_matches_reference(self):
        # Build reference: group uncorrected samples by absolute_frame_id,
        # filter out dark repeats, then apply a size-3 rolling mean manually.
        light_uncorrected = [
            s for s in self.uncorrected if s.is_dark is False
        ]
        light_uncorrected.sort(key=lambda s: s.absolute_frame_id)

        reference_means: dict[int, float] = {}
        window: list[Sample] = []
        for s in light_uncorrected:
            window.append(s)
            if len(window) > 3:
                window.pop(0)
            reference_means[s.absolute_frame_id] = sum(
                w.mean for w in window
            ) / len(window)

        for s in self.rolling:
            expected = reference_means[s.absolute_frame_id]
            assert abs(s.mean - expected) < 1e-6, (
                f"Frame {s.absolute_frame_id}: rolling mean {s.mean:.6f} "
                f"does not match reference {expected:.6f}"
            )

    def test_rolling_contrast_matches_reference(self):
        light_uncorrected = [
            s for s in self.uncorrected if s.is_dark is False
        ]
        light_uncorrected.sort(key=lambda s: s.absolute_frame_id)

        reference_contrasts: dict[int, float] = {}
        window: list[Sample] = []
        for s in light_uncorrected:
            window.append(s)
            if len(window) > 3:
                window.pop(0)
            reference_contrasts[s.absolute_frame_id] = sum(
                w.contrast for w in window
            ) / len(window)

        for s in self.rolling:
            expected = reference_contrasts[s.absolute_frame_id]
            assert abs(s.contrast - expected) < 1e-6, (
                f"Frame {s.absolute_frame_id}: rolling contrast {s.contrast:.6f} "
                f"does not match reference {expected:.6f}"
            )

    def test_partial_window_before_n_samples(self):
        # First light frame (4) has only 1 sample in the window; the emitted
        # mean must equal that sample's own uncorrected mean, not wait for
        # the window to fill.
        first = next(s for s in self.rolling if s.absolute_frame_id == 4)
        first_uncorr = next(
            s for s in self.uncorrected
            if s.absolute_frame_id == 4 and s.is_dark is False
        )
        assert abs(first.mean - first_uncorr.mean) < 1e-6, (
            f"Partial-window emission at frame 4 should equal uncorrected "
            f"mean {first_uncorr.mean:.6f}, got {first.mean:.6f}"
        )


class TestRollingAveragePerCameraIsolation:
    """Interleaved frames across cameras must not cross-contaminate windows."""

    def test_left_and_right_cameras_do_not_mix(self):
        # Enable one camera on each side; feed both fixtures.  The
        # single_cam_basic fixtures for left and right use the same bin
        # distributions, so a cross-contamination bug would be hard to
        # detect numerically — instead assert per-(side, cam_id) emission
        # counts.
        pipeline, _, _, rolling = _make_pipeline(
            rolling_avg_enabled=True,
            rolling_avg_window=3,
            left_mask=0x01,
            right_mask=0x01,
        )
        # Only run this if the right-side fixture exists
        right_fix = _fixture("single_cam_basic_right.csv")
        if not os.path.exists(right_fix):
            pytest.skip("right-side single-cam fixture not present")
        feed_pipeline_from_csv(
            _fixture("single_cam_basic_left.csv"), "left", pipeline
        )
        feed_pipeline_from_csv(right_fix, "right", pipeline)
        pipeline.stop(timeout=5.0)

        left_count = sum(1 for s in rolling if s.side == "left")
        right_count = sum(1 for s in rolling if s.side == "right")
        # Each side emits once per light frame → 7 each.
        assert left_count == 7
        assert right_count == 7
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_rolling_average.py -v`

Expected: all tests FAIL at `_make_pipeline` with `TypeError: create_science_pipeline() got an unexpected keyword argument 'on_rolling_avg_fn'`.

- [ ] **Step 3: Add rolling-average params + state to `SciencePipeline.__init__`**

Add `from collections import deque` to the stdlib imports at the top of `omotion/MotionProcessing.py` (it is not currently imported). Place it alongside the existing `from dataclasses import dataclass, field` line around line 5:

```python
from collections import deque
from dataclasses import dataclass, field
```

Edit `omotion/MotionProcessing.py:1004-1029` — extend the signature:

```python
def __init__(
    self,
    *,
    left_camera_mask: int = 0xFF,
    right_camera_mask: int = 0xFF,
    bfi_c_min,
    bfi_c_max,
    bfi_i_min,
    bfi_i_max,
    on_uncorrected_fn: Callable[[Sample], None] | None = None,
    on_corrected_batch_fn: Callable[[CorrectedBatch], None] | None = None,
    on_dark_frame_fn: Callable[[Sample], None] | None = None,
    on_rolling_avg_fn: Callable[[Sample], None] | None = None,
    rolling_avg_enabled: bool = False,
    rolling_avg_window: int = 10,
    dark_interval: int = 600,
    discard_count: int = 9,
    expected_row_sum: int | None = None,
    noise_floor: int = 10,
):
```

In the `__init__` body (alongside the other `self._on_*` assignments, circa `omotion/MotionProcessing.py:1024-1029`):

```python
    self._on_rolling_avg_fn = on_rolling_avg_fn
    self._rolling_avg_enabled = bool(rolling_avg_enabled)
    self._rolling_avg_window = int(rolling_avg_window)
    # Per (side, cam_id): deque of the last N uncorrected light Samples.
    # Only allocated when rolling_avg_enabled is True, so disabled mode
    # has zero per-frame overhead.
    self._rolling_buffers: dict[tuple[str, int], "deque[Sample]"] = {}
```

- [ ] **Step 4: Add the emission site in the light branch**

Edit `omotion/MotionProcessing.py:1235-1241`. Immediately after the `self._last_uncorrected[key] = uncorrected` line (the end of the light-branch processing), append the rolling-avg block:

```python
            if self._on_uncorrected_fn:
                try:
                    self._on_uncorrected_fn(uncorrected)
                except Exception:
                    pass

            self._last_uncorrected[key] = uncorrected

            # --- 7. Rolling-average over the last N uncorrected light samples ---
            # Placed in the light branch only, so dark-frame repeat samples
            # never enter the window (they continue via the dark branch
            # above and do not reach this code path).
            if self._rolling_avg_enabled and self._on_rolling_avg_fn is not None:
                buf = self._rolling_buffers.get(key)
                if buf is None:
                    buf = deque(maxlen=self._rolling_avg_window)
                    self._rolling_buffers[key] = buf
                buf.append(uncorrected)

                n = len(buf)
                mean_avg = sum(s.mean for s in buf) / n
                contrast_avg = sum(s.contrast for s in buf) / n

                rolling_sample = Sample(
                    side=uncorrected.side,
                    cam_id=uncorrected.cam_id,
                    frame_id=uncorrected.frame_id,
                    absolute_frame_id=uncorrected.absolute_frame_id,
                    timestamp_s=uncorrected.timestamp_s,
                    row_sum=0,
                    temperature_c=0.0,
                    mean=mean_avg,
                    std_dev=0.0,
                    contrast=contrast_avg,
                    bfi=0.0,
                    bvi=0.0,
                    is_corrected=False,
                    is_dark=False,
                )
                try:
                    self._on_rolling_avg_fn(rolling_sample)
                except Exception:
                    logger.exception("Error in on_rolling_avg_fn callback")
```

- [ ] **Step 5: Plumb through `create_science_pipeline`**

Edit `omotion/MotionProcessing.py:1498-1548`. Add the three parameters and pass them through to `SciencePipeline(...)`:

```python
def create_science_pipeline(
    *,
    left_camera_mask: int = 0xFF,
    right_camera_mask: int = 0xFF,
    bfi_c_min,
    bfi_c_max,
    bfi_i_min,
    bfi_i_max,
    on_uncorrected_fn: Callable[[Sample], None] | None = None,
    on_corrected_batch_fn: Callable[[CorrectedBatch], None] | None = None,
    on_dark_frame_fn: Callable[[Sample], None] | None = None,
    on_rolling_avg_fn: Callable[[Sample], None] | None = None,
    rolling_avg_enabled: bool = False,
    rolling_avg_window: int = 10,
    dark_interval: int = 600,
    discard_count: int = 9,
    expected_row_sum: int | None = None,
    noise_floor: int = 10,
) -> SciencePipeline:
    ...
    pipeline = SciencePipeline(
        left_camera_mask=left_camera_mask,
        right_camera_mask=right_camera_mask,
        bfi_c_min=bfi_c_min,
        bfi_c_max=bfi_c_max,
        bfi_i_min=bfi_i_min,
        bfi_i_max=bfi_i_max,
        on_uncorrected_fn=on_uncorrected_fn,
        on_corrected_batch_fn=on_corrected_batch_fn,
        on_dark_frame_fn=on_dark_frame_fn,
        on_rolling_avg_fn=on_rolling_avg_fn,
        rolling_avg_enabled=rolling_avg_enabled,
        rolling_avg_window=rolling_avg_window,
        dark_interval=dark_interval,
        discard_count=discard_count,
        ...
    )
```

Extend the factory docstring with a note for the new parameters:

```python
    on_rolling_avg_fn
        When rolling_avg_enabled is True, fires once per uncorrected light
        frame per camera with a Sample whose mean and contrast are the
        arithmetic means over the last rolling_avg_window light samples
        for that (side, cam_id).  Other numeric fields are zeroed.  Dark
        frames never enter the window.  Partial windows emit (no wait for
        N samples to accumulate).
    rolling_avg_enabled
        When True, activates the rolling-average stage.  Default False —
        no buffer is allocated and on_rolling_avg_fn is never invoked.
    rolling_avg_window
        Window size N (default 10).  Ignored when rolling_avg_enabled is False.
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_rolling_average.py -v`

Expected: all tests PASS (the `PerCameraIsolation` test may skip if `single_cam_basic_right.csv` doesn't exist — that's fine).

Run the full non-hardware suite to check for regressions:

`pytest tests/test_pipeline_csv.py tests/test_reduced_mode.py tests/test_corrected_csv_output.py tests/test_sequences.py tests/test_dark_frame_callback.py tests/test_rolling_average.py -v`

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add omotion/MotionProcessing.py tests/test_rolling_average.py
git commit -m "feat: add rolling-average stage to SciencePipeline

Maintains a per-(side, cam_id) deque(maxlen=N) of uncorrected light
Samples, emits a rolling-mean Sample each time a new light frame
arrives. Disabled by default; only mean and contrast are averaged
to save compute (other numeric fields zeroed). Dark frames never
enter the window (emission is on the light branch only).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: `ScanRequest` fields + `start_scan` plumbing

Mechanical: expose `rolling_avg_enabled` / `rolling_avg_window` on `ScanRequest`, expose `on_rolling_avg_fn` as a `start_scan` kwarg, wire all three through to `create_science_pipeline` inside `_worker`.

**Files:**
- Modify: `omotion/ScanWorkflow.py:68-91` (ScanRequest dataclass)
- Modify: `omotion/ScanWorkflow.py:178-191` (start_scan signature — already extended in Task 4, extending again here)
- Modify: `omotion/ScanWorkflow.py:601-610` (create_science_pipeline call inside _worker)

No test — same reason as Task 4 (workflow layer requires hardware to exercise end-to-end).

- [ ] **Step 1: Add fields to `ScanRequest`**

Edit `omotion/ScanWorkflow.py:68-91`. Append the two new fields to the dataclass (after `reduced_mode`):

```python
@dataclass
class ScanRequest:
    subject_id: str
    duration_sec: int
    left_camera_mask: int
    right_camera_mask: int
    data_dir: str
    disable_laser: bool
    expected_size: int = 32837
    # CSV output flags — all enabled by default.  Flip to False once the
    # corresponding downstream consumer no longer needs the file, so the
    # pipeline avoids unnecessary disk I/O.
    write_raw_csv: bool = True
    write_corrected_csv: bool = True
    write_telemetry_csv: bool = True
    # Maximum number of seconds for which raw histogram CSVs are written.
    # None (default) means write for the full scan duration.
    # Has no effect when write_raw_csv is False.
    raw_csv_duration_sec: float | None = None
    # When True, the pipeline averages all active cameras per side into
    # single left/right BFI/BVI values.  The corrected CSV contains only
    # bfi_left, bfi_right, bvi_left, bvi_right columns.  Uncorrected
    # samples emitted to the UI are also averaged per-side per-frame.
    reduced_mode: bool = False
    # When True, the pipeline emits a rolling-mean Sample (mean + contrast
    # only) via on_rolling_avg_fn once per uncorrected light frame per
    # camera.  Window size is rolling_avg_window.  Dark frames are
    # excluded from the window.
    rolling_avg_enabled: bool = False
    rolling_avg_window: int = 10
```

- [ ] **Step 2: Add `on_rolling_avg_fn` kwarg to `start_scan`**

Edit `omotion/ScanWorkflow.py:178-191` (extending the signature from Task 4):

```python
    def start_scan(
        self,
        request: ScanRequest,
        *,
        extra_cols_fn: Callable[[], list] | None = None,
        on_log_fn: Callable[[str], None] | None = None,
        on_progress_fn: Callable[[int], None] | None = None,
        on_trigger_state_fn: Callable[[str], None] | None = None,
        on_uncorrected_fn: Callable[[object], None] | None = None,
        on_corrected_batch_fn: Callable[[object], None] | None = None,
        on_dark_frame_fn: Callable[[object], None] | None = None,
        on_rolling_avg_fn: Callable[[object], None] | None = None,
        on_error_fn: Callable[[Exception], None] | None = None,
        on_side_stream_fn: Callable[[str, str], None] | None = None,
        on_complete_fn: Callable[[ScanResult], None] | None = None,
    ) -> bool:
```

- [ ] **Step 3: Forward into `create_science_pipeline`**

Edit `omotion/ScanWorkflow.py:601-610` (extending the factory call from Task 4):

```python
                    science_pipeline = create_science_pipeline(
                        left_camera_mask=left_mask_active,
                        right_camera_mask=right_mask_active,
                        bfi_c_min=self._bfi_c_min,
                        bfi_c_max=self._bfi_c_max,
                        bfi_i_min=self._bfi_i_min,
                        bfi_i_max=self._bfi_i_max,
                        on_uncorrected_fn=_on_uncorrected_sample,
                        on_corrected_batch_fn=_on_corrected_batch,
                        on_dark_frame_fn=on_dark_frame_fn,
                        on_rolling_avg_fn=on_rolling_avg_fn,
                        rolling_avg_enabled=request.rolling_avg_enabled,
                        rolling_avg_window=request.rolling_avg_window,
                    )
```

- [ ] **Step 4: Run all tests**

Run: `pytest tests/ -v --ignore=tests/hardware`

Expected: all non-hardware tests PASS. `ScanRequest` construction in existing code paths still works because both new fields have defaults.

- [ ] **Step 5: Commit**

```bash
git add omotion/ScanWorkflow.py
git commit -m "feat: expose rolling-average via ScanRequest + start_scan

Adds rolling_avg_enabled and rolling_avg_window to ScanRequest, and
on_rolling_avg_fn as a start_scan kwarg. Both flow through to
create_science_pipeline alongside on_dark_frame_fn.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Final verification

After all six tasks land, run the full non-hardware test suite one more time to confirm everything still works together:

```bash
pytest tests/ -v --ignore=tests/hardware
```

Expected: every test PASSes, including the two new test files (`test_dark_frame_callback.py` and `test_rolling_average.py`).

Then spot-check the branch state:

```bash
git log --oneline origin/next..HEAD
```

Expected: six commits on `feature/contact-quality-rehash` in the order: `feat: add is_dark field`, `feat: add on_dark_frame_fn callback`, `feat: tag dark-repeat uncorrected samples`, `feat: plumb on_dark_frame_fn through ScanWorkflow`, `feat: add rolling-average stage`, `feat: expose rolling-average via ScanRequest + start_scan`.

At that point the branch is ready to open a PR against `next`.
