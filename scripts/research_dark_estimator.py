"""Research: causal realtime dark-baseline estimators vs offline ground truth.

Replays recorded raw CSVs through the real pipeline front-end
(classify -> timestamp repair -> noise floor -> moments) to obtain the
exact (u1, std) per frame that DarkCorrectionStage sees, then evaluates
candidate causal estimators of the dark baseline against the non-causal
batch interpolation (the system's own "truth") and a smoothed reference.

See docs/labnotes/2026-06-11-realtime-dark-estimator-study.md.

Usage:
    python scripts/research_dark_estimator.py extract LEFT.csv RIGHT.csv OUT.npz
    python scripts/research_dark_estimator.py analyze OUT.npz
"""

from __future__ import annotations

import sys

import numpy as np

from omotion.pipeline.sources import CsvReplaySource
from omotion.pipeline.sinks import ScanMetadata
from omotion.pipeline.stages.classify import FrameClassificationStage
from omotion.pipeline.stages.timestamp_repair import TimestampRepairStage
from omotion.pipeline.stages.noise_floor import NoiseFloorStage
from omotion.pipeline.stages.moments import MomentsStage

FPS = 40.0
DARK_INTERVAL_S = 15.0


# ---------------------------------------------------------------------------
# Phase A — extraction
# ---------------------------------------------------------------------------

def extract(left_csv: str, right_csv: str, out_npz: str) -> None:
    meta = ScanMetadata(
        scan_id="research", subject_id="x", operator="x",
        started_at_iso="2026-06-11T00:00:00Z", duration_sec=0,
        left_camera_mask=0xFF, right_camera_mask=0xFF, reduced_mode=False,
    )
    stages = [
        FrameClassificationStage(),       # default discard_count=9, dark_interval=600
        TimestampRepairStage(),
        NoiseFloorStage(threshold=10),
        MomentsStage(),
    ]
    src = CsvReplaySource(
        raw_csv_left=left_csv or None, raw_csv_right=right_csv or None,
        batch_size_frames=200, metadata=meta,
    )
    side, cam, absid, t, u1, std = [], [], [], [], [], []
    ftype = []
    for batch in src:
        for s in stages:
            batch = s.process(batch)
        n = batch.frame_ids.shape[0]
        for i in range(n):
            ft = str(batch.frame_type[i])
            if ft not in ("light", "dark"):
                continue
            si = int(batch.side_ids[i])
            ci = int(batch.cam_ids[i])
            side.append(si)
            cam.append(ci)
            absid.append(int(batch.abs_frame_ids[i]))
            t.append(float(batch.timestamp_s[i]))
            ftype.append(1 if ft == "dark" else 0)
            u1.append(float(batch.mean_raw[i, si, ci]))
            std.append(float(batch.std_raw[i, si, ci]))
    np.savez_compressed(
        out_npz,
        side=np.array(side, np.int8), cam=np.array(cam, np.int8),
        absid=np.array(absid, np.int64), t=np.array(t, np.float64),
        is_dark=np.array(ftype, np.int8),
        u1=np.array(u1, np.float64), std=np.array(std, np.float64),
    )
    print(f"saved {len(t)} rows -> {out_npz}")


# ---------------------------------------------------------------------------
# Phase B — estimators (causal: use only darks strictly before target time)
# ---------------------------------------------------------------------------
# Each estimator takes the dark series (td, yd) and target times tq (all
# sorted), and returns predictions at tq. yd is u1 or var depending on call.

def _causal_indices(td, tq):
    """For each tq, index of last dark with td <= tq (-1 = none)."""
    return np.searchsorted(td, tq, side="right") - 1


def est_zoh(td, yd, tq):
    k = _causal_indices(td, tq)
    out = np.full(tq.shape, np.nan)
    m = k >= 0
    out[m] = yd[k[m]]
    return out


def est_avgN(N):
    def f(td, yd, tq):
        k = _causal_indices(td, tq)
        out = np.full(tq.shape, np.nan)
        for i, ki in enumerate(k):
            if ki < 0:
                continue
            lo = max(0, ki - N + 1)
            out[i] = yd[lo:ki + 1].mean()
        return out
    return f


def est_lin2(td, yd, tq):
    """Linear extrapolation through last 2 darks (the hybrid's std rule)."""
    k = _causal_indices(td, tq)
    out = np.full(tq.shape, np.nan)
    for i, ki in enumerate(k):
        if ki < 0:
            continue
        if ki == 0 or td[ki] == td[ki - 1]:
            out[i] = yd[ki]
        else:
            slope = (yd[ki] - yd[ki - 1]) / (td[ki] - td[ki - 1])
            out[i] = yd[ki] + slope * (tq[i] - td[ki])
    return out


def est_linK(K):
    """Least-squares line through last K darks, extrapolated to tq."""
    def f(td, yd, tq):
        k = _causal_indices(td, tq)
        out = np.full(tq.shape, np.nan)
        for i, ki in enumerate(k):
            if ki < 0:
                continue
            lo = max(0, ki - K + 1)
            x, y = td[lo:ki + 1], yd[lo:ki + 1]
            if len(x) < 2 or x[-1] == x[0]:
                out[i] = y[-1]
            else:
                b, a = np.polyfit(x - x[-1], y, 1)
                out[i] = a + b * (tq[i] - x[-1])
        return out
    return f


def est_ema(alpha):
    def f(td, yd, tq):
        e = np.empty_like(yd)
        e[0] = yd[0]
        for j in range(1, len(yd)):
            e[j] = alpha * yd[j] + (1 - alpha) * e[j - 1]
        k = _causal_indices(td, tq)
        out = np.full(tq.shape, np.nan)
        m = k >= 0
        out[m] = e[k[m]]
        return out
    return f


def est_llt(q_level, q_trend, r):
    """Local-level + trend Kalman filter over dark observations, predicted
    forward to tq. State [level, slope]; process noise scales with dt."""
    def f(td, yd, tq):
        n = len(yd)
        levels = np.empty(n)
        slopes = np.empty(n)
        x = np.array([yd[0], 0.0])
        P = np.array([[r, 0.0], [0.0, 1e-4]])
        levels[0], slopes[0] = x
        for j in range(1, n):
            dt = max(td[j] - td[j - 1], 1e-6)
            F = np.array([[1.0, dt], [0.0, 1.0]])
            Q = np.array([[q_level * dt, 0.0], [0.0, q_trend * dt]])
            x = F @ x
            P = F @ P @ F.T + Q
            S = P[0, 0] + r
            Kg = P[:, 0] / S
            x = x + Kg * (yd[j] - x[0])
            P = P - np.outer(Kg, P[0, :])
            levels[j], slopes[j] = x
        k = _causal_indices(td, tq)
        out = np.full(tq.shape, np.nan)
        for i, ki in enumerate(k):
            if ki < 0:
                continue
            out[i] = levels[ki] + slopes[ki] * (tq[i] - td[ki])
        return out
    return f


ESTIMATORS = {
    "ZOH":   est_zoh,
    "AVG2":  est_avgN(2),
    "AVG3":  est_avgN(3),   # hybrid's u1 rule
    "AVG4":  est_avgN(4),
    "LIN2":  est_lin2,      # hybrid's std rule
    "LINK3": est_linK(3),
    "LINK4": est_linK(4),
    "EMA.3": est_ema(0.3),
    "EMA.5": est_ema(0.5),
    "LLT":   est_llt(q_level=0.05, q_trend=1e-4, r=1.0),
}


# ---------------------------------------------------------------------------
# Ground truth
# ---------------------------------------------------------------------------

def gt_interp(td, yd, tq):
    """Non-causal linear interpolation between bounding darks (batch path)."""
    return np.interp(tq, td, yd)


def gt_smooth(td, yd, tq, window=5):
    """Savitzky-Golay (quadratic) smoothed darks, then interpolated."""
    from scipy.signal import savgol_filter
    if len(yd) < window:
        return gt_interp(td, yd, tq)
    ys = savgol_filter(yd, window_length=window, polyorder=2)
    return np.interp(tq, td, ys)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def band_power_ratio(t, e, f0=1.0 / DARK_INTERVAL_S, rel_bw=0.3):
    """Power in [f0*(1-bw), f0*(1+bw)] relative to broadband (0.01..1 Hz),
    of the (detrended) error series e sampled at times t (~uniform 40 Hz)."""
    from scipy.signal import welch
    m = np.isfinite(e)
    if m.sum() < 256:
        return np.nan
    tt, ee = t[m], e[m]
    fs = 1.0 / np.median(np.diff(tt))
    ee = ee - ee.mean()
    f, p = welch(ee, fs=fs, nperseg=min(4096, len(ee)))
    band = (f >= f0 * (1 - rel_bw)) & (f <= f0 * (1 + rel_bw))
    broad = (f >= 0.01) & (f <= 1.0)
    if not band.any() or not broad.any():
        return np.nan
    return float(p[band].mean() / p[broad].mean())


def analyze(npz_path: str) -> None:
    d = np.load(npz_path)
    side, cam = d["side"], d["cam"]
    t, is_dark = d["t"], d["is_dark"].astype(bool)
    u1, std = d["u1"], d["std"]
    var = std ** 2

    keys = sorted({(int(s), int(c)) for s, c in zip(side, cam)})
    print(f"{npz_path}: {len(t)} rows, cams: {keys}")

    # Accumulators: per estimator, list over cams of metric values
    acc = {name: {"u1_rmse_i": [], "u1_rmse_s": [], "var_rmse_i": [],
                  "K_rmse": [], "K_band": []} for name in ESTIMATORS}
    gt_acc = {"dark_drift": [], "dark_noise": [], "mean_dc_med": [],
              "var_drift": [], "var_noise": [], "var_dc_med": [],
              "K_gtsmooth_band": [], "K_med": []}

    for (s, c) in keys:
        m = (side == s) & (cam == c)
        tm, dk = t[m], is_dark[m]
        u1m, varm = u1[m], var[m]
        order = np.argsort(tm)
        tm, dk, u1m, varm = tm[order], dk[order], u1m[order], varm[order]

        td, u1d, vard = tm[dk], u1m[dk], varm[dk]
        tl, u1l, varl = tm[~dk], u1m[~dk], varm[~dk]
        if len(td) < 5 or len(tl) < 500:
            print(f"  side{s} cam{c}: too few darks/lights ({len(td)}/{len(tl)}) — skipped")
            continue

        # --- RQ1 characterization ---
        # noise vs drift: second differences isolate white observation noise
        d2 = np.diff(u1d, 2)
        noise = np.sqrt(np.maximum(np.var(d2) / 6.0, 0))   # per-observation sigma
        drift = np.sqrt(np.maximum(np.var(np.diff(u1d)) / 2.0 - noise ** 2, 0))
        d2v = np.diff(vard, 2)
        vnoise = np.sqrt(np.maximum(np.var(d2v) / 6.0, 0))
        vdrift = np.sqrt(np.maximum(np.var(np.diff(vard)) / 2.0 - vnoise ** 2, 0))

        g_u1_i = gt_interp(td, u1d, tl)
        g_u1_s = gt_smooth(td, u1d, tl)
        g_var_i = np.maximum(gt_interp(td, vard, tl), 0)
        g_var_s = np.maximum(gt_smooth(td, vard, tl), 0)

        mean_dc_gt = u1l - g_u1_i
        var_dc_gt = np.maximum(varl - g_var_i, 0)
        K_gt = np.sqrt(var_dc_gt) / np.where(mean_dc_gt > 0, mean_dc_gt, np.nan)

        gt_acc["dark_drift"].append(drift)
        gt_acc["dark_noise"].append(noise)
        gt_acc["var_drift"].append(vdrift)
        gt_acc["var_noise"].append(vnoise)
        gt_acc["mean_dc_med"].append(np.nanmedian(mean_dc_gt))
        gt_acc["var_dc_med"].append(np.nanmedian(var_dc_gt))
        gt_acc["K_med"].append(np.nanmedian(K_gt))
        # how periodic is even the smooth-vs-interp disagreement?
        K_s = np.sqrt(np.maximum(varl - g_var_s, 0)) / np.where(
            (u1l - g_u1_s) > 0, u1l - g_u1_s, np.nan)
        gt_acc["K_gtsmooth_band"].append(band_power_ratio(tl, K_s - K_gt))

        for name, est in ESTIMATORS.items():
            p_u1 = est(td, u1d, tl)
            p_var = np.maximum(est(td, vard, tl), 0)
            e_u1_i = p_u1 - g_u1_i
            e_u1_s = p_u1 - g_u1_s
            e_var = p_var - g_var_i
            mean_dc = u1l - p_u1
            var_dc = np.maximum(varl - p_var, 0)
            K = np.sqrt(var_dc) / np.where(mean_dc > 0, mean_dc, np.nan)
            eK = K - K_gt
            acc[name]["u1_rmse_i"].append(np.sqrt(np.nanmean(e_u1_i ** 2)))
            acc[name]["u1_rmse_s"].append(np.sqrt(np.nanmean(e_u1_s ** 2)))
            acc[name]["var_rmse_i"].append(np.sqrt(np.nanmean(e_var ** 2)))
            acc[name]["K_rmse"].append(np.sqrt(np.nanmean(eK ** 2)))
            acc[name]["K_band"].append(band_power_ratio(tl, eK))

    print("\n--- RQ1: dark-baseline characterization (median across cams) ---")
    for k, v in gt_acc.items():
        print(f"  {k:18s} {np.nanmedian(v):.4f}")

    print("\n--- RQ2/RQ3: estimator scores (median across cams) ---")
    hdr = f"{'est':6s} {'u1RMSE_i':>9s} {'u1RMSE_s':>9s} {'varRMSE':>9s} {'K_RMSE':>9s} {'K_band@1/15Hz':>14s}"
    print(hdr)
    for name in ESTIMATORS:
        a = acc[name]
        print(f"{name:6s} {np.nanmedian(a['u1_rmse_i']):9.4f} "
              f"{np.nanmedian(a['u1_rmse_s']):9.4f} "
              f"{np.nanmedian(a['var_rmse_i']):9.4f} "
              f"{np.nanmedian(a['K_rmse']):9.5f} "
              f"{np.nanmedian(a['K_band']):14.2f}")


# ---------------------------------------------------------------------------
# Phase C — v2: production-exact combos, gated robust metrics
# ---------------------------------------------------------------------------
# Production applies (û₁ from u1-darks, ŝtd from std-darks), then
#   mean_dc = u1 - û₁ ;  var_dc = max(0, std² - ŝtd²) ;  K = √var_dc/mean_dc
# GT (batch path) interpolates u1 and VAR linearly between bounding darks.

COMBOS = {
    #  name      u1-estimator        std-estimator
    "PROD":    ("AVG3",  "LIN2"),    # current HybridRealtimePredictor
    "ZOHb":    ("ZOH",   "ZOH"),     # the new "zoh" flag
    "AVG3b":   ("AVG3",  "AVG3"),
    "AVG4b":   ("AVG4",  "AVG4"),
    "A3+ZOH":  ("AVG3",  "ZOH"),
    "A3+LK4":  ("AVG3",  "LINK4"),
    "A3+EMA":  ("AVG3",  "EMA.5"),
    "A3+LLT":  ("AVG3",  "LLT"),
}


def band_rms(t, e, f0=1.0 / DARK_INTERVAL_S, rel_bw=0.25):
    """Sinusoid-equivalent amplitude of e in the band around f0, with the
    flank-band background subtracted in power. Returns (amp, flank_amp)."""
    from scipy.signal import welch
    m = np.isfinite(e)
    if m.sum() < 2048:
        return (np.nan, np.nan)
    tt, ee = t[m], e[m]
    fs = 1.0 / np.median(np.diff(tt))
    ee = ee - ee.mean()
    f, p = welch(ee, fs=fs, nperseg=min(4096, len(ee)))
    df = f[1] - f[0]
    band = (f >= f0 * (1 - rel_bw)) & (f <= f0 * (1 + rel_bw))
    flank = ((f >= f0 * 1.5) & (f <= f0 * 2.5)) | ((f >= f0 * 0.3) & (f <= f0 * 0.6))
    if not band.any() or not flank.any():
        return (np.nan, np.nan)
    pwr_band = p[band].sum() * df
    # flank power scaled to the band's width
    pwr_flank = p[flank].mean() * band.sum() * df
    excess = max(pwr_band - pwr_flank, 0.0)
    return (float(np.sqrt(2 * excess)), float(np.sqrt(2 * pwr_flank)))


def analyze2(npz_path: str) -> None:
    d = np.load(npz_path)
    side, cam = d["side"], d["cam"]
    t, is_dark = d["t"], d["is_dark"].astype(bool)
    u1, std = d["u1"], d["std"]

    keys = sorted({(int(s), int(c)) for s, c in zip(side, cam)})
    print(f"\n=== {npz_path}: {len(t)} rows ===")

    res = {name: {"medK": [], "p95K": [], "bandK": [], "trace_bandK": []}
           for name in COMBOS}
    gt_band = []
    n_gated = []

    for (s, c) in keys:
        m = (side == s) & (cam == c)
        order = np.argsort(t[m])
        tm = t[m][order]; dk = is_dark[m][order]
        u1m = u1[m][order]; stdm = std[m][order]

        td, u1d, stdd = tm[dk], u1m[dk], stdm[dk]
        tl, u1l, stdl = tm[~dk], u1m[~dk], stdm[~dk]
        if len(td) < 5 or len(tl) < 500:
            continue
        vard = stdd ** 2

        g_u1 = gt_interp(td, u1d, tl)
        g_var = np.maximum(gt_interp(td, vard, tl), 0)
        mean_dc_gt = u1l - g_u1
        var_dc_gt = np.maximum(stdl ** 2 - g_var, 0)
        with np.errstate(invalid="ignore", divide="ignore"):
            K_gt = np.where(mean_dc_gt > 0,
                            np.sqrt(var_dc_gt) / mean_dc_gt, np.nan)

        # Gate: healthy-signal light frames only, fixed across all combos.
        gate = mean_dc_gt > max(20.0, 0.3 * np.nanmedian(mean_dc_gt))
        n_gated.append(int((~gate).sum()))

        gt_band.append(band_rms(tl[gate], K_gt[gate])[0])

        for name, (eu, es) in COMBOS.items():
            p_u1 = ESTIMATORS[eu](td, u1d, tl)
            p_std = ESTIMATORS[es](td, stdd, tl)
            mean_dc = u1l - p_u1
            var_dc = np.maximum(stdl ** 2 - p_std ** 2, 0)
            with np.errstate(invalid="ignore", divide="ignore"):
                K = np.where(mean_dc > 0, np.sqrt(var_dc) / mean_dc, np.nan)
            eK = (K - K_gt)[gate]
            res[name]["medK"].append(np.nanmedian(np.abs(eK)))
            res[name]["p95K"].append(np.nanpercentile(np.abs(eK), 95))
            res[name]["bandK"].append(band_rms(tl[gate], eK)[0])
            res[name]["trace_bandK"].append(band_rms(tl[gate], K[gate])[0])

    print(f"gated-out frames per cam (median): {np.median(n_gated):.0f}")
    print(f"GT K-trace 15s-band amplitude (median across cams): "
          f"{np.nanmedian(gt_band):.5f}  (K units; BFI = 10/c_span x this)")
    print(f"\n{'combo':8s} {'med|eK|':>10s} {'p95|eK|':>10s} "
          f"{'eK band amp':>12s} {'K-trace band':>13s}")
    for name in COMBOS:
        r = res[name]
        print(f"{name:8s} {np.nanmedian(r['medK']):10.6f} "
              f"{np.nanmedian(r['p95K']):10.6f} "
              f"{np.nanmedian(r['bandK']):12.6f} "
              f"{np.nanmedian(r['trace_bandK']):13.6f}")


if __name__ == "__main__":
    if sys.argv[1] == "extract":
        extract(sys.argv[2], sys.argv[3], sys.argv[4])
    elif sys.argv[1] == "analyze":
        analyze(sys.argv[2])
    elif sys.argv[1] == "analyze2":
        analyze2(sys.argv[2])
    else:
        raise SystemExit(__doc__)
