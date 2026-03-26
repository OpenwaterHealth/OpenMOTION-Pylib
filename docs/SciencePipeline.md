# OpenMotion Science Pipeline — Technical Reference

**Audience:** Scientists and engineers who need a complete, unambiguous description of every data transformation that occurs between the raw FPGA histogram output and the BFI/BVI values that appear in the corrected CSV and live display.

**Implementation:** `omotion/MotionProcessing.py` — class `SciencePipeline` and supporting functions.

---

## 1. Summary

Each camera produces a 1024-bin histogram at 40 Hz.  The science pipeline:

1. **Discards** the first 9 frames from every camera (hardware warmup).
2. **Identifies dark frames** by a fixed schedule based on firmware timing.  Dark frames are acquired with the laser off and provide a measurement of the ambient + dark-current floor.
3. **Emits an uncorrected sample** for every non-dark frame immediately, using the raw histogram statistics with no dark subtraction.  For dark frames, it re-emits the previous non-dark frame's values so the live display shows no artefact.
4. **Buffers** the raw first and second moments of every non-dark frame.
5. **When a second consecutive dark frame arrives**, linearly interpolates the dark baseline across the buffered interval, subtracts it frame-by-frame, recomputes corrected contrast and intensity, applies the per-camera BFI/BVI calibration, and emits a `CorrectedBatch`.  The leading dark frame of the interval also receives an interpolated corrected value derived from its two adjacent non-dark neighbors.

Consumers therefore receive:

| Stream | Type | Rate | `is_corrected` | Destination |
|---|---|---|---|---|
| Uncorrected | `CorrectedSample` per camera | ~40 Hz (every non-dark + every dark) | `False` | Live plot |
| Corrected batch | `CorrectedBatch` per camera | ~1 per 15 s (configurable) | `True` | Corrected CSV + plot snap |

---

## 2. Hardware context

- **Cameras:** Up to 8 OV2312 cameras per sensor module, up to 2 sensor modules (left + right), for a maximum of 16 cameras total.
- **Frame rate:** 40 Hz (camera frame sync controlled by the console MCU).
- **Histogram:** 1024 bins, each bin is a 32-bit count value.  A counts-per-electron-volt conversion factor is available but not applied within the pipeline.  The total count per frame is fixed at **2,457,606** (validated by `EXPECTED_HISTOGRAM_SUM`): the OV2312 sensor is 1920 × 1280 = 2,457,600 pixels, plus a sentinel value of 6 added by the firmware.
- **Dark frame protocol:** The firmware periodically cuts laser illumination for one frame cycle so the camera captures only ambient light and dark current.  The pipeline never has to infer whether a frame is dark from the data — it follows a deterministic schedule (§4).
- **Frame ID:** The firmware embeds an 8-bit rolling counter (0–255) in the last word of each histogram.  The pipeline unwraps this to a monotonic **absolute frame index** (§3).

---

## 3. Frame ID unwrapping

**Class:** `FrameIdUnwrapper` — one instance per `(side, cam_id)` pair.

The firmware's 8-bit counter wraps from 255 → 0.  The unwrapper maintains an epoch counter that increments on each detected rollover:

```python
delta = (raw_frame_id - last_raw) & 0xFF   # unsigned 8-bit forward distance

if delta <= 128 and raw_frame_id < last_raw:
    epoch += 1  # genuine rollover crossing

absolute_frame_id = epoch * 256 + raw_frame_id
```

A `delta > 128` (apparent large backward jump) is treated as a packet anomaly and the epoch is left unchanged.  The `absolute_frame_id` is therefore strictly monotonic for normal packet delivery and weakly monotonic under anomalies.

All downstream logic uses `absolute_frame_id`.  The raw `frame_id` is retained only for CSV column compatibility.

---

## 4. Frame classification

### 4.1 Warmup discard

Frames with `absolute_frame_id` ∈ {1, 2, …, `discard_count`} (default `discard_count = 9`) are silently dropped.  These frames correspond to the first 9 camera trigger cycles after the pipeline starts, during which the sensor's internal state (AGC, PLL, etc.) has not yet settled.

No callback is fired and no data is stored for these frames.

### 4.2 Dark frame schedule

A frame at absolute index *n* is classified as a **dark frame** if and only if:

```
n == discard_count + 1                         # first dark (frame 10 by default)
OR
n > discard_count + 1  AND  (n − 1) mod dark_interval == 0
```

where `dark_interval` defaults to 600 (= 15 seconds at 40 Hz).  Under the defaults the dark frames occur at:

> n = 10, 601, 1201, 1801, …

Every other frame with `n > discard_count` is a **bright (non-dark) frame**.

Note that frame n = 1 satisfies the formula `(n−1) mod 600 == 0` but is already discarded as a warmup frame, so it is never observed by the pipeline.

---

## 5. Moment computation

For every frame that passes the discard and dark-schedule checks, the pipeline computes the first two moments of the histogram.  Let **h** = (h₀, h₁, …, h₁₀₂₃) be the bin values (in counts) and *N* = Σ_k h_k the total count.

```
μ₁ = (1/N) Σ_{k=0}^{1023} k · h_k          # first moment (mean bin index)
μ₂ = (1/N) Σ_{k=0}^{1023} k² · h_k         # second moment
σ² = μ₂ − μ₁²                               # variance  (always ≥ 0 by construction)
σ  = √σ²
K  = σ / μ₁                                 # speckle contrast
```

In NumPy:

```python
HISTO_BINS    = np.arange(1024, dtype=np.float64)
HISTO_BINS_SQ = HISTO_BINS ** 2

μ₁ = np.dot(hist, HISTO_BINS)    / row_sum
μ₂ = np.dot(hist, HISTO_BINS_SQ) / row_sum
σ² = max(0.0, μ₂ - μ₁**2)
```

These are computed identically for both dark and bright frames.

---

## 6. Uncorrected stream

### 6.1 Bright frames

For each bright frame *n*, the pipeline immediately constructs and emits a `CorrectedSample` with `is_corrected = False`:

| Field | Value |
|---|---|
| `mean` | μ₁(n) |
| `std_dev` | σ(n) |
| `contrast` | K(n) = σ(n) / μ₁(n) |
| `bfi_corrected` | BFI(K(n), μ₁(n)) — see §8 |
| `bvi_corrected` | BVI(K(n), μ₁(n)) — see §8 |
| `is_corrected` | `False` |

This fires the `on_uncorrected_fn` callback immediately, before any dark-frame correction is available.

The moments μ₁(n) and μ₂(n) are also stored in `_pending_moments[key]` as a `_StoredFrameMoments` object for later dark-frame correction.

### 6.2 Dark frames — live display masking

**Rule:** When a dark frame *D* arrives, the `on_uncorrected_fn` callback receives a `CorrectedSample` whose metric values (`mean`, `std_dev`, `contrast`, `bfi_corrected`, `bvi_corrected`) are copied from the immediately preceding bright frame's `CorrectedSample`.  Only `frame_id`, `absolute_frame_id`, and `timestamp_s` are updated to reflect the dark frame's true position.

```python
# Pseudo-code
dark_uncorrected = copy(last_uncorrected[key])
dark_uncorrected.frame_id          = D_raw
dark_uncorrected.absolute_frame_id = D
dark_uncorrected.timestamp_s       = ts
dark_uncorrected.is_corrected      = False
emit on_uncorrected_fn(dark_uncorrected)
```

**Rationale:** A dark frame has anomalously low intensity and contrast because the laser is off.  Emitting the raw dark values would cause the live trace to drop sharply and then recover, appearing as a ~25 ms artefact every 15 seconds.  Repeating the previous value makes the dark frame invisible to the live display.

If no preceding bright frame exists (i.e. the very first frame in the scan is a dark frame), no uncorrected sample is emitted for that dark frame.

---

## 7. Corrected batch computation

The corrected batch is computed and emitted by `_emit_corrected_for_camera(key)`, which is called each time a second (or later) consecutive dark frame arrives for a given `(side, cam_id)` pair.

Let the two bounding dark frames be at absolute positions **D_prev** and **D_curr**, with:

- μ₁(D_prev), σ²(D_prev) — moments of the earlier dark frame
- μ₁(D_curr), σ²(D_curr) — moments of the later dark frame
- Δ = D_curr − D_prev — interval width in frames

### 7.1 Baseline interpolation

For each bright frame *n* ∈ (D_prev, D_curr) (i.e. strictly between the two dark frames), the dark baseline is linearly interpolated:

```
t(n) = (n − D_prev) / Δ            ∈ (0, 1)

μ̄₁(n) = μ₁(D_prev) + t(n) · [μ₁(D_curr) − μ₁(D_prev)]
σ̄²(n) = σ²(D_prev) + t(n) · [σ²(D_curr) − σ²(D_prev)]
```

`t(n)` is 0 at the first dark and 1 at the second dark, so the interpolation assigns more dark-frame weight to frames closer in time to that dark measurement.

### 7.2 Dark-subtracted moments

```
μ̃₁(n) = μ₁(n) − μ̄₁(n)                   # corrected mean

raw_σ²(n) = μ₂(n) − μ₁(n)²               # raw variance (from stored moments)
σ̃²(n)  = max(0, raw_σ²(n) − σ̄²(n))       # corrected variance, clamped ≥ 0
σ̃(n)   = √σ̃²(n)

K̃(n) = σ̃(n) / μ̃₁(n)    if μ̃₁(n) > 0
      = 0.0               otherwise
```

Clamping `σ̃²` to zero prevents imaginary standard deviations when shot-noise fluctuations cause the measured variance to fall below the interpolated dark variance.

### 7.3 Corrected BFI/BVI

BFI and BVI are computed from K̃(n) and μ̃₁(n) via the calibration mapping (§8), yielding `bfi_corrected` and `bvi_corrected` for each frame.  These samples are assembled into a `CorrectedBatch` with `is_corrected = True`.

### 7.4 Corrected value for the dark frame itself

The dark frame D_prev is included in the corrected batch.  Its corrected BFI/BVI are not computed by baseline subtraction (its histogram *is* the baseline); instead they are linearly interpolated between the last corrected sample of the *previous* batch (absolute frame D_prev − 1) and the first corrected sample of the *current* batch (absolute frame D_prev + 1):

```
bfi_corr(D_prev)     = [ bfi_corr(D_prev − 1) + bfi_corr(D_prev + 1) ] / 2
bvi_corr(D_prev)     = [ bvi_corr(D_prev − 1) + bvi_corr(D_prev + 1) ] / 2
mean_corr(D_prev)    = [ mean_corr(D_prev − 1) + mean_corr(D_prev + 1) ] / 2
std_corr(D_prev)     = [ std_corr(D_prev − 1)  + std_corr(D_prev + 1)  ] / 2
contrast_corr(D_prev)= [ K̃(D_prev − 1) + K̃(D_prev + 1) ] / 2
```

**Edge case:** If no previous corrected batch exists (this is the first dark interval), the right neighbor value is used directly (no averaging).

The current dark frame D_curr is **not** included in this batch.  It becomes D_prev for the next batch and will receive its interpolated corrected value at that time.

**Rationale:** The dark frame's laser-off histogram is a valid measurement of the background floor, not of blood flow.  Interpolating between neighbors removes the dark-frame artefact from the corrected time series.

### 7.5 Batch ordering and content

The emitted `CorrectedBatch` contains samples in ascending `absolute_frame_id` order:

```
[D_prev, D_prev+1, D_prev+2, ..., D_curr-1]
```

All samples have `is_corrected = True`.  The batch is emitted via `on_corrected_batch_fn`.  Internally, `_pending_moments[key]` is then truncated to discard all stored moments up to and including D_curr − 1; any moments at or beyond D_curr are retained for the next interval (should not occur in normal operation).

---

## 8. BFI/BVI calibration mapping

Both the uncorrected and corrected paths share the same linear calibration mapping from (contrast, mean) to (BFI, BVI).  The calibration constants are stored in four NumPy arrays of shape **(2, 8)** — axis 0 is the module index (0 = left sensor, 1 = right sensor), axis 1 is the camera position (0–7).

```python
module_idx = 0 if side == "left" else 1
cam_pos    = cam_id % 8

BFI = (1.0 - (K  − C_min[module_idx, cam_pos]) /
              (C_max[module_idx, cam_pos] − C_min[module_idx, cam_pos])) × 10

BVI = (1.0 - (μ₁ − I_min[module_idx, cam_pos]) /
              (I_max[module_idx, cam_pos] − I_min[module_idx, cam_pos])) × 10
```

where:
- `C_min`, `C_max` — minimum and maximum speckle contrast values over the calibration population (`bfi_c_min`, `bfi_c_max` constructor arguments)
- `I_min`, `I_max` — minimum and maximum mean bin-index values (`bfi_i_min`, `bfi_i_max`)

For the uncorrected stream, K and μ₁ are the raw (not dark-subtracted) values.  For the corrected batch, K̃ and μ̃₁ are used.

**Fallback:** If the module or camera index is out of bounds for the calibration arrays, BFI = K × 10 and BVI = μ₁ × 10 (identity scaling, no clipping).

---

## 9. Science frame alignment

Independent of the correction machinery, the pipeline also aligns samples from all active cameras into **`ScienceFrame`** objects.  A `_FrameBuffer` accumulates `CorrectedSample` objects (uncorrected, `is_corrected = False`) keyed by `(side, cam_id)`.  When the buffer for a given `absolute_frame_id` has received contributions from all expected cameras (as determined by `left_camera_mask` and `right_camera_mask`), a `ScienceFrame` is assembled and `on_science_frame_fn` is fired.

Dark frames participate in this alignment using the repeated-previous-value uncorrected sample (§6.2), so the aligned frame stream is continuous even across dark frames.

Partial frames (missing one or more cameras) are flushed after `frame_timeout_s` (default 0.5 s) to prevent stalls when a sensor drops a packet.

---

## 10. Input validation and guard rails

### Histogram sum check

Before any frame enters the pipeline, the total histogram bin sum is compared to `EXPECTED_HISTOGRAM_SUM = 2,457,606` (= 1920 × 1280 pixels + 6 sentinel counts).  Any frame whose sum does not match is silently dropped with a `WARNING`-level log message.  This rejects corrupt packets before they can contaminate science results.

This check occurs in two places:
1. In `parse_histogram_packet_structured()` during raw binary parsing.
2. Again in `_science_worker` for samples enqueued directly (e.g. in tests).

### First-frame staleness check

For each `(side, cam_id)` pair, the very first frame received after pipeline start must have `raw_frame_id == 1`.  Any other value indicates a stale packet from a previous scan that was delivered after the pipeline restarted.  Such frames are dropped and a `WARNING` is logged.

---

## 11. Threading model

The pipeline runs a single background daemon thread (`SciencePipeline` thread).  All histogram samples are ingested via a `queue.Queue` (`_ingress_queue`).  The worker thread consumes from this queue and is the sole writer of all internal state (`_unwrappers`, `_dark_history`, `_pending_moments`, `_last_uncorrected`, `_last_corrected`, `_frame_buffers`).  No locks are needed for pipeline-internal state.

Callbacks (`on_uncorrected_fn`, `on_corrected_batch_fn`, `on_science_frame_fn`) are invoked on the science thread.  Implementations that touch UI state must marshal to the UI thread (e.g. via a Qt signal).

---

## 12. Complete data flow diagram

```
Sensor firmware
  │  8-bit rolling frame_id + 1024-bin histogram (4096 bytes)
  │  at 40 Hz per camera
  ▼
parse_histogram_packet_structured()
  │  → HistogramSample (cam_id, frame_id, histogram, row_sum, timestamp_s, temperature_c)
  │  Drop if row_sum ≠ 2,457,606  (1920×1280 px + 6 sentinel)
  ▼
SciencePipeline._ingress_queue
  │  (side, cam_id, frame_id, timestamp_s, hist, row_sum, temperature_c)
  ▼
_science_worker (single background thread)
  │
  ├─ [row_sum check] → drop if mismatch
  ├─ [first-frame check] → drop if first raw_frame_id ≠ 1
  ├─ FrameIdUnwrapper.unwrap() → absolute_frame_id
  ├─ [discard check] → drop if absolute_frame_id ≤ 9
  │
  ├─ [dark frame?] ──── YES ──────────────────────────────────────────┐
  │                                                                    │
  │  Compute μ₁, σ² from histogram                                    │
  │  Append (abs_frame, raw_fid, ts, μ₁, σ²) to _dark_history[key]  │
  │                                                                    │
  │  If ≥2 dark frames in history:                                    │
  │    _emit_corrected_for_camera(key)  ──► CorrectedBatch            │
  │       dark-subtracted BFI/BVI for interval (D_prev, D_curr)       │
  │       + interpolated corrected value for D_prev itself            │
  │       → on_corrected_batch_fn()                                   │
  │       → corrected CSV                                             │
  │                                                                    │
  │  Emit uncorrected sample with values from _last_uncorrected[key]  │
  │    (frame IDs + timestamp updated to D; BFI/BVI held constant)    │
  │    → on_uncorrected_fn()                                          │
  │    → _FrameBuffer[abs_frame]  → ScienceFrame alignment            │
  │                                                                    │
  │  continue ◄──────────────────────────────────────────────────────┘
  │
  └─ [bright frame] ──────────────────────────────────────────────────┐
                                                                       │
     Compute μ₁, σ² from histogram                                    │
     Store _StoredFrameMoments(μ₁, μ₂) → _pending_moments[key]       │
                                                                       │
     compute_realtime_metrics() → RealtimeSample                      │
       (raw K, μ₁, σ → raw BFI/BVI via calibration)                  │
                                                                       │
     Emit CorrectedSample(is_corrected=False)                         │
       → on_uncorrected_fn()                                          │
       → _last_uncorrected[key]                                       │
       → _FrameBuffer[abs_frame]  → ScienceFrame alignment            │
                                                              ◄────────┘
```

---

## 13. Key constants and defaults

| Parameter | Default | Meaning |
|---|---|---|
| `discard_count` | 9 | Warmup frames dropped at start |
| `dark_interval` | 600 | Frames between dark acquisitions (15 s at 40 Hz) |
| `frame_timeout_s` | 0.5 s | Stale partial frame flush threshold |
| `EXPECTED_HISTOGRAM_SUM` | 2,457,606 | Required total count per valid frame (1920 × 1280 px + 6 sentinel) |
| `FRAME_ID_MODULUS` | 256 | Firmware 8-bit counter rollover period |
| `FRAME_ROLLOVER_THRESHOLD` | 128 | Max forward delta before rollover is detected |
| Histogram bins | 1024 | Bin indices k ∈ {0, 1, …, 1023} |
| Cameras per sensor | 8 | OV2312 cameras per sensor module |
| Max sensors | 2 | Left and right modules |

---

## 14. Output data types

```python
@dataclass
class CorrectedSample:
    side: str               # "left" or "right"
    cam_id: int             # 0–7
    frame_id: int           # raw 8-bit firmware counter value
    absolute_frame_id: int  # monotonic unwrapped counter
    timestamp_s: float      # sensor-reported timestamp
    mean: float             # μ₁ or μ̃₁ (corrected) in bin-index units
    std_dev: float          # σ  or σ̃
    contrast: float         # K  or K̃
    bfi_corrected: float    # BFI on [0, 10] scale
    bvi_corrected: float    # BVI on [0, 10] scale
    is_corrected: bool      # False = uncorrected stream; True = corrected batch

@dataclass
class CorrectedBatch:
    dark_frame_start: int        # absolute_frame_id of D_prev
    dark_frame_end: int          # absolute_frame_id of D_curr
    samples: list[CorrectedSample]  # chronological, is_corrected=True
                                    # includes D_prev, excludes D_curr

@dataclass
class ScienceFrame:
    absolute_frame: int
    frame_id: int
    timestamp_s: float
    samples: dict[tuple[str, int], CorrectedSample]  # (side, cam_id) → sample
                                                      # uncorrected, is_corrected=False
```
