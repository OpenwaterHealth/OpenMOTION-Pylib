# Pulse-Waveform Analysis: Real-Time SDK Engine vs. Gen2 Research Pipeline

This note compares the **real-time pulse engine** shipped in this SDK (`omotion/pulse/`, documented in [`PulseWaveform.md`](PulseWaveform.md)) against the **offline research analysis** in the `opw_bloodflow_gen2_ai` repository — the pipeline that produced the clinical feature vectors, RedCap correlations, cross-site validation, and deep-learning models for the Gen2 study.

Both extract a cardiac pulse from an Open-Motion speckle signal and measure its morphology. They are built for **opposite operating points**, and almost every design choice follows from that. The Gen2 pipeline is the validated, feature-rich, batch analyzer; the SDK engine is a lean, streaming approximation for live operator feedback. They are complementary, not competing.

> **Provenance.** Gen2 references are file/line in `opw_bloodflow_gen2_ai` (chiefly `ReadGen2Data.py`, `Tools/ppg.py`, `runAsymmetryAnalysis.py`). SDK references are symbols in `omotion/pulse/` — see `PulseWaveform.md` for the full derivation.

---

## 1. The fundamental divergence: streaming vs. batch

| | **SDK real-time engine** | **Gen2 research pipeline** |
|---|---|---|
| Operating mode | **Streaming** — rolling 12 s window, incremental, updates ~5 Hz | **Batch** — whole recorded scan, processed once, post-hoc |
| Latency budget | Milliseconds; runs inside the live pipeline | Minutes; runs offline on saved data |
| Primary output | A live template + envelope + reliability flag for the operator | A ~16-feature vector per pulse for statistics / ML |
| Consumer | The bloodflow-app pulse viewer | RedCap correlation, cross-site RF, deep learning, asymmetry study |
| Failure mode it guards | *Showing a fake pulse on a phantom / lead-off channel* | *Feeding a noisy beat into a study feature vector* |
| Dependencies | **numpy only** | biosppy, scipy, scikit-learn, scikit-image, statsmodels, torch, ray, wandb |

Everything below is a consequence of this row.

---

## 2. Side-by-side summary

| Step | SDK engine (`omotion/pulse/`) | Gen2 pipeline (`ReadGen2Data.py`, `Tools/ppg.py`) |
|---|---|---|
| **Input signal** | **BFI** (blood-flow index) — rises during systole | **Contrast** (and corrected mean) — *drops* during systole; inverted (`1 − x`) before morphology |
| **Window** | Rolling 12 s (`raw_window_s`), trimmed continuously | Entire scan |
| **Period estimate** | Normalized **autocorrelation** peak in the plausible RR band (`_autocorr_period`) | **FFT** — largest spectral peak, per channel, median across channels (`DetectPulseAndFindFit`); or Butterworth-LP + `find_peaks` |
| **Band-limit** | Moving-average high-pass + 5-tap smoothing (`_band_limit`); optional sym4 à-trous MODWT | 2nd-order Butterworth low-pass at `median_bpm/60` (`FindLowNoisePulseAndAverage`); Elgendi squared-signal TERMA (`Tools/ppg.py`) |
| **Beat / onset detection** | **Peak-then-foot**: local maxima on the smoothed signal, feet = `argmin` of the *original* signal between peaks (`_segment_feet`) | **Elgendi 2013** systolic-peak detector via `biosppy.signals.ppg.ppg` (`FindGoldenPulseNew`), or paired-channel `find_peaks` on the LP signal |
| **Beat length** | Per-beat, RR-gated to `[60/max_bpm, 60/min_bpm]` | **Modal** onset-to-onset length (histogram argmax, `FindPulseLengthNew`), ±1.5-sample tolerance |
| **Beat normalization** | **Phase-resample** each beat to 60 bins on `[0,1)` (`_resample_beat`) — HR-invariant | Crop to modal **sample** length; drift-correct by subtracting each beat's mean (`GetPulsesNew`) |
| **Template ("golden pulse")** | Ensemble **mean** of the last 20 QC-passed phase-beats + min/max envelope + p25/p75 IQR | Mean of drift-corrected length-matched beats; `FindLowNoisePulseAndAverage` adds a spline fit + paired-channel selection |
| **Quality gate** | **Reliability gate**: ≥3 beats ∧ amp>0 ∧ **autocorr periodicity ≥ 0.45** (`_features`) | Per-segment **std** thresholds (median σ > 0.005 / 0.01), FFT period ∈ [min,max] bpm, beat count (`DetectPulseAndFindFit`) |
| **Feature count** | ~11, clinical-index-oriented | ~16, ML-feature-oriented |
| **L/R comparison** | Live per-feature **Δ (L−R)** + template shape **Pearson r** | Signed `R−L`, `|R−L|`, and **AsymIdx `(R−L)/(|R|+|L|)`** per feature × {H,V} (`runAsymmetryAnalysis.py`) |
| **Per-beat QC** | 4 morphology rules (`_beat_passes_qc`) | Modal-length filter + `std`/drift outlier rejection |

---

## 3. Signal and polarity

The most basic difference. Gen2 analyses **speckle contrast** `K`, which *decreases* as flow increases (faster decorrelation → lower contrast). Every Gen2 morphology routine therefore inverts the signal first — e.g. `ComputeVCI` does `goldenPulse = 1 − normalized(goldenPulse)`, and `GetAreaUnderCurve` reflects the distribution — so that "systole" is a peak.

The SDK engine analyses **BFI**, which the science pipeline has already computed as an inverse-of-contrast index (`SciencePipeline.md` §5.9). BFI *rises* during systole, so the SDK engine treats the systolic peak as a genuine maximum with no inversion (`PSF = max(template)`, `_detect_peaks` on maxima). Anyone porting a Gen2 feature onto the SDK path must drop the inversion, and vice-versa.

---

## 4. Period estimation — autocorrelation vs. FFT

**Gen2 (`DetectPulseAndFindFit`)** takes the FFT of the whole (DC-removed) contrast signal per channel, finds the largest spectral peak, converts bin → period, and takes the **median across the paired channels**, gating out anything outside `[minbpm, maxbpm]`. A global FFT is ideal offline: the whole scan is available, and averaging the spectrum over minutes gives a very stable dominant frequency.

**SDK (`_autocorr_period`)** computes the **normalized autocorrelation** of the band-limited window and takes the peak lag inside `[60/max_bpm, 60/min_bpm]`. Autocorrelation is the natural streaming choice — it works on a short rolling window, needs no FFT length padding, and, crucially, its **peak height (0–1) doubles as a periodicity strength** that the reliability gate keys on (§6). The FFT approach yields a frequency but not a comparably clean "how periodic is this really" scalar.

Both converge to the same heart rate on a clean signal; they differ in what else they produce and in how little data they need.

---

## 5. Beat / onset detection

**Gen2** uses the **Elgendi 2013** systolic-peak detector (`Tools/ppg.py::find_onsets_elgendi2013`, wrapped by BioSPPy's `ppg.ppg`): truncate-and-square the filtered signal, compute two boxcar moving averages (a short `MA_peak` over ~111 ms and a long `MA_beat` over ~667 ms), mark "waves" where `MA_peak > MA_beat + β·mean(sq)`, and take the most *prominent* local maximum in each wave with a minimum-delay guard. It also has a second path (`FindLowNoisePulseAndAverage`) that low-pass filters the paired channels and runs `scipy.find_peaks` with `distance = 0.85·period`, exploiting the fact that the left/right cameras of a pair are acquired simultaneously.

**SDK** uses **peak-then-foot** (`_detect_peaks` → `_segment_feet`): find systolic peaks as refractory-spaced local maxima on the *smoothed* signal, then place each beat onset at the true end-diastolic **foot** = `argmin` of the *original* signal between consecutive peaks. Detecting on the smoothed copy but cutting on the raw trough keeps windows aligned to real diastolic minima — the moving-average detrend shifts minima, so trough-detecting the detrended signal directly windows beats off-centre (a bug this design specifically fixes).

Trade-off: Elgendi/TERMA is a well-cited, tuned PPG detector but assumes a filtered, whole-signal context and pulls in BioSPPy+scipy; peak-then-foot is dependency-free, streaming-friendly, and returns feet (needed to window a beat) directly.

---

## 6. Reliability / validity — the biggest philosophical gap

This is where the two systems most reflect their purpose.

**Gen2** decides a channel is bad from **beat-shape dispersion**: in `DetectPulseAndFindFit`, if the median per-phase `std` of the stacked pulse segments exceeds `0.005` (falling back, then `0.01` to reject entirely) the golden pulse is discarded; a channel also fails if the FFT period lands outside the HR band or too few beats are found. This is a *consistency* test — appropriate offline, where you can afford to drop a channel and still have plenty of subjects.

**SDK** adds a gate that **consistency alone cannot pass**: the normalized-autocorrelation **periodicity** (`PERIODICITY_MIN = 0.45`). Band-limiting narrowband noise from a static phantom or an unplugged channel produces beats that are *self-consistent* (low std) and have a positive amplitude — they would sail through a std/dispersion test — but they are **not periodic in time**. Periodicity separates a real cardiac rhythm (~0.85) from consistent-looking noise (~0.15). This matters specifically because the SDK engine drives a **live clinical display**: showing an operator a confident-looking pulse number on a phantom is a safety problem, so `reliable` must be false there. Offline, a human reviews every channel, so the bar is different.

Put differently: Gen2 asks *"are these beats the same shape?"*; the SDK also asks *"is there actually a heartbeat here?"*.

---

## 7. Template ("golden pulse") construction

Both build an ensemble-average beat, but normalize differently:

- **Gen2** crops beats to a **fixed modal sample length** and **drift-corrects** each beat (subtracts its own mean) before averaging (`GetPulsesNew`), optionally spline-fitting and choosing the lower-noise of the paired channels (`FindLowNoisePulseAndAverage`). Fixed-length averaging is exact when HR is stable and preserves absolute timing in samples; drift removal is deliberately **disabled** for scan types where slow contrast change is the signal of interest (`scanType 2/4`).
- **SDK** **phase-resamples** every beat to 60 bins on `[0,1)` before averaging (`_resample_beat`), and keeps the beats in BFI units (no per-beat mean removal). Phase resampling makes the template robust to beat-to-beat RR variation in a live window (where HR drifts within the 12 s span) at the cost of absolute-timing resolution — recovered separately as `rise_time_ms = rise_frac · median(RR)`.

Gen2 additionally keeps a **vertical normalization** (`WaveformVertNorm`): flatten the trough envelope, divide by the peak envelope → `[0,1]`, for shape-only comparison. The SDK exposes the analogous idea implicitly through the phase axis + the min/max envelope, but does not ship a separate normalized waveform.

---

## 8. Feature sets

The overlap is real but partial. Mapping the two vocabularies:

| Concept | SDK feature | Gen2 feature | Notes |
|---|---|---|---|
| Heart rate | `hr_bpm` (60/median RR) | `hr` (from BioSPPy) | Same quantity |
| Pulse amplitude | `amp` = PSF−EDF | `amplitude` = max−min; `unbiasedAmp` (first-half) | SDK anchors on template ends; Gen2 on extrema |
| Area under curve | `auc` | `areaUnderCurve`, `areaUnderCurveP1` (systolic) | Gen2 splits systolic AUC out |
| Systolic timing | `rise_time_frac`, `rise_time_ms` | `pulseOnset`, `pulseOnsetProp` | Both measure upstroke duration |
| Mean flow | `mean_flow` | `average` (segment mean) | Same |
| **Pulsatility index** | **`pi` = AMP/MF** | — | Doppler index; SDK-only |
| **Resistivity index** | **`ri` = AMP/PSF** | — | Doppler index; SDK-only |
| Augmentation index | `aix` (best-effort) | — | Secondary-peak ratio; SDK-only |
| Beat regularity | `periodicity` (autocorr) | — (uses segment std) | Different mechanism |
| Beat repeatability | `consistency` (median Pearson r) | segment `std` | Same intent |
| **Velocity curve index** | — | **`veloCurveIndex`** (+ Hann, +norm) | Curvature `∫|y″|/(1+y′²)^{3/2}` over the systolic canopy; Gen2-only |
| **Canopy width** | — | **`pulseCanopy`** | Fraction of beat below the systolic quartile; Gen2-only |
| Distribution shape | — | `skewness`, `kurtosis`, `secondMoment`, `modulationDepth` | scipy moment features for ML; Gen2-only |
| Noise proxy | — | `noiseMetric` (slope-change count) | Gen2-only |

**Reading:** the SDK leans on the **clinical Doppler indices** (PI, RI, AIx) a physician reads directly, plus HR/AUC/rise-time. Gen2 leans on a **rich, ML-friendly feature vector** — curvature (VCI), statistical moments, canopy — designed to be fed to random forests and networks that pick their own discriminative combinations. Neither is a superset; the SDK could add VCI/skewness cheaply, and Gen2 could compute PI/RI trivially, but each chose the set that fits its consumer.

---

## 9. Left/right comparison

**Gen2** is built for a **stroke/asymmetry study**: for each feature it emits the signed difference `R − L`, the magnitude `|R − L|`, and the scale-invariant **asymmetry index `(R − L)/(|R| + |L|)`**, across both **H and V** orientations and for both the golden pulse and the per-segment medians (`runAsymmetryAnalysis.py`). These land in a per-subject matrix for RF cross-validation and cross-site comparison — asymmetry is the clinical endpoint.

**SDK** shows the operator a **live** side-by-side: a per-feature **Δ (L−R)** column and a single template **shape-similarity `r = pearson(left.template, right.template)`** (mean/scale-invariant, so it isolates *shape* asymmetry independent of amplitude). It is a real-time glance, not a study statistic — but `(R−L)` maps directly onto Gen2's signed difference, so a live reading and an offline analysis of the same scan tell the same directional story.

---

## 10. Dependency & footprint contrast

Gen2 pulls in `biosppy` (Elgendi/PPG), `scipy` (Butterworth, `find_peaks`, `moment`, `describe`, splines), `scikit-learn`/`scikit-image`, `statsmodels`, `torch`+`torchvision` (deep learning), `ray` (distributed sweeps), and `wandb` (experiment tracking) — appropriate for a research bench.

The SDK engine is **numpy-only by deliberate constraint** (`PulseWaveform.md` §4.3): three band-limits — moving-average, a scipy Butterworth+`find_peaks`, and the sym4 MODWT — were benchmarked and found *statistically indistinguishable* for HR recovery on 40 fps data, so the simplest was kept and scipy/biosppy stayed out of the runtime. That keeps the engine embeddable in the live pipeline and identical across the live path, the demo, and the tests.

---

## 11. How they relate

- **The Gen2 pipeline is the reference; the SDK engine is the real-time approximation.** Where features overlap (HR, AUC, amplitude, onset/rise time), they should track in trend on the same scan; the SDK trades absolute-timing precision (phase resampling) and feature breadth for latency, streaming, and a hard reliability gate.
- **They are complementary in the product.** The SDK engine gives the operator immediate feedback and a "is there a pulse?" verdict during acquisition; the Gen2 pipeline (or its successor) does the definitive offline feature extraction and study statistics on the saved scan afterward.
- **Cross-validation path.** A recorded scan replayed through the SDK's `DemoScanSource` (`PulseWaveform.md` §15) produces the SDK's template/features; the same recording processed by Gen2 produces the research vector. Comparing HR, AUC, amplitude, and the L−R direction on shared scans is the natural way to confirm the streaming engine agrees with the validated pipeline.

---

## 12. When to use which

| You want… | Use |
|---|---|
| A live pulse + envelope + "real pulse?" flag during a scan | **SDK engine** (this repo) |
| PI / RI / AIx clinical indices in real time | **SDK engine** |
| VCI, canopy, skewness/kurtosis, moment features | **Gen2 pipeline** |
| A per-subject asymmetry matrix for a study / ML model | **Gen2 pipeline** |
| Dependency-free, embeddable, identical live/offline analysis | **SDK engine** |
| The validated feature vector behind the Gen2 clinical results | **Gen2 pipeline** |

---

## 13. References

- **SDK engine:** [`PulseWaveform.md`](PulseWaveform.md); `omotion/pulse/analyzer.py`, `synth.py`, `scan_synth.py`; `omotion/pipeline/stages/pulse_waveform.py`.
- **Gen2 pipeline:** `opw_bloodflow_gen2_ai/ReadGen2Data.py` (`DetectPulseAndFindFit`, `FindGoldenPulseNew`, `GetPulsesNew`, `ComputeVCI`, `ComputeWaveformAttributes`, `GetWaveformAttributesForSingleChannelPulse`), `Tools/ppg.py` (`find_onsets_elgendi2013`), `runAsymmetryAnalysis.py`.
- **Method sources:** Elgendi M. et al. (2013), *Systolic Peak Detection in Acceleration Photoplethysmograms*, PLoS ONE 8(10):e76585 (Gen2 onsets); two-Gaussian synthetic PPG, Nature Sci. Rep. 2020 doi:10.1038/s41598-020-69076-x (SDK synth/ground truth); DCS pulsatility literature (SDK sym4 band + beat QC).
