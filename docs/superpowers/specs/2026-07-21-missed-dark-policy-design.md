# Missed-dark policy — design

**Issue:** [#175](https://github.com/OpenwaterHealth/openmotion-sdk/issues/175)
**Date:** 2026-07-21
**Status:** approved, not yet implemented

## Problem

Dark frames are scheduled positionally: `abs_id == discard_count + 1`, then every
`dark_interval` thereafter (`classify.py:_is_dark`). At the defaults that is abs_id
10, 601, 1201, … — one dark every 600 frames, or 15 s at 40 fps.

`PendingInterval` closes an interval when a second dark arrives, and
`LinearInterpolation` interpolates the dark baseline (u1 and variance) between the
two bounding darks. If a scheduled dark **never arrives** — a dropped frame, a
camera dropout, a frame rejected as stale — the interval does not close at that
position. The next dark, 600 frames later, closes it instead.

Two consequences today:

1. The interval silently spans ~1200 frames instead of ~600, and every light frame
   in it gets its baseline interpolated across the doubled span. Worst error sits
   where the missing dark should have been. **~30 s of a scan is degraded per
   missed dark, and nothing records that it happened.**
2. `timestamp_repair.py` hardcodes `"frame_type": "light"` on every synthetic
   NaN-fill row (it runs at stage 6, after classification at stage 1, so fill rows
   are never tested against the dark schedule). A fill row standing in for a missed
   dark therefore enters the interval as a NaN light sample.

### The inconsistency this resolves

Three closely-related conditions are handled three different ways:

| condition | current behaviour |
|---|---|
| terminal dark missing | interval left open, corrected data **discarded**, logged ERROR |
| mid-scan dark missing | **silently** interpolate across 2x the span |
| dark contaminated (integrity guard fires) | **used as a boundary anyway**, logged WARNING |

`DarkIntegrityGuard.check()` returns a pass/fail bool that `DarkCorrectionStage`
discards.

## Scope

**In scope:** missing darks only.

**Out of scope, deliberately:**

- **Contaminated darks.** The guard's verdict stays ignored. Separate ticket.
- **Any tolerance for "how wide is too wide."** We keep-and-flag unconditionally;
  no threshold is invented here.
- **`timestamp_repair.py` changes.** The detection approach below removes the need.
- **Terminal dark missing.** Keeps its current discard behaviour — it is a
  genuinely different condition, with no right-hand boundary to interpolate
  between at all.

## Decisions

1. **Disposition: keep and flag.** Frames in a widened interval are emitted, marked
   degraded. No data is discarded. Consumers and analytics filter on the flag.
2. **Representation: a new ranked `quality` value**, reusing the existing
   single-string mechanism rather than adding a field or a flag set.
3. **Detection: from the dark schedule**, not from re-typing fill rows.

## Design

### Detection

`DarkCorrectionStage` currently has no knowledge of the dark schedule — its
constructor takes no `discard_count` or `dark_interval`; it only reacts to frames
the classifier already typed `"dark"`. Both values are available in
`factory.default_pipeline()` and are already passed to `FrameClassificationStage`.

Pass them to `DarkCorrectionStage` as well. When an interval closes as
`[left_abs, right_abs]`, count the scheduled dark positions falling **strictly
inside** that range. Zero means the interval is nominal; one or more means that
many darks were missed, and we know precisely which abs_ids.

The schedule predicate is **extracted from `classify.py:_is_dark` into one shared
function** that both stages import. Two independent copies of the dark schedule
would drift and reproduce exactly this class of bug.

Why this beats the approach sketched in #175 (re-typing schedule-aligned fill rows
as `"dark"`):

- No `timestamp_repair` change.
- No risk of a NaN-valued `DarkObservation` poisoning `DarkHistory` or becoming an
  interval boundary — the hazard that caused item 4 to be deferred from #114 in the
  first place.
- Catches a missed dark from **any** cause, not just the NaN-fill path.

### Marking

Frames in a widened interval get `quality` escalated to a new `wide_interval`
value. Applied in `DarkCorrectionStage`, where the `CorrectedInterval` is
constructed, so it flows through ShotNoise -> BfiBvi -> DarkFrameHold -> SideAverage
-> sinks with no changes to any of those stages.

Escalation is worst-wins against whatever the frame already carries, matching the
existing convention.

### Quality rank

```
ok 0  <  ts_corrected 1  <  nan_filled 2  <  wide_interval 3
```

`wide_interval` ranks highest because it is **the only flag that silently biases the
aggregate**. A `nan_filled` camera emits NaN and is dropped by
`spatial_side_average`'s `nanmean` on its own; a wide-interval camera contributes
real-looking numbers built on a stretched baseline. If `nan_filled` could mask it,
the side-average row would hide the one thing actually skewing the value.

Housekeeping in the same change: `_QUALITY_RANK` currently lives only in
`side_avg.py:39`, whose comment claims it "Mirrors `sinks._QUALITY_RANK`" — no such
constant exists in `sinks.py`. Move the rank to one shared location and correct the
comment.

**Hazard to handle explicitly.** The lookup is
`_QUALITY_RANK.get(fq, 0)` — an **unrecognised quality string silently ranks as
`ok`**. So any consumer holding its own copy of the rank map that is not updated
will treat `wide_interval` as the *best* quality rather than the worst, which is
the exact inversion of what this change is for. Two required mitigations:

1. Single shared rank definition, imported — never re-declared.
2. Audit consumers outside this module before landing. Checked at design time:
   neither `openmotion-bloodflow-app` nor `openmotion-test-app` has independent
   quality logic — every hit is a *bundled* copy of `omotion` inside a packaged
   `dist/` build, so they inherit whatever the SDK ships. No app-side change needed,
   but packaged builds carry the old 3-value rank until they are rebuilt.

### Where the flag actually surfaces

`5c3e107 refactor(pipeline): drop quality column from corrected CSV` is already on
`next`, and removed `_QUALITY_RANK` from `sinks.py` along with the column. As of
today `quality` reaches exactly one artifact:

- **`session_data.quality` in the scan DB** (`sinks.py:754`) — the only science
  record that carries it.
- **Not the corrected CSV** — the column was deliberately dropped.

So "keep and flag" makes the degradation visible to DB/analytics consumers, and
**invisible to anyone working from the corrected CSV**. The `MissedDarkWarning`
diagnostic and the scan-summary tally are the second surface, and are independent of
both — they fire regardless of which artifact a consumer reads.

This is a known, accepted limitation of the chosen approach rather than an
oversight. Revisit if the corrected CSV becomes the primary analysis path again.

### Diagnostics

New `MissedDarkWarning` batch event, alongside the existing `DarkIntegrityWarning`,
carrying:

- `side`, `cam_id`
- `expected_abs_ids` — the scheduled positions that produced no dark
- `left_abs`, `right_abs` — the interval that actually closed
- `n_missed`

Emitted once per (side, cam) per widened interval, with a WARNING log. Sinks already
route diagnostics events and tally them by type in the scan summary, so missed darks
surface in the same place `TerminalDarkResult` and `TimestampMisalignmentWindow`
already do — **no sink changes required**.

## Testing

- Schedule counter in isolation: darks at 10/601/1201; interval `[10, 1201]` reports
  1 missed at 601; interval `[601, 1201]` reports 0.
- Shared predicate: the extracted function agrees with the current
  `classify.py:_is_dark` across a sweep of abs_ids and both config values.
- A widened interval marks every frame `wide_interval`; a nominal interval leaves
  `quality` untouched.
- Worst-wins: a `nan_filled` frame inside a widened interval ends up
  `wide_interval`.
- `MissedDarkWarning` is emitted once per (side, cam) with correct fields.
- The side-average row inherits `wide_interval` from a contributing camera.
- Regression: golden replay is unchanged when no dark is missed.

## Follow-ups, not blocking

**Quantify the drift.** Nothing here depends on knowing how much accuracy a doubled
interval actually costs, since we keep-and-flag unconditionally. But that number is
what would justify a future tolerance-based *discard* policy — or show that a 2x
span is merely untidy rather than harmful.

It is not currently measurable from the scans on hand: a 20 s scan at
`dark_interval` 600 contains only two darks (abs_id 10 and 601). A ~2 minute scan
would give 5-6 darks and enough interval-to-interval baseline data to answer it.
Cheap to run on the bench.
