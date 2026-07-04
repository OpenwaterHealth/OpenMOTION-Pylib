# Contact-Quality per-channel pulse-validity criterion — design

**Issue:** [openmotion-sdk#126](https://github.com/OpenwaterHealth/openmotion-sdk/issues/126)
**Branch:** `feature/126-cq-pulse-validity` off `feature/124-pulse-waveform-analysis` (base `da48de1`)
**Board:** Project #11 item `PVTI_lADOAif52c4BVgTuzgxue3k` (In progress)
**Date:** 2026-07-04

## Goal

Add a new **per-camera** criterion to the SDK's `ContactQualityWorkflow` output:
*is a valid cardiac pulse train present on this channel for **>75% of the CQ scan**?*
A channel with adequate signal levels but no detectable pulse is flagged `no_pulse`.

This is an **SDK-only** feature. The bloodflow-app wiring (config keys, longer CQ
capture, surfacing `no_pulse` in the CQ modal) is a **separate follow-up** ticket/PR
in `openmotion-bloodflow-app`.

## Background & constraints

- The CQ "quick check" today runs `duration_sec=1.0` and evaluates **DN-scale signal
  levels** only: `ambient_light` (dark-frame `subtracted_mean` too high) and
  `poor_contact` (light-frame rolling `mean_dc_rt` too low). See
  `omotion/ContactQualityWorkflow.py`.
- A pulse train cannot be seen in 1 s. The pulse criterion needs **~10–15 s** of data.
  The caller must therefore pass a longer `duration_sec` when pulse evaluation is on.
- The `#124` branch already provides `PulseWaveformAnalyzer`
  (`omotion/pulse/analyzer.py`), which band-limits a 1-D signal, detects cardiac beats,
  and computes an autocorrelation **periodicity** score plus a coarse `reliable` gate
  (≥3 beats, amp>0, periodicity≥0.45). We **reuse** it rather than re-implement.
- The CQ sink already subscribes to the `live` pipeline channel, on which per-`(side,cam)`
  `bfi_live` is present (set by `BfiBviStage` before `Tee("live")`).

## Metric — grounded in bench data

Validated against a 15 s, 16-channel BFI capture
(`scan_owC27EHALL_20251217_161126_bfi_results.csv`, ~85 bpm, known-good contact):

| metric | 16 good channels | flat ref | noise ref |
|---|---|---|---|
| coverage = Σ in-band RR / scan duration | 0.90 – 0.94 | 0.57 | 0.51 |
| periodicity (autocorrelation strength) | 0.80 – 0.88 | 0.17 | 0.10 |

**Criterion:** `pulse_valid = (coverage > pulse_min_coverage) AND (periodicity >= 0.45)`
with `pulse_min_coverage` default `0.75`.

- **coverage** enforces the "*>75% of the scan*" temporal requirement.
- **periodicity** (already the analyzer's noise discriminator) rejects flat/noisy
  channels that would otherwise fake coverage.
- **Per-beat morphology QC is deliberately NOT used.** The analyzer's `_beat_passes_qc`
  is right for the clinical pulse *view*, but on a contact gate it rejected individual
  beats on noisier/lower-amplitude BFI channels, punching gaps that failed **6/16** of
  the known-good channels at 0.75. Removing it (onset-based coverage) makes all 16 pass
  while flat/noise still fail on periodicity.

`coverage` is computed from **refractory-spaced beat onsets** (feet between detected
systolic peaks), summing only inter-onset intervals whose length falls in the plausible
RR band `[60/max_bpm, 60/min_bpm]`, divided by the scan light-capture duration.

## Design

### 1. `omotion/pulse/analyzer.py` — additive only

New dataclass in `omotion/pulse/types.py`:

```python
@dataclass
class PulseCoverage:
    coverage:    float   # Σ in-band RR intervals / total_duration_s, 0..1 (NaN if unmeasurable)
    periodicity: float   # autocorrelation strength 0..1
    hr_bpm:      float   # 60 / median in-band RR (NaN if <1 beat)
    beat_count:  int     # in-band beats counted
    valid:       bool    # coverage > min_coverage AND periodicity >= PERIODICITY_MIN
```

New method on `PulseWaveformAnalyzer`:

```python
def beat_coverage(self, total_duration_s: float, *,
                  min_coverage: float = 0.75) -> PulseCoverage
```

- Reuses existing internals: `_estimate_fs`, `_nan_interp`, `_band_limit`,
  `_autocorr_period`, `_segment_feet`. **Does not** call `_beat_passes_qc` or
  `_resample_beat`, and **does not** modify `snapshot()` — zero risk to the live pulse
  path and its tests, minimal merge-conflict surface against `#124`'s ongoing work.
- `total_duration_s` is supplied by the caller (the CQ scan light-capture span), not the
  analyzer's internal buffer span — so a channel that drops out mid-scan gets low
  coverage rather than flattering short-buffer coverage.
- Degenerate cases (<8 samples, <2 onsets, non-finite fs) → `PulseCoverage(coverage=0.0,
  periodicity=<measured or 0>, hr_bpm=NaN, beat_count=0, valid=False)`.
- Reuses the class constant `PERIODICITY_MIN` (0.45) for the periodicity gate.

### 2. `omotion/ContactQualityWorkflow.py` — `_ContactQualitySink`

- For **light** frames, in addition to the existing `mean_dc_rt` collection, buffer
  `(timestamp_s, bfi_live)` per `(side, cam)` when `bfi_live` is present and finite.
  Skip when `bfi_live` is `None` (no BfiBviStage / replay) — pulse simply isn't
  evaluated then.
- Track the **global** min/max light-frame `timestamp_s` seen on any channel. The span
  `(max - min)` is the coverage denominator, falling back to the `duration_sec` passed to
  `result()` when unavailable.
- At `result()`, when pulse evaluation is enabled: for each active channel with a buffer,
  construct a `PulseWaveformAnalyzer` (configured with `raw_window_s` ≥ scan span so
  `add_samples` does not trim early samples), `add_samples(t, bfi)`, and call
  `beat_coverage(total_duration_s=<global span>, min_coverage=pulse_min_coverage)`.
  (`beat_coverage` sums **all** in-band inter-onset intervals across the buffer, so
  `history_beats` — a `snapshot()`-only truncation — does not apply.)

### 3. `CamCQResult` — new fields + reason

```python
pulse_valid:       bool    # False when not evaluated
pulse_coverage:    float   # NaN when not evaluated
pulse_hr_bpm:      float   # NaN when not evaluated
pulse_periodicity: float   # NaN when not evaluated
```

`reason` vocabulary gains `"no_pulse"`. Precedence (unchanged checks first):

```
no_signal  >  ambient_light  >  poor_contact  >  no_pulse  >  ok
```

Pulse is evaluated **only** on channels that otherwise pass the signal-level checks
(you can't have a pulse without signal). When pulse evaluation is enabled and such a
channel's `pulse_valid` is False, `reason="no_pulse"` and `passed=False`.

### 4. `ContactQualityWorkflow.check()` — new params

```python
def check(self, *, duration_sec=1.0, rolling_window=10,
          dark_threshold_per_camera, light_threshold_per_camera,
          left_camera_mask, right_camera_mask,
          evaluate_pulse: bool = False,
          pulse_min_coverage: float = 0.75) -> ContactQualityResult
```

- **`evaluate_pulse=False` by default → fully backward-compatible.** Existing callers,
  the 1 s quick-check, and all current tests are unchanged; `pulse_*` fields default to
  `False`/`NaN` and `passed`/`reason` semantics are identical to today.
- When `True`, the caller is responsible for passing a long enough `duration_sec`
  (~15 s) and having a valid calibration loaded (so `bfi_live` is meaningful). An
  all-NaN `bfi_live` channel → pulse not evaluated (fields stay NaN/False, `reason`
  falls through to the signal-level verdict).

## Testing

New `PulseCoverage` / `beat_coverage` unit tests (`tests/test_pulse_analyzer.py` or a
new module):
- Synthetic sinusoidal pulse (~72 bpm, 15 s @ 40 Hz) → `valid=True`, coverage>0.9.
- Flat + small noise, and broadband noise → `valid=False` via periodicity.
- Pulse present only in the first half of the scan → coverage≈0.5 → `valid=False`.
- `<8` samples → `valid=False`, no exception.

New CQ-sink tests (`tests/test_contact_quality_workflow.py`):
- Pulsatile `bfi_live` over a simulated ~15 s scan → `pulse_valid=True`, `reason="ok"`.
- Signal OK but flat `bfi_live` → `reason="no_pulse"`, `passed=False` (with
  `evaluate_pulse=True`).
- `evaluate_pulse=False` → identical to current behavior (regression guard).
- Precedence: a `poor_contact` channel stays `poor_contact` even with `evaluate_pulse=True`.

Regression fixture:
- Vendor the bench capture under `tests/data/` as a **clean tidy CSV**
  (`camera,side,time_s,bfi` — normalized, stdlib-`csv`/numpy-loadable, no bespoke parser).
- A test loads it, replays each channel's BFI through `beat_coverage`, and asserts all 16
  channels are `valid` at `min_coverage=0.75`.

All new tests are pure-software (`@pytest.mark.unit`, no hardware), runnable via
`pytest -m "not console and not sensor and not destructive"`.

## Out of scope (YAGNI)

- Per-beat morphology QC in the coverage metric.
- Windowed-reliability metric; BVI or dual-signal fallback.
- Any change to the live pulse view / `PulseWaveformStage` / `snapshot()`.
- Bloodflow-app UI + config wiring (separate follow-up ticket/PR).

## Risks / notes

- **Calibration dependency:** `bfi_live` is only meaningful with a loaded calibration.
  Documented in the `check()` docstring; all-NaN channels degrade gracefully.
- **Concurrent `#124` work:** the SDK main working tree is shared with an active `#124`
  session. This feature lives in its own worktree off a fixed base commit and touches
  `analyzer.py`/`types.py` **additively** to keep the merge surface small.
- **`ContactQualityWorkflow` is small and focused** — the pulse buffering adds bounded
  per-channel state; no extraction needed.
