"""Full analysis of the 8h camera off-sweep experiment (v2).

v2 fixes vs v1:
  - ALL per-camera series are ordered by timestamp_s. frame_id wraps (2048
    frames = 51.2 s at 40 fps, see docs/scan-sequencing.md) so sorting by it
    scrambles time and fabricates jumps.
  - Temperature is despiked (rolling-median, |T-med|>3 C replaced) before any
    fit; glitch counts reported separately.
  - Frozen-temperature scans (range < 0.5 C after despike) are excluded from
    temperature fits/models and flagged loudly (C07 right showed this).
  - Mean-vs-T / std-vs-T models use rolling-median-smoothed series to remove
    ambient light flicker; flicker quantified separately.

Outputs: CSV tables + PNGs in report_assets/ (see bottom).
"""

import re
import sys
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from fitting import fit_exp, fit_exp2

# Usage: analyze_sweep.py [run1|ff] [data_dir]
#   run1 preset: mask 0x0F run (4 cams/side, subject SWEEP8H)
#   ff   preset: mask 0xFF run (8 cams/side, subject FFSWEEP)
# data_dir defaults to the 2026-07-11 bench locations. Outputs (fit CSVs +
# report_assets/ figures) land in <data_dir>/analysis.
RUN = sys.argv[1] if len(sys.argv) > 1 else "run1"
if RUN == "ff":
    DATA = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(
        r"C:\Users\openwater\Projects\openmotion-sdk\scan_off_cycle_data_ff")
    RUNLOG = DATA / "ffsweep_stdout.log"
    SUBJECT, MASK_HEX = "FFSWEEP", "FF"
    CAMS = list(range(8))
else:
    DATA = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(
        r"C:\Users\openwater\Projects\openmotion-sdk\scan_off_cycle_data")
    RUNLOG = DATA / "sweep8h_stdout.log"
    SUBJECT, MASK_HEX = "SWEEP8H", "0F"
    CAMS = list(range(4))
DERIVED = DATA / "derived"
OUT = DATA / "analysis"
OUT.mkdir(exist_ok=True)
ASSETS = OUT / "report_assets"
ASSETS.mkdir(exist_ok=True)

# Fixed categorical slot order (dataviz reference palette)
CAM_COLORS = ["#2a78d6", "#1baf7a", "#eda100", "#008300",
              "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"]
SEQ_RAMP = ["#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6",
            "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, SURFACE = "#e1e0d9", "#fcfcfb"
VIOLET, BLUE = "#4a3aa7", "#2a78d6"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE, "savefig.dpi": 150,
    "axes.edgecolor": "#c3c2b7", "axes.linewidth": 0.8,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False,
    "text.color": INK, "axes.labelcolor": INK2,
    "xtick.color": MUTED, "ytick.color": MUTED,
    "font.size": 9, "axes.titlesize": 10, "axes.titlecolor": INK,
    "legend.frameon": False,
})

STATS_RE = re.compile(
    rf"(\d{{8}}_\d{{6}})_{SUBJECT}_C(\d{{2}})_(left|right)_mask{MASK_HEX}_stats\.csv")
LOG_TS = "%Y-%m-%d %H:%M:%S,%f"

FROZEN_RANGE_C = 0.5     # temp range below this = frozen readout
SPIKE_C = 3.0            # |T - rolling med| beyond this = glitch
SMOOTH_WIN = 81          # ~2 s rolling median for flicker removal
STALE_SKIP_S = 0.5       # first frames carry the PREVIOUS scan's cached temp
SCAN_BODY_END_S = 1195.0 # past this = duration-gate/teardown boundary


def leading_const_run(x, tol=1e-6):
    """Length of the initial constant run (stale cached-temp window)."""
    if len(x) == 0:
        return 0
    d = np.abs(x - x[0]) > tol
    idx = np.argmax(d)
    return int(idx) if d.any() else len(x)


# ── load & prepare ──────────────────────────────────────────────────────
def prep_cam(df, cam, light_only=False):
    """Time-ordered per-camera slice with despiked temp + smoothed mean/std."""
    g = df[(df.cam_id == cam) & (df.timestamp_s >= 0)]
    if light_only:
        g = g[g.type == "light"]
    g = g.sort_values("timestamp_s").reset_index(drop=True)
    t = g.temperature.to_numpy(dtype=float)
    med = pd.Series(t).rolling(5, center=True, min_periods=1).median().to_numpy()
    spikes = np.abs(t - med) > SPIKE_C
    tc = np.where(spikes, med, t)
    g["temp_clean"] = tc
    g["n_temp_glitch"] = int(spikes.sum())
    g["mean_smooth"] = (g["mean"].rolling(SMOOTH_WIN, center=True, min_periods=20)
                        .median())
    g["std_smooth"] = (g["std"].rolling(SMOOTH_WIN, center=True, min_periods=20)
                       .median())
    g["frozen"] = bool((np.nanmax(tc) - np.nanmin(tc)) < FROZEN_RANGE_C)
    # Per-camera stale-start window: with 8 cams the firmware's temp poll
    # round-robin stretches to ~1 s, so a fixed skip is not enough.
    stale_n = leading_const_run(t)
    ts_arr = g.timestamp_s.to_numpy()
    idx = min(stale_n, len(ts_arr) - 1)
    g["stale_n"] = stale_n
    g["body_start_s"] = float(max(STALE_SKIP_S, ts_arr[idx] + 0.15))
    return g


def load_scans():
    scans = {}
    for p in sorted(DERIVED.glob(f"*{SUBJECT}*_stats.csv")):
        m = STATS_RE.match(p.name)
        if not m:
            continue
        scans[(int(m.group(2)), m.group(3))] = {
            "label": m.group(1), "df": pd.read_csv(p)}
    return scans


def parse_run_log():
    pat = {
        "cycle": re.compile(r"^(\S+ \S+) INFO .*=== Cycle (\d+)/10 ==="),
        "pwr_on": re.compile(r"^(\S+ \S+) INFO .*left: powered on cameras"),
        "scan_start": re.compile(r"^(\S+ \S+) INFO .*Scan (\S+) started"),
        "scan_done": re.compile(r"^(\S+ \S+) INFO .*Scan (\S+) complete in"),
        "pwr_off": re.compile(r"^(\S+ \S+) INFO .*left: camera power OFF"),
    }
    rows, cur = [], None
    for line in RUNLOG.read_text(encoding="utf-8", errors="replace").splitlines():
        for key, rx in pat.items():
            m = rx.match(line)
            if not m:
                continue
            ts = datetime.strptime(m.group(1), LOG_TS)
            if key == "cycle":
                if cur:
                    rows.append(cur)
                cur = {"cycle": int(m.group(2)), "t_cycle": ts}
            elif cur is not None and key not in cur:
                cur[key] = ts
    if cur:
        rows.append(cur)
    out, prev_off = [], None
    for r in rows:
        off_min = ((r["pwr_on"] - prev_off).total_seconds() / 60
                   if prev_off is not None and "pwr_on" in r else np.nan)
        lead = ((r["scan_start"] - r["pwr_on"]).total_seconds()
                if "pwr_on" in r and "scan_start" in r else np.nan)
        out.append({"cycle": r["cycle"], "off_min": off_min,
                    "poweron_to_scan_s": lead})
        prev_off = r.get("pwr_off")
    return pd.DataFrame(out)


# ── fits ────────────────────────────────────────────────────────────────
def warmup_fits(scans):
    rows = []
    for (n, side), rec in sorted(scans.items()):
        df = rec["df"]
        for cam in sorted(df.cam_id.unique()):
            g = prep_cam(df, cam)
            frozen = bool(g["frozen"].iloc[0])
            tmax = g.timestamp_s.max()
            stale_n = int(g["stale_n"].iloc[0])
            body = g[g.timestamp_s >= g["body_start_s"].iloc[0]]
            gt = body.iloc[::10]
            r1 = r2 = None
            if not frozen:
                r1 = fit_exp(gt.timestamp_s, gt.temp_clean, tau_bounds=(5.0, 3000.0))
                r2 = fit_exp2(gt.timestamp_s, gt.temp_clean)
            lt = prep_cam(df, cam, light_only=True)
            ls = lt.dropna(subset=["mean_smooth"]).iloc[::10]
            rm = fit_exp(ls.timestamp_s, ls.mean_smooth, tau_bounds=(5.0, 3000.0))
            rs = fit_exp(ls.timestamp_s, ls.std_smooth, tau_bounds=(5.0, 3000.0))
            dmean = np.diff(lt["mean"].to_numpy())
            rows.append({
                "scan": n, "side": side, "cam": cam, "frozen_temp": frozen,
                "temp_glitches": int(g["n_temp_glitch"].iloc[0]),
                "stale_start_frames": stale_n,
                "T_start": body[body.timestamp_s < 3.0].temp_clean.median(),
                "T_end": g[g.timestamp_s > tmax - 10].temp_clean.median(),
                "tau_T_s": r1["tau_s"] if r1 else np.nan,
                "T_inf": r1["y_inf"] if r1 else np.nan,
                "rmse_T": r1["rmse"] if r1 else np.nan,
                "tau2_fast_s": r2["tau1_s"] if r2 else np.nan,
                "tau2_slow_s": r2["tau2_s"] if r2 else np.nan,
                "rmse_T2": r2["rmse"] if r2 else np.nan,
                "mean_light": lt["mean"].mean(),
                "mean_dark": df[(df.cam_id == cam) & (df.type == "dark")
                                & (df.timestamp_s >= 0)]["mean"].mean(),
                "tau_mean_s": rm["tau_s"] if rm else np.nan,
                "mean0": rm["y0"] if rm else np.nan,
                "mean_inf": rm["y_inf"] if rm else np.nan,
                "tau_std_s": rs["tau_s"] if rs else np.nan,
                "flicker_rms": float(np.nanstd(dmean)),
                "flicker_pct": float(100 * np.nanstd(dmean) / lt["mean"].mean()),
            })
    return pd.DataFrame(rows)


def cooling_fits(wf, timing):
    off_by_cycle = dict(zip(timing.cycle, timing.off_min))
    rows, points = [], []
    for side in ("left", "right"):
        for cam in CAMS:
            sub = wf[(wf.side == side) & (wf.cam == cam)].sort_values("scan")
            W, T0, TE = [], [], []
            for i in range(1, len(sub)):
                a, b = sub.iloc[i - 1], sub.iloc[i]
                w = off_by_cycle.get(int(b.scan), np.nan)
                if not np.isfinite(w) or a.frozen_temp or b.frozen_temp:
                    continue
                W.append(w * 60.0); T0.append(b.T_start); TE.append(a.T_end)
            W, T0, TE = map(np.asarray, (W, T0, TE))
            if len(W) < 4:
                continue
            best = (np.inf, None, None)
            for tau in np.geomspace(30, 6000, 500):
                e = np.exp(-W / tau)
                denom = ((1 - e) ** 2).sum()
                floor = (((T0 - TE * e) * (1 - e)).sum()) / denom
                sse = ((floor + (TE - floor) * e - T0) ** 2).sum()
                if sse < best[0]:
                    best = (sse, tau, floor)
            sse, tau, floor = best
            rows.append({"side": side, "cam": cam, "tau_cool_s": tau,
                         "tau_cool_min": tau / 60, "T_floor": floor,
                         "rmse": np.sqrt(sse / len(W)), "n_points": len(W)})
            for w, t0, te in zip(W, T0, TE):
                points.append({"side": side, "cam": cam, "wait_min": w / 60,
                               "T_start": t0, "T_end_prev": te})
    return (pd.DataFrame(rows, columns=["side", "cam", "tau_cool_s",
                                        "tau_cool_min", "T_floor", "rmse",
                                        "n_points"]),
            pd.DataFrame(points, columns=["side", "cam", "wait_min",
                                          "T_start", "T_end_prev"]))


def mean_std_models(scans, wf):
    frozen = {(r.scan, r.side, r.cam) for r in wf.itertuples() if r.frozen_temp}
    rows = []
    for side in ("left", "right"):
        for cam in CAMS:
            ts, ms, ss = [], [], []
            for (n, s), rec in sorted(scans.items()):
                if s != side or (n, s, cam) in frozen:
                    continue
                g = prep_cam(rec["df"], cam, light_only=True)
                g = g.dropna(subset=["mean_smooth", "std_smooth"]).iloc[::25]
                ts.append(g.temp_clean.to_numpy())
                ms.append(g.mean_smooth.to_numpy())
                ss.append(g.std_smooth.to_numpy())
            if not ts:
                continue
            T = np.concatenate(ts); M = np.concatenate(ms); S = np.concatenate(ss)
            ok = np.isfinite(T) & np.isfinite(M) & np.isfinite(S)
            T, M, S = T[ok], M[ok], S[ok]
            bm, am = np.polyfit(T, M, 1)
            bs, as_ = np.polyfit(T, S, 1)
            r2m = 1 - ((M - (am + bm * T)) ** 2).sum() / ((M - M.mean()) ** 2).sum()
            r2s = 1 - ((S - (as_ + bs * T)) ** 2).sum() / ((S - S.mean()) ** 2).sum()
            rows.append({
                "side": side, "cam": cam, "n": len(T),
                "mean_slope_per_C": bm, "mean_intercept": am, "r2_mean": r2m,
                "mean_pct_per_C": 100 * bm / M.mean(),
                "std_slope_per_C": bs, "std_intercept": as_, "r2_std": r2s,
            })
    return pd.DataFrame(rows)


def discontinuities(scans):
    """Time-ordered per-camera continuity check."""
    rows = []
    for (n, side), rec in sorted(scans.items()):
        df = rec["df"]
        neg = int((df.timestamp_s < 0).sum())
        for cam in sorted(df.cam_id.unique()):
            g = prep_cam(df, cam)
            t = g.timestamp_s.to_numpy()
            stale_n = int(g["stale_n"].iloc[0])
            dt = np.diff(t)
            mid = t[:-1] < SCAN_BODY_END_S        # exclude teardown boundary
            gaps_mid = (dt > 0.0375) & mid
            gaps_end = (dt > 0.0375) & ~mid
            dupes = dt < 0.010
            # temp steps evaluated past the (per-camera) stale-start window
            body_mask = t[:-1] >= g["body_start_s"].iloc[0]
            dT = np.abs(np.diff(g.temp_clean.to_numpy()))
            dT_body = dT[body_mask]
            ms = g["mean_smooth"].to_numpy()
            step = np.abs(np.diff(ms[~np.isnan(ms)]))
            rows.append({
                "scan": n, "side": side, "cam": cam,
                "neg_ts_rows_scanwide": neg,
                "frozen_temp": bool(g["frozen"].iloc[0]),
                "stale_start_frames": stale_n,
                "stale_start_s": float(t[min(stale_n, len(t) - 1)]),
                "temp_glitches": int(g["n_temp_glitch"].iloc[0]),
                "midscan_gaps": int(gaps_mid.sum()),
                "midscan_missing_frames": int(np.round(
                    (dt[gaps_mid] / 0.025 - 1).sum()) if gaps_mid.any() else 0),
                "teardown_gaps": int(gaps_end.sum()),
                "dupe_dt": int(dupes.sum()),
                "temp_steps_gt1C_body": int((dT_body > 1.0).sum()),
                "max_temp_step_body_C": float(dT_body.max()) if dT_body.size else np.nan,
                "smoothed_mean_steps_gt2": int((step > 2.0).sum()),
                "max_smoothed_mean_step": float(step.max()) if step.size else np.nan,
            })
    return pd.DataFrame(rows)


# ── figures ─────────────────────────────────────────────────────────────
def fig_warmup(scans, timing):
    off_by_cycle = dict(zip(timing.cycle, timing.off_min))
    order = sorted({n for (n, s) in scans if s == "left"},
                   key=lambda n: (off_by_cycle.get(n) if np.isfinite(
                       off_by_cycle.get(n, np.nan)) else -1))
    color_of = {n: SEQ_RAMP[min(i, len(SEQ_RAMP) - 1)] for i, n in enumerate(order)}
    ncols = 4 if len(CAMS) > 4 else 2
    nrows = -(-len(CAMS) // ncols)
    for side in ("left", "right"):
        fig, axes = plt.subplots(nrows, ncols,
                                 figsize=(2.4 + 2.9 * ncols, 1.2 + 2.7 * nrows),
                                 sharex=True)
        for cam, ax in zip(CAMS, np.atleast_1d(axes).ravel()):
            for n in order:
                rec = scans.get((n, side))
                if not rec:
                    continue
                g = prep_cam(rec["df"], cam)
                if bool(g["frozen"].iloc[0]):
                    continue
                g = g[g.timestamp_s >= g["body_start_s"].iloc[0]].iloc[::40]
                w = off_by_cycle.get(n, np.nan)
                lbl = f"{w:.0f} min off" if np.isfinite(w) else "first scan"
                ax.plot(g.timestamp_s / 60, g.temp_clean, lw=1.8,
                        color=color_of[n], label=lbl)
            ax.set_title(f"cam {cam}")
            if cam >= len(CAMS) - ncols:
                ax.set_xlabel("time in scan (min)")
            if cam % ncols == 0:
                ax.set_ylabel("die temp (°C)")
        handles, labels = np.atleast_2d(axes)[0, 0].get_legend_handles_labels()
        fig.legend(handles, labels, loc="lower center", ncol=5,
                   bbox_to_anchor=(0.5, -0.02))
        fig.suptitle(f"Warm-up during 20-min scans — {side} sensor "
                     "(line color = preceding cameras-off duration)", color=INK)
        fig.tight_layout(rect=[0, 0.06, 1, 1])
        fig.savefig(ASSETS / f"warmup_{side}.png", bbox_inches="tight")
        plt.close(fig)


def fig_cooling(cool_fits, cool_pts):
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.4), sharey=True)
    for side, ax in zip(("left", "right"), axes):
        for cam in CAMS:
            pts = cool_pts[(cool_pts.side == side) & (cool_pts.cam == cam)]
            f = cool_fits[(cool_fits.side == side) & (cool_fits.cam == cam)]
            c = CAM_COLORS[cam]
            ax.scatter(pts.wait_min, pts.T_start, s=26, color=c, zorder=3,
                       edgecolor=SURFACE, linewidth=1.2)
            if len(f):
                f = f.iloc[0]
                w = np.linspace(0, 60, 200)
                te = pts.T_end_prev.mean()
                y = f.T_floor + (te - f.T_floor) * np.exp(-w * 60 / f.tau_cool_s)
                ax.plot(w, y, color=c, lw=2, alpha=0.85,
                        label=f"cam {cam}  τ={f.tau_cool_s / 60:.1f} min, "
                              f"floor={f.T_floor:.0f} °C")
        ax.set_title(f"{side} sensor")
        ax.set_xlabel("cameras-off duration before scan (min)")
        ax.set_xlim(0, 60)
        ax.legend(fontsize=7.5, loc="upper right")
    axes[0].set_ylabel("die temp at next scan start (°C)")
    fig.suptitle("Cool-down: start temperature vs preceding off period "
                 "(curves: T = floor + (T_end − floor)·e^(−W/τ))", color=INK)
    fig.tight_layout()
    fig.savefig(ASSETS / "cooling.png", bbox_inches="tight")
    plt.close(fig)


def fig_mean_std_vs_T(scans, wf):
    frozen = {(r.scan, r.side, r.cam) for r in wf.itertuples() if r.frozen_temp}
    rows_per_side = -(-len(CAMS) // 4)
    for metric, fname in (("mean_smooth", "mean_vs_temp.png"),
                          ("std_smooth", "std_vs_temp.png")):
        fig, axes = plt.subplots(2 * rows_per_side, 4,
                                 figsize=(11, 2.6 * 2 * rows_per_side + 0.6))
        for r, side in enumerate(("left", "right")):
            for cam in CAMS:
                ax = axes[r * rows_per_side + cam // 4, cam % 4]
                T_all, Y_all = [], []
                for (n, s), rec in sorted(scans.items()):
                    if s != side or (n, s, cam) in frozen:
                        continue
                    g = prep_cam(rec["df"], cam, light_only=True)
                    g = g.dropna(subset=[metric]).iloc[::100]
                    T_all.append(g.temp_clean.to_numpy())
                    Y_all.append(g[metric].to_numpy())
                if not T_all:
                    continue
                T = np.concatenate(T_all); Y = np.concatenate(Y_all)
                ax.scatter(T, Y, s=3, color=BLUE, alpha=0.25, linewidths=0)
                b, a = np.polyfit(T, Y, 1)
                tx = np.array([T.min(), T.max()])
                ax.plot(tx, a + b * tx, color=INK, lw=1.6)
                ax.set_title(f"{side} cam {cam}", fontsize=9)
                ax.annotate(f"{b:+.3f}/°C", xy=(0.04, 0.87),
                            xycoords="axes fraction", fontsize=8, color=INK2)
                if r == 1 and cam >= len(CAMS) - 4:
                    ax.set_xlabel("die temp (°C)")
                if cam % 4 == 0:
                    ax.set_ylabel(metric.replace("_smooth", "") + " (counts)")
        fig.suptitle(f"Light-frame {metric.replace('_smooth', '')} (2 s median) "
                     "vs die temperature — all scans pooled", color=INK)
        fig.tight_layout()
        fig.savefig(ASSETS / fname, bbox_inches="tight")
        plt.close(fig)


def fig_timeseries_example(scans, scan_n=5, side="left"):
    rec = scans.get((scan_n, side))
    if not rec:
        return
    fig, axes = plt.subplots(3, 1, figsize=(9, 7), sharex=True)
    for cam in CAMS:
        g = prep_cam(rec["df"], cam, light_only=True)
        g = g[g.timestamp_s >= g["body_start_s"].iloc[0]].iloc[::20]
        c = CAM_COLORS[cam]
        base_m = g.mean_smooth.iloc[:120].median()
        base_s = g.std_smooth.iloc[:120].median()
        tmin = g.timestamp_s / 60
        axes[0].plot(tmin, g.temp_clean, lw=1.6, color=c, label=f"cam {cam}")
        axes[1].plot(tmin, 100 * (g["mean"] / base_m - 1), lw=0.7, color=c,
                     alpha=0.35)
        axes[1].plot(tmin, 100 * (g.mean_smooth / base_m - 1), lw=1.8, color=c)
        axes[2].plot(tmin, 100 * (g["std"] / base_s - 1), lw=0.7, color=c,
                     alpha=0.35)
        axes[2].plot(tmin, 100 * (g.std_smooth / base_s - 1), lw=1.8, color=c)
    axes[0].set_ylabel("die temp (°C)")
    axes[1].set_ylabel("mean drift (% of start)")
    axes[2].set_ylabel("std drift (% of start)")
    axes[2].set_xlabel("time in scan (min)")
    axes[0].legend(ncol=4, loc="lower right", fontsize=7.5)
    fig.suptitle(f"Scan C{scan_n:02d} ({side}): temperature and relative "
                 "mean/std drift vs time — thin: per-frame, thick: 2 s median",
                 color=INK)
    fig.tight_layout()
    fig.savefig(ASSETS / "timeseries_example.png", bbox_inches="tight")
    plt.close(fig)


def fig_tau_compare(wf, cool_fits):
    fig, ax = plt.subplots(figsize=(8.5, 1.4 + 0.32 * 2 * len(CAMS)))
    labels, y, idx = [], [], 0
    for side in ("left", "right"):
        for cam in CAMS:
            sub = wf[(wf.side == side) & (wf.cam == cam) & (~wf.frozen_temp)]
            cf = cool_fits[(cool_fits.side == side) & (cool_fits.cam == cam)]
            if not len(sub):
                continue
            taus = sub.tau_T_s.dropna() / 60
            ax.plot([taus.min(), taus.max()], [idx, idx], color=VIOLET,
                    lw=2, alpha=0.5)
            ax.scatter(taus.median(), idx, color=VIOLET, s=48, zorder=3,
                       label="warm-up τ (single-exp)" if idx == 0 else None)
            if len(cf):
                ax.scatter(cf.iloc[0].tau_cool_min, idx, color=BLUE, s=48,
                           zorder=3, marker="D",
                           label="cool-down τ" if idx == 0 else None)
            labels.append(f"{side[0].upper()} cam{cam}")
            y.append(idx); idx += 1
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_xlabel("time constant (min)")
    ax.legend(loc="lower right")
    ax.set_title("Thermal time constants per camera — warm-up (median + range) "
                 "vs cool-down", color=INK)
    fig.tight_layout()
    fig.savefig(ASSETS / "tau_compare.png", bbox_inches="tight")
    plt.close(fig)


# ── main ────────────────────────────────────────────────────────────────
def main():
    scans = load_scans()
    print(f"loaded {len(scans)} scan-side stats files "
          f"({sorted(set(n for n, _ in scans))})")
    fid_max = max(int(rec["df"].frame_id.max()) for rec in scans.values())
    print(f"max frame_id seen: {fid_max} (wrap check)")

    timing = parse_run_log()
    timing.to_csv(OUT / "cycle_timing.csv", index=False)
    print("\n== cycle timing ==")
    print(timing.round(3).to_string(index=False))

    wf = warmup_fits(scans)
    wf.to_csv(OUT / "fits_warmup.csv", index=False)
    print("\n== warm-up fits (despiked temperature, stale start skipped) ==")
    print(wf[["scan", "side", "cam", "frozen_temp", "stale_start_frames",
              "T_start", "T_end", "tau_T_s", "T_inf", "rmse_T", "tau2_fast_s",
              "tau2_slow_s", "rmse_T2", "flicker_rms", "flicker_pct"]]
          .round(2).to_string(index=False))

    cf, cpts = cooling_fits(wf, timing)
    cf.to_csv(OUT / "fits_cooling.csv", index=False)
    cpts.to_csv(OUT / "cooling_points.csv", index=False)
    print("\n== cooling fits (frozen scans excluded) ==")
    print(cf.round(2).to_string(index=False))

    mm = mean_std_models(scans, wf)
    mm.to_csv(OUT / "models_mean_std.csv", index=False)
    print("\n== mean/std vs T models (smoothed, light frames) ==")
    print(mm.round(4).to_string(index=False))

    dd = discontinuities(scans)
    dd.to_csv(OUT / "discontinuities.csv", index=False)
    print("\n== discontinuities ==")
    flagged = dd[(dd.midscan_gaps > 0) | (dd.temp_steps_gt1C_body > 0)
                 | (dd.smoothed_mean_steps_gt2 > 0) | (dd.frozen_temp)
                 | (dd.neg_ts_rows_scanwide > 0) | (dd.dupe_dt > 0)]
    print(flagged.to_string(index=False) if len(flagged) else "none")
    print("\n(all rows written to discontinuities.csv)")

    fig_warmup(scans, timing)
    if len(cf):
        fig_cooling(cf, cpts)
    fig_mean_std_vs_T(scans, wf)
    fig_timeseries_example(scans)
    fig_tau_compare(wf, cf)
    print(f"\nfigures -> {ASSETS}")


if __name__ == "__main__":
    main()
