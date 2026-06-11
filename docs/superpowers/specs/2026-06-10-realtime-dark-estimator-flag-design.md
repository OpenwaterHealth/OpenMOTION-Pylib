# Realtime dark-estimator flag — design

**Date:** 2026-06-10
**Status:** approved (off-behavior = zero-order hold; toggle = factory kwarg only)

## Problem

The live BFI/BVI trace shows a sinusoidal artifact with a ~15 s period —
exactly the dark-frame cadence (`dark_interval=600` frames ÷ 40 fps). The
suspected cause is `HybridRealtimePredictor` (avg-of-3 u1 + linear
extrapolation of std through the last 2 darks): the std slope is fit through
two noisy dark observations 15 s apart, so each interval the predicted
baseline over/undershoots and resets at the next dark. There is currently no
way to disable the predictor to A/B test this hypothesis.

## Design

Add a `realtime_dark_estimator` kwarg to `default_pipeline()`:

- `"hybrid"` (default) — current behavior, `HybridRealtimePredictor`.
- `"zoh"` — new `ZeroOrderHoldPredictor`: returns the most recent dark
  observation's (u1, std) unchanged. Plain dark subtraction with no
  averaging or extrapolation; the live trace keeps working and stays
  scale-comparable with hybrid runs.
- Any other value — `ValueError`.

`DarkCorrectionStage` already takes the predictor via its
`realtime_estimator` constructor arg, so the flag is purely factory-level
selection. Both predictors share the duck-typed interface
`predict(side, cam_id, *, history, target_t) -> Optional[(u1, std)]` and
return `None` on empty history (warmup window → NaN live values, as today).

## Non-goals

- The batched/final correction path (`LinearInterpolation` between dark
  boundaries) is untouched — only the realtime channel changes.
- No `ScanRequest` field or env var; callers that build the pipeline
  directly (scripts, replay) pass the kwarg.

## Testing

- `tests/test_pipeline/test_dark_estimators.py`: ZOH returns the last dark
  verbatim (where hybrid would average/extrapolate), ignores `target_t`,
  returns `None` on empty history.
- `tests/test_pipeline/test_factory.py`: default selects hybrid; `"zoh"`
  selects ZOH; invalid value raises `ValueError`.
