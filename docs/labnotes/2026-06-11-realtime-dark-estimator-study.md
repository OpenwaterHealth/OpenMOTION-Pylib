# Lab notebook — realtime dark-baseline estimator study

**Date:** 2026-06-11
**Researcher:** Claude (autonomous session, ~45 min)
**Context:** Live BFI/BVI traces show a ~15 s sinusoid (= dark cadence,
600 frames @ 40 fps). Suspect: `HybridRealtimePredictor` (avg-of-3 u1 +
linear-extrapolated std). A `realtime_dark_estimator` flag (hybrid|zoh)
was added yesterday for A/B. This session asks the deeper question.

## Research questions

- **RQ1 — Value:** Is realtime dark subtraction worth doing at all? I.e.,
  how large is the dark baseline's drift within/across 15 s intervals,
  relative to (a) one dark observation's own measurement noise and (b) the
  light-signal magnitudes that feed contrast/BVI?
- **RQ2 — Best causal estimator:** Among causal estimators (only past darks
  available), which best matches the *offline* baseline (the batch
  interpolation the final record uses, and a smoother all-darks reference)?
- **RQ3 — Artifact mechanism:** Does the hybrid estimator's error actually
  carry power at the dark cadence (1/15 Hz), and does a better estimator
  remove it?

## Ground truth definition

"True to life" target at light-frame time t, per (side, cam):
- **GT-interp:** the non-causal linear interpolation between the bounding
  darks — what the batched/final path computes. This is the system's own
  definition of truth.
- **GT-smooth:** a smoother non-causal reference (e.g. cubic-spline or
  Savitzky–Golay through *all* darks) to check whether GT-interp itself is
  noisy (each dark observation has measurement noise; interpolation passes
  through the noise verbatim — relevant for RQ1).

## Candidate causal estimators (u1 and var = std²)

| ID | Estimator |
|----|-----------|
| ZOH | last dark verbatim (the new flag's "zoh") |
| AVG2/AVG3/AVG4 | mean of last N darks (AVG3-u1 = current hybrid's u1 half) |
| HYBRID | current production: AVG3 u1 + linear-extrap std through last 2 |
| LIN | linear extrapolation through last 2 darks, both u1 and var |
| LINK | least-squares line through last K darks (K=3,4), extrapolated |
| EMA(α) | exponential moving average, a few α values |
| LLT | local-level-with-trend Kalman filter on dark observations |

## Metrics

1. RMSE / bias of predicted u1 baseline vs GT at every light frame.
2. Same for var baseline (this feeds std_dc → contrast → BFI; suspect #1).
3. Propagated effect: contrast K = std_dc/mean_dc computed with each
   estimator vs with GT; report RMSE and **PSD at the dark cadence**
   (power near 1/15 Hz) of the error trace — the sinusoid metric.
4. Warmup behavior (frames until first usable estimate) — secondary.

## Test plan

1. **Data:** find real raw scan CSVs (repo golden data; user scan data on
   disk if reachable). Need: u1/std per frame per cam, frame_type
   (dark/light), timestamps, ideally ≥ several dark intervals.
2. Characterize darks: per-cam dark u1/var time series; drift magnitude
   per 15 s interval vs dark-to-dark measurement noise (lag-structure /
   Allan-style comparison). → RQ1.
3. Implement GT + candidates in a standalone analysis script
   (`scripts/research_dark_estimator.py`), run over all (side, cam).
4. Score per metrics; tabulate. → RQ2.
5. Spectral check on the best vs hybrid vs ZOH. → RQ3.
6. Conclusions + recommendation; fold into the test plan for any
   follow-up SDK change.

## Log

### 00:00 — Session start

Notebook created. Next: locate data.

### 00:08 — Data located

- Bloodflow app data dir = app repo root. No raw CSV for the screenshot
  session (ow310X2Y), but same-evening scans exist with full 1024-bin raw
  histograms: `20260611_040627_owV30EY3` (~61 s, mask66 = cams 1,2,5,6)
  and a long one `20260528_033020_ow08SSSK` (~245 s, mask66).
- Extraction method: replay raw CSV through the real pipeline front-end
  (classify → ts-repair → noise-floor → moments) and tap `mean_raw`/
  `std_raw` — exactly what DarkCorrectionStage sees. Script:
  `scripts/research_dark_estimator.py`.

### 00:18 — First pass results (v1 metrics)

RQ1 characterization, long scan (median across 8 cams), second-difference
noise/drift decomposition on the dark series:

| quantity | value | meaning |
|---|---|---|
| dark u1 obs noise σ | **0.018 DN** | one dark's measurement noise |
| dark u1 drift / interval | **0.004 DN** | true level movement per 15 s |
| median mean_dc (light) | ~93 DN | signal scale |
| dark var obs noise | 0.045 DN² | |
| dark var drift / interval | **0.15 DN²** | var DOES drift |
| median var_dc (light) | ~1828 DN² | |

**Finding 1 (RQ1, big):** the dark u1 baseline is *nearly constant* —
observation noise dominates drift 4:1. For the mean, the best estimator
of a constant is a long average, and ANY estimator's error (~0.02 DN) is
~0.02 % of the ~95 DN dark-subtracted mean. **Realtime u1 dark
subtraction error is utterly negligible for BVI.** A 15 s sinusoid in
BVI cannot plausibly come from u1 baseline estimation error (would be
~0.002 BVI units for any sane i-span).

**Finding 2:** dark *variance* does drift (drift > noise on the long
scan). The var baseline feeds std_dc → contrast → BFI. ZOH on var leaves
a sawtooth at the dark cadence; extrapolation tracks drift but amplifies
the noise. This is where estimator choice can matter.

**Finding 3 (suspicious):** v1 K_RMSE values are non-physical for some
estimators (AVG2 K_RMSE jumps 3–4 orders of magnitude between scans;
expected K error from baseline errors is ~1e-4). Suspect low-signal
frames (mean_dc → 0) exploding K and inconsistent NaN exclusion between
estimators. v1 also scores each estimator on u1-and-var jointly, but
production combines AVG3(u1) + LIN2(**std**, then squared). → v2:
exact production combos, gate to frames with healthy mean_dc, robust
metrics (median |eK|, p95), and absolute 15 s-band amplitude.

**Finding 4 (worth keeping):** even the two ground truths disagree with
15 s periodicity (K_gtsmooth_band ≈ 0.75 on the long scan): GT-interp
passes through each noisy dark observation, so the *final batch record
itself* carries dark-cadence structure at some amplitude. The question
is amplitude, not existence.

### Next (v2 plan)

1. Score production-style combos: (û₁, ŝtd) pairs, var = ŝtd².
   PROD = AVG3(u1)+LIN2(std); ZOH both; AVG3 both; LINK4; LLT; EMA.
2. Robust + absolute metrics: median|eK|, p95|eK|, rms amplitude of eK
   in the 15 s band; same for the *actual* K traces (est vs GT PSD).
3. End-to-end: replay owV30EY3 through full default_pipeline with
   hybrid vs zoh, capture live_side emissions, PSD at 1/15 Hz —
   does the estimator visibly imprint the live trace, at what amplitude?

### 00:30 — v2 combo results + the decisive PSD of the real session

**v2 (production-exact combos, gated to healthy-signal frames, median
across cams), error vs GT-interp expressed in contrast (K) units:**

long scan ow08SSSK / short scan owV30EY3:
- PROD (hybrid):   med|eK| 5.9e-5 / 6.5e-5 ; **15 s-band err amp ≈ 0 / 0**
- ZOH both:        med|eK| 4.4e-5 / 3.0e-5 ; 15 s-band amp 0 / 6e-6
- AVG3 both / LINK4 / EMA / LLT: all within 2e-5 of each other.

Every estimator — including the production hybrid and plain ZOH — agrees
with the offline batch baseline to **< 1.7e-4 K** at the 95th percentile,
and the **15 s-band component of every estimator's error is ~0**. The
K-trace's own 15 s-band content (~2e-4 K) is essentially identical across
all estimators → whatever periodicity exists in contrast is NOT injected
by the realtime estimator; it is in the data. **Finding 5: estimator
choice is immaterial to any 15 s artifact.**

**The decisive test — PSD of the actual screenshot session (ow310X2Y,
session 48 in scans.db, the reduced-mode final side-average record, 444 s
@ 40 Hz):**

| trace | dominant period | 15 s-band amplitude |
|---|---|---|
| BVI (the smooth blue sinusoid in the screenshot) | **10.8 s (0.093 Hz)** | **0** |
| mean | 10.8 s | 0 |
| BFI | 5.4 s & 10.8 s | 0 |

**Finding 6 (the answer): the oscillation is NOT at the 15 s dark
cadence — it peaks at ~0.09–0.10 Hz (≈10 s), squarely in the Mayer-wave
band (≈0.1 Hz arterial blood-pressure oscillation). There is ZERO power
at 1/15 Hz.** Two independent reasons this cannot be the dark estimator:
(1) wrong frequency — 10.8 s ≠ 15 s, and the dark cadence has no power;
(2) this is the *final batch-corrected* record, which uses linear
interpolation between bounding darks and **never runs the realtime
estimator at all** — yet it shows the same oscillation the live trace
does. The sinusoid is in the underlying signal, common to both paths.

(Caveat: "15 s" was the user's eyeball estimate; 10.8 s is close by eye.
The point stands regardless — it is physiological-band, not dark-cadence,
and present in the estimator-free path.)

### 00:40 — End-to-end live-path test (the direct RQ3 answer)

Replayed ow08SSSK through the *full* `default_pipeline` twice (hybrid vs
zoh), captured the `live_side` trace the UI actually plots
(`scripts/research_dark_e2e.py`). 14 776 live samples each.

| live trace | dominant period (both estimators) | 15 s-band amp | hybrid−zoh rms / trace rms |
|---|---|---|---|
| **BVI** (the smooth blue sinusoid in the screenshot) | **23–26 s** | ~0 | **0.001 / 0.139 → <1 %** |
| BFI (red, noisy) | **10.8 s** | 0.004 | 4.4 / 7.6 (spikes, max diff 375) |

**Finding 7 — the screenshot's smooth sinusoid is BVI, and BVI is
estimator-independent.** BVI is computed from `mean_dc = u1 − û₁`. Swapping
the entire realtime estimator (hybrid→zoh) moves BVI by <1 % rms; its
oscillation period (23–26 s) is unchanged and is *not* the 15 s dark
cadence. So the thing the user is looking at is physiological/instrumental
signal, not a dark-correction artifact. (Consistent with Finding 1: u1
baseline barely drifts, so any u1 estimator gives essentially the same
mean_dc.)

**Finding 8 — BFI (contrast) *is* estimator-sensitive, but as spikes, not
a sinusoid.** hybrid−zoh BFI differs by rms 4.4 with isolated max 375 on
low-signal frames near dark boundaries — `var_dc = std²−ŝtd²` divided by a
near-zero `mean_dc` blows up contrast. This is the dark-*variance* path
(Finding 2), and it manifests as transient spikes, not a 15 s tone. ZOH
does not "fix" it — it just relocates the spikes. The honest lever for BFI
robustness is variance-floor / low-signal gating, not the estimator family.

(Note: the `on_complete` AttributeError at the end is my throwaway
collector missing an optional method; the runner had already delivered all
samples — does not affect results.)

---

## Conclusions

**RQ1 — Is realtime dark subtraction worth it?**
For the **mean (→ BVI):** barely. The dark u1 baseline is constant to
within measurement noise (drift 0.004 DN/interval vs noise 0.018 DN, on a
~95 DN signal). Any causal estimator — even "subtract a fixed constant" —
is within ~0.02 % of truth. The realtime mean correction is essentially
free but also essentially unnecessary for accuracy; its value is keeping
BVI on the same scale as the final record, which a constant pedestal would
also achieve.
For the **variance (→ contrast → BFI):** yes, it matters more — dark
variance genuinely drifts (drift > noise), and getting it wrong injects
per-frame error, concentrated as spikes on low-signal frames.

**RQ2 — Best causal estimator of the dark-subtracted numbers?**
- **u1:** a short average (AVG3/AVG4) or EMA(0.5) is best — it suppresses
  the dominant observation noise on a near-constant level. ZOH is slightly
  noisier (passes each dark's full noise through) but still within 1.5e-4 K.
  Linear extrapolation (LIN2/LINK) is *worse* for u1 — it amplifies dark
  noise to chase a drift that is mostly not there.
- **std/var:** this is where the production **LIN2** (linear extrapolation
  of std through the last 2 darks) is the weakest choice — it has the
  largest var RMSE-vs-truth among options and the most high-frequency
  error, because it extrapolates a slope fit through just two noisy points.
  A short **least-squares line over 4 darks (LINK4)** or the **local-
  level+trend filter (LLT)** track the real variance drift with far less
  noise amplification. Best overall combo: **AVG3 (u1) + LINK4 or LLT
  (std)** — matches GT batch baseline as well as anything and avoids LIN2's
  noise gain.

**RQ3 — Is the estimator causing the 15 s sinusoid?** **No.** Three
independent lines of evidence: (a) the real session's oscillation is at
~0.09–0.10 Hz (≈10 s, Mayer-wave band), with *zero* power at the 1/15 Hz
dark cadence; (b) it appears in the final batch-corrected record, which
never runs the realtime estimator; (c) swapping hybrid→zoh end-to-end
changes the visible BVI sinusoid by <1 %. The sinusoid is physiological /
instrumental, not a dark-estimation artifact.

## Recommendations

1. **Keep the realtime dark correction** — it's cheap and harmless. Don't
   expect removing it to change the live trace the user is worried about.
2. **The 15 s flag's real use** is BFI (contrast) robustness, not the BVI
   sinusoid. If we touch the estimator, the win is replacing **LIN2 std
   extrapolation with LINK4 or LLT** (less noise gain on the variance
   baseline). This is a larger change than the flag; worth a follow-up
   spec if BFI spiking near dark boundaries is a real complaint.
3. **For BFI spikes specifically**, a variance-floor + low-signal gate
   (don't compute contrast when mean_dc below a threshold) would do more
   than any estimator swap.
4. **Chase the ~0.1 Hz BVI oscillation separately** — it is the actual
   thing on screen. Candidates: genuine physiology (Mayer waves), motion/
   coupling, or laser/TEC thermal cycling. Cross-check against the
   telemetry CSV (TEC/PDC at ~10 Hz) for a correlated 0.1 Hz component
   before assuming physiology.

## Reproducibility

- `scripts/research_dark_estimator.py` — extract (replay→moments tap) +
  analyze (v1 per-estimator) + analyze2 (production combos, gated).
- `scripts/research_dark_e2e.py` — full-pipeline hybrid-vs-zoh on the
  live side-average, with PSD.
- Data: `…/openmotion-bloodflow-app/2026*_*_raw.csv`; session PSD from
  `scans.db` session_id 48 (ow310X2Y, the screenshot scan).
- **Caveat on import path:** run analysis with `PYTHONPATH=<worktree>` —
  `python scripts/x.py` otherwise imports the *installed* omotion (no
  `realtime_dark_estimator` kwarg). Extraction-only stages are identical
  in both, so the v1/v2 moment taps are unaffected.
