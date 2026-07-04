# Open-Motion Pulse-Waveform Analysis — Technical Reference

**Implementation:** `omotion/pulse/` — a numpy-only cardiac-pulse engine, plus one pipeline stage (`omotion/pipeline/stages/pulse_waveform.py`) that drives it during a live scan. The entry point is `PulseWaveformAnalyzer` (`omotion/pulse/analyzer.py`); the pipeline inserts a `PulseWaveformStage` when `default_pipeline(enable_pulse=True)` is called in reduced mode.

**Audience:**

1. **Scientists and engineers** who need a mathematically precise description of every transformation between the per-side Blood-Flow-Index (BFI) time series and the pulse template, envelope, and morphology features (PI, RI, AUC, rise time, augmentation index, heart rate) shown in the pulse viewer.
2. **Contributors** extending the analyzer, the pipeline stage, or the app-side pulse sink/UI. §3–§11 describe the engine; §12 the stage contract; §13 the app consumers.
3. **Auditors** who need a static account of how a noisy 40 Hz BFI stream becomes a reliability-gated cardiac pulse. Every formula below is implemented in the cited file at the cited symbol.

This document is the pulse-domain companion to [`SciencePipeline.md`](SciencePipeline.md), which covers everything **upstream** of the BFI value this engine consumes (raw histogram → dark correction → shot-noise correction → BFI/BVI → per-side average).

---

## 1. Where pulse analysis sits

The science pipeline (`SciencePipeline.md` §5) turns raw histograms into a per-frame, per-camera BFI. In **reduced mode** (clinical / pulse-viewer scans), `SideAverageStage` collapses the enabled cameras of each side into one BFI value per capture and emits it as `LiveEmit("live_side", SideAverageSample(t, frame_id, side, bfi, bvi))` (`SciencePipeline.md` §5.10).

`PulseWaveformStage` consumes exactly that event. It is **additive**: it reads `live_side` events and appends `LiveEmit("pulse", PulseAnalysis)` events; it never mutates the batch's per-frame arrays.

```
                              per-side BFI, 40 Hz
 SideAverageStage ──LiveEmit("live_side")──► PulseWaveformStage
                                                    │
                              one PulseWaveformAnalyzer per side
                                     (rolling 12 s window)
                                                    │
                              every emit_every=8 samples: snapshot()
                                                    │
                                          LiveEmit("pulse", PulseAnalysis)
                                                    ▼
                                     "pulse" channel  ──►  PulseSink (bloodflow-app)
                                                             → pulseSnapshot signal → QML
                                                               (PulseCanvas + PulseStatsPanel)
```

The **same** `PulseWaveformAnalyzer` drives the no-hardware demo (`DemoScanSource`, §15) and the unit tests (`synth.py` ground truth, §14), so the live path and the offline paths are guaranteed to compute identically. The engine is pure numpy and Qt-free by design.

**Placement in the chain** (`omotion/pipeline/factory.py`): the stage is appended only when `enable_pulse and metadata.reduced_mode`, immediately after `SideAverageStage` (whose `live_side` events it depends on) and before the final `Tee("live")`.

---

## 2. The two data carriers

`omotion/pulse/types.py` defines the snapshot returned by every `snapshot()` call.

### 2.1 PulseAnalysis — one snapshot for one side

| Field | Shape | Meaning |
|---|---|---|
| `side` | str | `"left"` / `"right"` |
| `phase` | `(bins,)` | Cardiac-phase axis, `linspace(0, 1, bins, endpoint=False)` |
| `template` | `(bins,)` | Ensemble-average beat, BFI units (§8) |
| `env_min` / `env_max` | `(bins,)` | Per-phase min / max over the recent beats (the envelope) |
| `env_p25` / `env_p75` | `(bins,)` | Per-phase 25th / 75th percentile (the IQR band) |
| `live_phase` / `live_value` | `(k,)` | The in-progress (post-last-onset) beat, phase-mapped (§11) |
| `features` | `PulseFeatures` | Morphology metrics (§9) |
| `beat_count` | int | Accepted beats in the current window |
| `updated_beat` | bool | A beat closed since the previous snapshot |

`to_qvariant()` flattens every array to a plain Python list (`camelCase` keys) so the snapshot can cross the PyQt6 signal boundary into QML.

### 2.2 PulseFeatures — morphology metrics

| Field | Units | Definition (§9) |
|---|---|---|
| `hr_bpm` | bpm | `60 / median(RR)` |
| `mean_flow` (MF) | BFI | `mean(template)` |
| `psf` | BFI | `max(template)` — peak systolic flow |
| `edf` | BFI | `template[-1]` — end-diastolic flow |
| `amp` | BFI | `PSF − EDF` |
| `pi` | — | pulsatility index `AMP / MF` |
| `ri` | — | resistivity index `AMP / PSF` |
| `auc` | BFI | `sum(template − EDF) / bins` |
| `rise_time_frac` | 0..1 | phase of the systolic peak |
| `rise_time_ms` | ms | `rise_time_frac · median(RR) · 1000` |
| `aix` | — | augmentation index (best-effort at 40 Hz) |
| `beat_count` | int | accepted beats |
| `consistency` | Pearson r | median beat-vs-template correlation |
| `periodicity` | 0..1 | normalized autocorrelation peak — **the reliability discriminator** (§5, §10) |
| `reliable` | bool | a genuine, regular cardiac pulse is present (§10) |

All feature fields default to `NaN` (`beat_count` to 0, `periodicity` to 0.0, `reliable` to `False`) — the "not enough data yet" state.

---

## 3. PulseWaveformAnalyzer — ingest and windowing

**File:** `omotion/pulse/analyzer.py`. One instance per side.

`add_samples(t, v)` appends timestamped BFI samples and trims to a rolling window:

```
_t, _v = concat(existing, new)
cutoff = _t[-1] − raw_window_s            # raw_window_s = 12.0 s
keep    = _t >= cutoff
_t, _v  = _t[keep], _v[keep]
```

`add_samples` only buffers; **all computation is lazy in `snapshot()`**, so a fast live caller can add every frame and snapshot at its own cadence (the stage snapshots every 8 samples, §12). At 40 Hz a 12 s window holds ~480 samples ≈ 8–14 beats.

`snapshot()` returns an empty snapshot (all-NaN bins, `beat_count=0`) until at least 8 samples are buffered. `reset()` clears the window and is called at scan start / replay reuse.

**Sampling-rate estimate.** `fs = 1 / median(diff(_t))`, falling back to `fs_hint = 40.0` when fewer than 3 samples or a non-positive median. Every downstream bpm↔sample conversion uses this estimate.

The full `snapshot()` computation is: band-limit → periodicity/period → beat onsets → per-beat resample + QC → template/envelope → features → live beat. §4–§11 walk each step.

---

## 4. Band-limiting for beat detection

**Function:** `_band_limit(vfill, fs)`. Beats are located on a **band-limited copy** of the signal; the morphology is always measured on the **original** signal (§7). First, NaN samples are linearly interpolated (`_nan_interp`) so the detection signal stays continuous.

Two interchangeable strategies, selected by `band_method`:

### 4.1 `"movavg"` (default) — moving-average high-pass + smoothing

```
base_win = round(fs · 60 / min_bpm)       # samples in one longest beat (~60 @ 40 fps, 40 bpm)
ac       = vfill − movavg(vfill, base_win) # high-pass: remove slow baseline / wander
sm       = movavg(ac, smooth_win)          # light low-pass (smooth_win = 5)
```

`_movavg` is an edge-padded box filter (length-preserving). Subtracting a one-beat-wide moving average removes baseline wander and DC while preserving the beat-to-beat oscillation; the short 5-sample average then suppresses per-frame noise without smearing the systolic upstroke.

### 4.2 `"modwt"` — sym4 stationary-wavelet cardiac band

The DCS pulsatility-paper method, implemented dependency-free (no PyWavelets). `_wavelet_band` runs an **à-trous** (stationary / undecimated) sym4 transform and sums the detail levels whose octave band overlaps the cardiac band:

```
level j covers ~[fs/2^(j+1), fs/2^j] Hz; centre f_c = fs / 2^j / √2
in-band  ⟺  (0.7·min_bpm/60) ≤ f_c ≤ (1.3·max_bpm/60)
band(x)  = Σ_{j in-band} detail_j(x)      # à-trous sym4 decomposition
```

`_atrous(filt, up)` inserts `up−1` zeros between taps (the "holes"); `_conv_reflect` convolves with reflect padding. The sym4 low-pass `_SYM4_LO` matches PyWavelets' `dec_lo`; the high-pass is its quadrature mirror `g[k] = (−1)^k · h[N−1−k]`. If no level lands in-band (degenerate `fs`), it falls back to the movavg detrend.

### 4.3 Why movavg is the default

Three independent band-limits — moving-average, a scipy Butterworth bandpass + `find_peaks`, and the sym4 MODWT — benchmark **statistically indistinguishable** for heart-rate recovery across a noise / HR sweep. The residual error is the 40 fps sampling limit at high HR, which no filter fixes. So the simplest method is the default and **scipy is not a dependency**. Don't change the default or add scipy/pywt without re-benchmarking (`tests/test_pulse_analyzer.py`).

---

## 5. Period and periodicity — the autocorrelation

**Function:** `_autocorr_period(sm, fs, min_bpm, max_bpm) → (lag, peak)`.

The band-limited signal's normalized autocorrelation gives both the dominant cardiac period **and** a scalar "how periodic is this really" strength:

```
ac    = sm − mean(sm)
corr  = autocorr(ac)[0 : n]               # lags 0 .. n−1
corr /= corr[0]                            # normalize so corr[0] = 1
lag_min = max(1, fs·60 / max_bpm)          # shortest plausible RR
lag_max = min(n−1, fs·60 / min_bpm)        # longest plausible RR
k       = argmax(corr[lag_min : lag_max+1])
period      = lag_min + k                  # samples per beat
periodicity = corr[period]                 # 0 .. 1
```

`periodicity` is the crux of the reliability gate (§10). A real, regular pulse peaks high (**~0.8–0.9**); band-limited noise from a static phantom or a lead-off channel — which can otherwise fake a positive amplitude and high beat-consistency — peaks low (**~0.1–0.2**). Amplitude and consistency alone cannot separate the two; periodicity can.

The period sets the **refractory distance** for peak detection:

```
refractory = max(1, round(0.6 · period))          if a period was found
           = round(fs · 60 / max_bpm)             otherwise
```

---

## 6. Beat onsets — peak-then-foot

**Functions:** `_detect_peaks`, `_segment_feet`.

Beat windows are cut at **feet** (end-diastolic troughs), but troughs are found *relative to peaks*, and peaks are found on the band-limited signal while feet are found on the original:

1. **Systolic peaks** (`_detect_peaks`) — interior local maxima of `sm` (`sm[i] > sm[i−1] and sm[i] ≥ sm[i+1]`) at least `refractory` samples apart; when two are closer than the refractory, the **taller wins**.

2. **Feet / onsets** (`_segment_feet`) — for each consecutive peak pair `(a, b)`, the onset is `a + argmin(vsig[a:b])` — the minimum of the **original** signal between the two peaks.

Detecting peaks on the smoothed signal but locating each foot on the raw signal keeps beat windows aligned to real end-diastolic troughs. The moving-average detrend shifts minima, so trough-detecting the detrended signal directly would window beats off-centre — a bug that showed up as systolic peaks landing at phase ~0.43 and being QC-rejected. If fewer than two peaks are found, no onsets are returned and the snapshot is empty.

---

## 7. Per-beat resample and quality control

For each consecutive onset pair `(o0, o1)` with times `(t0, t1)`:

**RR gate.** Reject the interval unless it is a plausible heart rate:

```
60 / max_bpm  ≤  (t1 − t0)  ≤  60 / min_bpm      # 0.33 s .. 1.5 s at defaults
```

**Phase resample** (`_resample_beat`). The **original**-signal segment `v[o0:o1]` (≥ 3 finite samples) is linearly resampled onto a fixed phase grid:

```
src = linspace(0, 1, m, endpoint=False)         # m = raw beat length
dst = linspace(0, 1, bins, endpoint=False)       # bins = phase_bins = 60
rb  = interp(dst, src, seg)                       # one beat, 60 phase bins
```

**Quality control** (`_beat_passes_qc`) — adapted from the DCS pulsatility QC rules. A resampled beat `rb` is rejected if any of:

| Rule | Rejects |
|---|---|
| `argmax(rb) / bins ≥ 0.6` | systolic peak too late (not a systolic-led beat) |
| `rb.max() − rb.min() ≤ 0` | flat / inverted |
| `rb[bins//10] − rb[0] ≤ 0` | no systolic upstroke in the first ~10 % |
| `edf > 2·max(onset, ε)` **or** `edf > mean` | end-diastolic value implausibly high (baseline drift, not a beat) |

Beats that pass are stacked; their RR intervals are recorded for the heart-rate estimate. Both lists are truncated to the most recent `history_beats` (= 20).

---

## 8. Template and envelope

Given the accepted-beat stack `stack` of shape `(n_beats, bins)`:

```
template = mean(stack, axis=0)              # ensemble-average beat
env_min  = min(stack, axis=0)               # per-phase minimum  (envelope floor)
env_max  = max(stack, axis=0)               # per-phase maximum  (envelope ceiling)
env_p25  = percentile(stack, 25, axis=0)    # IQR band, lower
env_p75  = percentile(stack, 75, axis=0)    # IQR band, upper
```

The template is the "average pulse"; `env_min`/`env_max` are the historical spread the viewer shades as the outer envelope; `env_p25`/`env_p75` are the tighter interquartile band. These are what `PulseCanvas` draws behind the live trace (§13.2).

---

## 9. Morphology features

**Function:** `_features(template, stack, rr, periodicity)`. All operate on the ensemble template except HR (from RR) and consistency (from the stack).

```
PSF  = nanmax(template)                     # peak systolic flow
EDF  = template[-1]                          # end-diastolic flow (template end)
MF   = nanmean(template)                     # mean flow
AMP  = PSF − EDF
PI   = AMP / MF        (NaN if MF == 0)      # pulsatility index
RI   = AMP / PSF       (NaN if PSF == 0)     # resistivity index
AUC  = nansum(template − EDF) / bins         # area under the baseline-subtracted beat
rise_frac = argmax(template) / bins          # phase of the systolic peak, 0..1
HR   = 60 / median(RR)                        # bpm
rise_ms   = rise_frac · median(RR) · 1000     # systolic upstroke time, ms
consistency = median( pearson(beat, template) for beat in stack )
```

**Augmentation index** (`_augmentation_index`) — best-effort at 40 Hz. Search the template *after* the systolic peak for a secondary local maximum (the diastolic / dicrotic wave); if one exists,

```
AIx = (diastolic_peak − EDF) / (PSF − EDF)
```

Returns `NaN` when no secondary peak is resolvable (common at 40 Hz — the dicrotic notch is near the sampling limit) or when `PSF − EDF ≤ 0`.

`consistency` is the median Pearson correlation of each accepted beat against the template (`_pearson` is the mean-subtracted normalized dot product) — a measure of beat-shape repeatability, distinct from `periodicity` (timing regularity).

---

## 10. Reliability gate — "is this a real pulse?"

**The single most important guard.** Band-limiting narrowband noise (a static phantom, an unplugged channel) produces something that *looks* like a pulse: it has a positive amplitude, a plausible-looking template, even high beat-to-beat consistency. What it lacks is **timing regularity**, and that is exactly what the normalized autocorrelation peak measures.

```
reliable = (len(stack) ≥ MIN_BEATS_RELIABLE)     # ≥ 3 accepted beats
           and (amp > 0.0)                         # a real excursion
           and (periodicity ≥ PERIODICITY_MIN)     # ≥ 0.45 autocorrelation
```

`MIN_BEATS_RELIABLE = 3`, `PERIODICITY_MIN = 0.45`. Calibrated so a real cardiac pulse (periodicity ~0.8–0.9) passes and band-limited phantom / lead-off noise (~0.1–0.2) does not. The threshold sits well below the real-signal band and well above the noise band, so it is robust to moderate SNR.

`reliable` drives the UI: when it is `False`, `PulseCanvas` dims the trace and shows a "No pulse detected" banner, and `PulseStatsPanel` blanks the pulse-shape metrics (a phantom must never display a fake HR or PI). See §13.

---

## 11. The live (in-progress) beat

**Function:** `_append_live`. Separately from the completed-beat template, the snapshot carries the beat currently being drawn across the screen — the samples after the **last** detected onset:

```
last_t = t[onsets[-1]]
mask   = t >= last_t
rr_est = median(rr)            if beats exist
         median(diff(onset_times)) else
         60/72                 as a last resort
phase       = clip((t[mask] − last_t) / rr_est, 0, 1)
live_phase  = phase[finite],  live_value = v[mask][finite]
```

`live_value` is the **original** BFI (not resampled), phase-mapped by the estimated RR so it sweeps 0→1 across the current beat. This is the moving trace the viewer overlays on the static template + envelope.

---

## 12. PulseWaveformStage — the pipeline driver

**File:** `omotion/pipeline/stages/pulse_waveform.py`. **Reads:** `LiveEmit("live_side", …)` events. **Appends:** `LiveEmit("pulse", PulseAnalysis)` events. Never mutates per-frame arrays.

It holds two analyzers (`{0: left, 1: right}`) and a per-side counter. For each batch:

```
for event in batch.events (snapshot of the list):
    if event is LiveEmit("live_side", SideAverageSample):
        side = sample.side
        analyzers[side].add_samples([sample.t], [sample.bfi])
        since_emit[side] += 1
for side in touched:
    if since_emit[side] >= emit_every:        # emit_every = 8 samples (~0.2 s)
        since_emit[side] = 0
        batch.events.append(LiveEmit("pulse", analyzers[side].snapshot()))
```

Snapshotting every 8 side-samples (~5 Hz at 40 fps) keeps the viewer smooth without recomputing the template on every frame. `on_scan_stop(batch)` emits a final snapshot for both sides so the last partial window is delivered. `reset()` rebuilds both analyzers.

Because the stage only runs in reduced mode (§1), the per-side average it needs always exists; a non-reduced scan carries no pulse analysis.

---

## 13. App-side consumers (bloodflow-app)

The SDK ships no UI. The bloodflow-app wires the `"pulse"` channel to QML.

### 13.1 PulseSink

`pulse_view.py`: `channels = {"pulse"}`; `consume("pulse", analysis)` forwards each `PulseAnalysis` to a callback that the connector wires to the `pulseSnapshot(side, map)` Qt signal (via `PulseAnalysis.to_qvariant()`). Decoupled from the connector so it is trivially unit-testable.

### 13.2 PulseCanvas

`components/PulseCanvas.qml` draws, back to front: the **min/max envelope** as a shaded band, the **p25/p75 IQR** as a darker band, the **template** as the accent-colour line, and the **live trace** (`live_phase` / `live_value`) as a bright line sweeping across. Y-scaling is auto-ranged over the union of the two sides' envelopes and live values so left and right share one axis. When `features.reliable` is `False`, the trace is dimmed and a "No pulse detected" banner is shown.

### 13.3 PulseStatsPanel — left-vs-right comparison

`components/PulseStatsPanel.qml` renders a metric table (Left | Right | Δ) over §9's features, plus a **template shape-similarity** readout:

```
r = pearson(leftSnap.template, rightSnap.template)     # both sides, same phase grid
```

`r = 1.00` means identical morphology; lower values indicate left/right asymmetry in the pulse *shape* (independent of amplitude, since Pearson r is mean-and-scale invariant). Pulse-shape metrics (HR, PI, RI, …) are blanked per-side when that side's `reliable` is `False`, and the Δ column is blanked unless both sides are reliable — so a phantom side never contributes a fake number to the comparison.

---

## 14. Synthetic BFI generator (ground truth)

**File:** `omotion/pulse/synth.py`. Used by the unit tests (known ground truth) and, through `DemoScanSource` (§15), by the no-hardware demo.

A single beat is the sum of two Gaussians on the phase axis — a tall early **systolic** wave and a lower, later **diastolic (dicrotic)** wave (the standard synthetic-PPG construction, Nature Sci. Rep. 2020, doi:10.1038/s41598-020-69076-x):

```
beat(phase) = sys_amp·exp(−½((phase − sys_pos)/sys_wid)²)
            + dia_amp·exp(−½((phase − dia_pos)/dia_wid)²)
```

`SHAPE_PRESETS` provides five morphologies — `normal`, `high_pi` (tall narrow systolic spike, faint diastole), `low_pi` (shorter systole, prominent diastole), `damped` (low-amplitude, smeared), and `noisy` (normal shape, callers add heavy noise) — that differ in intrinsic shape, not just size. A full signal strings beats with Normal-distributed RR intervals (heart-rate variability), then adds optional measurement noise and slow sinusoidal baseline wander:

```
onsets: RR_k = max(0.3, 60/bpm + N(0, hrv_frac·60/bpm))     # 0.3 s floor = 200 bpm
phase(t) = clip((t − beat_start) / beat_len, 0, 1)
v(t)     = baseline + amp·beat(phase(t)) + wander·sin(2π·0.1·t) + N(0, noise)
```

`synth_bfi` returns one side; `synth_pair` returns a correlated left/right pair sharing **one** onset train (so the two waveforms stay cardiac-aligned), with asymmetry introduced by `right_amp_ratio` and/or a different `right_shape`. Deterministic for a given `seed`.

---

## 15. DemoScanSource — full-pipeline replay (no hardware)

**File:** `omotion/pulse/scan_synth.py`. Feeds a recorded `*_bfi_results.csv` (or synthetic BFI) through the **real** `default_pipeline`, so the whole chain (dark correction → shot noise → BFI/BVI → side average → pulse) recomputes ~the recorded values with no sensors attached.

**Inverse model.** The science pipeline maps histogram width → contrast → BFI. `DemoScanSource` runs that backward: for each recorded `(BFI, BVI)` it picks a target contrast and mean, then synthesises a Gaussian histogram of the width that the forward pipeline will map back to ~that BFI. The calibration brackets are chosen (not fit) and used **both** to invert here and by the pipeline forward, so the values round-trip:

```
BFI 0..10  ↔  contrast [c_max .. c_min]      c_min, c_max = 0.04, 0.18
BVI 0..10  ↔  mean_dc  [i_max .. i_min]      i_min, i_max = 100.0, 200.0
std(target contrast) = √( (contrast·mean_dc)² + adc_gain·mean_dc·gain_cam )   # invert shot-noise
```

The `adc_gain·mean·gain_cam` term is the same shot-noise variance the pipeline will *subtract* (`SciencePipeline.md` §5.8), so it must be *added* here or the recovered contrast lands below the √mean noise floor and the BFI flat-lines. Frames follow the classifier's positional schedule (warmup 1..9, boundary dark at 10, then every `dark_interval`, else light), interleaved across both sides by timestamp so a realtime replay advances both together.

**Stop behaviour.** On a mid-replay Stop (`cancel_scan → close()`), the source emits a synthetic **laser-off terminal-dark frame** per active camera, continuing the frame-id/timestamp sequence — mirroring the firmware's terminal dark on a real cancel — so the pipeline closes the open interval instead of logging `TERMINAL DARK MISSING` and dropping it. See `SciencePipeline.md` §5.7.8 for the terminal-dark flush this feeds.

---

## 16. Output data types reference

`omotion/pulse/types.py`:

```python
@dataclass
class PulseFeatures:
    hr_bpm:         float = nan      # 60 / median RR
    mean_flow:      float = nan      # MF, mean of template
    psf:            float = nan      # peak systolic flow (template max)
    edf:            float = nan      # end-diastolic flow (template end)
    amp:            float = nan      # PSF − EDF
    pi:             float = nan      # AMP / MF
    ri:             float = nan      # AMP / PSF
    auc:            float = nan      # ∫(template − EDF) / bins
    rise_time_frac: float = nan      # systolic-peak phase, 0..1
    rise_time_ms:   float = nan      # rise_time_frac · median RR · 1000
    aix:            float = nan      # augmentation index (best-effort)
    beat_count:     int   = 0
    consistency:    float = nan      # median Pearson r, beats vs template
    periodicity:    float = 0.0      # normalized autocorrelation peak, 0..1
    reliable:       bool  = False    # reliability gate (§10)

@dataclass
class PulseAnalysis:
    side:       str
    phase:      np.ndarray    # (bins,) 0..1
    template:   np.ndarray    # (bins,) ensemble-average beat
    env_min:    np.ndarray    # (bins,) per-phase min
    env_max:    np.ndarray    # (bins,) per-phase max
    env_p25:    np.ndarray    # (bins,) 25th percentile
    env_p75:    np.ndarray    # (bins,) 75th percentile
    live_phase: np.ndarray    # (k,) in-progress beat phase
    live_value: np.ndarray    # (k,) in-progress beat BFI
    features:   PulseFeatures
    beat_count: int
    updated_beat: bool
```

---

## 17. Key defaults

| Parameter | Default | Defined in | Meaning |
|---|---|---|---|
| `phase_bins` | 60 | `PulseWaveformAnalyzer` | Points per resampled beat / template |
| `history_beats` | 20 | `PulseWaveformAnalyzer` | Most recent beats kept for the template + envelope |
| `raw_window_s` | 12.0 | `PulseWaveformAnalyzer` | Rolling BFI window length (~8–14 beats at rest) |
| `min_bpm` / `max_bpm` | 40 / 180 | `PulseWaveformAnalyzer` | Plausible heart-rate band (sets RR gate, refractory, autocorr lags) |
| `smooth_win` | 5 | `PulseWaveformAnalyzer` | Post-detrend smoothing window (movavg method) |
| `band_method` | `"movavg"` | `PulseWaveformAnalyzer` | Beat-detection band-limit; `"modwt"` = sym4 wavelet (§4) |
| `MIN_BEATS_RELIABLE` | 3 | `PulseWaveformAnalyzer` | Beats required for `reliable` |
| `PERIODICITY_MIN` | 0.45 | `PulseWaveformAnalyzer` | Autocorrelation threshold for `reliable` (§10) |
| `emit_every` | 8 | `PulseWaveformStage` | `live_side` samples between `"pulse"` emissions (~5 Hz) |
| `fs_hint` | 40.0 | `PulseWaveformAnalyzer` | Sampling-rate fallback when the timestamp median is unusable |

---

## 18. Provenance and validation

- **Method sources:** two-Gaussian synthetic PPG (Nature Sci. Rep. 2020, doi:10.1038/s41598-020-69076-x); the sym4 à-trous cardiac-band sum and the beat QC rules are adapted from the DCS pulsatility literature.
- **Band-method equivalence:** movavg vs scipy Butterworth+`find_peaks` vs sym4 MODWT benchmark statistically indistinguishable for HR recovery across a noise / HR sweep — see `tests/test_pulse_analyzer.py`. This is why the engine is numpy-only.
- **Round-trip validation:** `tests/test_pipeline/test_demo_source.py` drives recorded BFI through the full pipeline and asserts the recomputed BFI and recovered heart rate match the recording; `tests/test_pipeline/test_pulse_integration.py` exercises the stage in a live-shaped pipeline.
- **Reliability gate:** validated on real recorded data (heart rate 82–86 bpm, periodicity ~0.87, `reliable=True`) and on static-phantom / lead-off channels (`reliable=False`).
