# Contact Quality Rehash — Design

**Date:** 2026-04-20
**Branch:** `feature/contact-quality-rehash` (openmotion-sdk)
**Scope:** SDK only (`openmotion-sdk`). App integration lives elsewhere.

## Background

An earlier `feature/contact-quality` branch shipped a specialized `ContactQualityMonitor` class that encoded warning logic (ambient-light, poor-contact) inside the SDK. That implementation grew too complex and did not work correctly. The rehash replaces it with two general-purpose primitives in the data pipeline, leaving contact-quality logic to the app layer:

1. A per-dark-frame callback exposing raw dark baseline stats.
2. A toggleable rolling-average computation over the last N uncorrected light samples, with its own callback.

These primitives are domain-agnostic — the app (or any other SDK consumer) composes them into contact-quality logic.

## Architecture

Both primitives live in `SciencePipeline` (`omotion/MotionProcessing.py`) and are plumbed through `ScanWorkflow.start_scan` following the existing pattern used by `on_uncorrected_fn` / `on_corrected_batch_fn`.

### 1. `Sample.is_dark` field

Add a new field to the existing `Sample` dataclass:

```python
is_dark: bool = False
```

Default `False` preserves behavior for all existing emission sites. The field is used to distinguish:

- Light uncorrected samples → `is_dark=False` (unchanged).
- Dark-frame samples emitted via the new `on_dark_frame_fn` → `is_dark=True`.
- The "repeat previous uncorrected value" sample emitted on dark frames via the existing `on_uncorrected_fn` path (MotionProcessing.py:1182) → also `is_dark=True`, so consumers of `on_uncorrected_fn` can now filter darks if they want to.
- Rolling-average samples → `is_dark=False`.
- Corrected-batch samples → unchanged (`is_dark=False`; the dataclass default).

### 2. `on_dark_frame_fn` callback

New optional callback on `SciencePipeline` and `ScanWorkflow.start_scan`:

```python
on_dark_frame_fn: Callable[[Sample], None] | None = None
```

**Fire point:** `MotionProcessing._science_worker`, inside `if self._is_dark_frame(absolute_frame):` at MotionProcessing.py:1162, right after the dark `variance` is computed and before the entry is appended to `_dark_history`.

**Payload:** a `Sample` populated with:

| Field | Value |
|---|---|
| `side`, `cam_id` | from the current frame |
| `frame_id`, `absolute_frame_id`, `timestamp_s` | from the current frame |
| `row_sum`, `temperature_c` | from the current frame |
| `mean` | `u1` |
| `std_dev` | `sqrt(max(0, variance))` |
| `contrast` | `std_dev / mean` if `mean > 0` else `0.0` |
| `bfi`, `bvi` | `0.0` (not meaningful on a dark frame) |
| `is_corrected` | `False` |
| `is_dark` | `True` |

**Gating:** always fires when a dark frame is detected and the callback is registered (`is not None`). No `ScanRequest` toggle — it is a pure observability hook with no side effects.

### 3. Rolling-average over last N light samples

A new stage in `SciencePipeline` that maintains a per-`(side, cam_id)` deque of the last N uncorrected light `Sample`s, and emits a rolling-average `Sample` each time a new light frame arrives.

**Input.** The uncorrected `Sample` computed at MotionProcessing.py:1220 — i.e. only when the current frame is a light frame. The dark-frame "repeat previous uncorrected" branch at line 1182 does NOT feed this stage.

**Buffer.** `collections.deque(maxlen=N)` per `(side, cam_id)` key. Created lazily on first light sample for that key.

**Emission cadence.** On every new light sample: append to the deque, then compute the rolling average over the current deque contents and emit. Partial windows (count < N) are emitted — the consumer can filter by `absolute_frame_id` or its own counter if it wants full-window only.

**Fields averaged.** Only `mean` and `contrast`. All other numeric fields are zeroed in the emitted rolling-avg `Sample` to save compute:

| Field | Value in emitted rolling-avg Sample |
|---|---|
| `side`, `cam_id` | carried from the newest sample (same for every sample in the window) |
| `frame_id`, `absolute_frame_id`, `timestamp_s` | from the newest sample |
| `mean` | arithmetic mean of `mean` across buffered samples |
| `contrast` | arithmetic mean of `contrast` across buffered samples |
| `std_dev`, `bfi`, `bvi`, `row_sum`, `temperature_c` | `0` / `0.0` |
| `is_corrected` | `False` |
| `is_dark` | `False` |

**Output callback:**

```python
on_rolling_avg_fn: Callable[[Sample], None] | None = None
```

**Gating.** The rolling-average stage is only instantiated when `ScanRequest.rolling_avg_enabled` is `True`. When disabled, no buffer is allocated and the callback is never invoked — zero overhead for existing consumers.

### 4. `ScanRequest` additions

Two new fields on the `ScanRequest` dataclass in `omotion/ScanWorkflow.py`:

```python
rolling_avg_enabled: bool = False   # toggle the rolling-average stage
rolling_avg_window: int = 10        # N; matches the old poor-contact window size
```

### 5. Plumbing through `ScanWorkflow.start_scan`

Two new optional parameters on `start_scan`, mirroring the existing callback signature style:

```python
on_dark_frame_fn: Callable[[Sample], None] | None = None,
on_rolling_avg_fn: Callable[[Sample], None] | None = None,
```

Both are forwarded into `create_science_pipeline(...)` along with `rolling_avg_enabled` and `rolling_avg_window` read from the `ScanRequest`. No new aggregation closures in the workflow layer — unlike `reduced_mode`, which post-processes in `ScanWorkflow`, both new primitives emit directly from the pipeline.

### 6. `create_science_pipeline` factory

Extend the factory in `MotionProcessing.py` with the new parameters:

```python
def create_science_pipeline(
    *,
    # ...existing params...
    on_dark_frame_fn=None,
    on_rolling_avg_fn=None,
    rolling_avg_enabled=False,
    rolling_avg_window=10,
):
```

## Data Flow

```
Sensor USB stream
  → StreamInterface row handler
  → SciencePipeline.enqueue(...)
  → _science_worker loop
      ├── is dark frame?
      │     ├── yes → build Sample(is_dark=True, mean=u1, std_dev=sqrt(var), ...)
      │     │        └── fire on_dark_frame_fn
      │     │        └── update _dark_history, maybe emit corrected batch
      │     │        └── fire on_uncorrected_fn with repeat-prev Sample (is_dark=True)
      │     └── no  → compute uncorrected Sample (is_dark=False)
      │              ├── fire on_uncorrected_fn (unchanged)
      │              └── if rolling_avg_enabled:
      │                    append to deque[(side,cam_id)]
      │                    emit rolling-avg Sample (mean, contrast only)
      │                    fire on_rolling_avg_fn
      └── periodic: emit CorrectedBatch via on_corrected_batch_fn (unchanged)
```

## Testing

New unit tests under `tests/` following the pattern of `test_reduced_mode_averaging.py`. Hardware-free — drive `SciencePipeline.enqueue` with synthetic histograms.

**`tests/test_dark_frame_callback.py`:**

- `on_dark_frame_fn` fires exactly once per scheduled dark frame position (frame `discard_count + 1`, then every `dark_interval`).
- Emitted `Sample` has `is_dark=True`, `mean` matches `u1` of the dark histogram, `std_dev` matches `sqrt(variance)`, `bfi`/`bvi` are `0.0`.
- Does not fire on light frames.
- Missing callback (`None`) is a no-op — pipeline completes normally.
- Existing repeat-prev dark sample emitted via `on_uncorrected_fn` now has `is_dark=True`.

**`tests/test_rolling_average.py`:**

- With `rolling_avg_enabled=False`, `on_rolling_avg_fn` is never called even when registered.
- With `rolling_avg_enabled=True` and `rolling_avg_window=N`, callback fires once per light frame per camera.
- Emitted `mean` and `contrast` match a reference arithmetic mean over the trailing ≤ N light samples.
- Emitted `std_dev`, `bfi`, `bvi`, `row_sum`, `temperature_c` are all `0`.
- Dark frames do not enter the window — verified by feeding a mixed dark/light sequence and checking the window contents via the emitted averages.
- Per-`(side, cam_id)` independence — interleaved frames across cameras do not cross-contaminate windows.
- Partial-window emission (before the deque has filled to N) produces a valid average over what is buffered.

## Files Touched

- `omotion/MotionProcessing.py`
  - Add `is_dark: bool = False` to `Sample`.
  - Add `on_dark_frame_fn`, `on_rolling_avg_fn`, `rolling_avg_enabled`, `rolling_avg_window` params to `SciencePipeline.__init__`.
  - Initialize rolling-avg deque dict (only if enabled).
  - Fire `on_dark_frame_fn` inside the dark branch of `_science_worker`.
  - Set `is_dark=True` on the dark-repeat `Sample` at ~line 1182.
  - Append + emit rolling-average `Sample` after `on_uncorrected_fn` invocation at ~line 1237.
  - Extend `create_science_pipeline` factory with the new params.
- `omotion/ScanWorkflow.py`
  - Add `rolling_avg_enabled` and `rolling_avg_window` to `ScanRequest`.
  - Add `on_dark_frame_fn` and `on_rolling_avg_fn` params to `start_scan`.
  - Forward all four through to `create_science_pipeline`.
- `omotion/__init__.py`
  - No new exports required (existing `Sample` type is reused).
- `tests/test_dark_frame_callback.py` — new.
- `tests/test_rolling_average.py` — new.

## Out of Scope

- QML / bloodflow-app / test-app changes — this branch is SDK-only.
- Re-adding any `ContactQuality*` class in the SDK. App composes primitives.
- Persisting rolling-average values to the corrected CSV.
- Per-camera or per-side threshold logic (that's app domain).
- New corrected-pipeline rolling average (corrected batches are too coarse; the app can buffer `CorrectedBatch.samples` itself if needed).
