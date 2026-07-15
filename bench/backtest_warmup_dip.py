#!/usr/bin/env python3
"""Backtest: do the early image-mean dips co-occur with rapid die warm-up,
and if so, which thermal metric best predicts when each dip RECOVERS?

Theory under test (rate-lag): the sensor's internal temperature compensation
can't keep up while the die heats quickly, so the dip should track dT/dt.
Competing theory (disequilibrium): the dip persists until the assembly
reaches thermal steady state, so it should track the remaining warmup
(deficit to final temperature) regardless of instantaneous rate.

Caveat: during a passive first-order warmup dT/dt and the absolute deficit
are proportional (dT/dt ~ deficit/tau), so they only separate if tau differs
across cameras/runs. The FRACTIONAL warmup completed does differ, so the
spread of each metric at recovery, across events, is the discriminator:
whichever metric is most consistent across all dip events is the better
predictor.

For every run in the registry with an analysis CSV, and every camera whose
plateau mean_norm exceeds a floor: locate the dip (minimum of mean_norm in
minutes 1-6), its depth vs plateau, its recovery time (first return to 99%
of plateau), and the thermal state at recovery. Prints a table and saves an
overlay figure (mean_norm/plateau + dT/dt per run).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# Default matches the original bench runs; override with DRIFT_DATA_DIR to point
# at another run set (e.g. the overnight DIP2H series in bench/dip_overnight_out).
DATA_DIR = Path(os.environ.get("DRIFT_DATA_DIR", "bench/drift_scan_out"))
BIN_S = 5.0
BRIGHT_MIN = 0.1          # plateau mean_norm floor for a camera to be usable
PLATEAU_LO_S, PLATEAU_HI_S = 600.0, 1500.0
DIP_SEARCH_LO_S, DIP_SEARCH_HI_S = 60.0, 360.0
RECOVERY_FRACTION = 0.99


def series_at(s: pd.Series, t: float) -> float:
    if not np.isfinite(t):
        return float("nan")
    return float(s.iloc[s.index.get_indexer([t], method="nearest")[0]])


def main() -> int:
    runs = sys.argv[1:] or ["DRIFT30", "DRIFT30B", "DRIFT30C"]
    rows = []

    fig, axes = plt.subplots(len(runs), 1, figsize=(12, 3.2 * len(runs)), sharex=True)
    axes = np.atleast_1d(axes)

    for ax, subj in zip(axes, runs):
        df = pd.read_csv(DATA_DIR / f"{subj}_analysis.csv",
                         usecols=["cam_id", "timestamp_s", "is_dark", "mean_norm", "temperature"])
        ax2 = ax.twinx()

        for cam in range(8):
            d = df[df["cam_id"] == cam].sort_values("timestamp_s").copy()
            if d.empty:
                continue
            d["tb"] = (d["timestamp_s"] // BIN_S) * BIN_S + BIN_S / 2
            light = d[(~d["is_dark"]) & (d["mean_norm"] > 0)]
            if light.empty:
                continue
            mn = light.groupby("tb")["mean_norm"].median()
            tc = d.groupby("tb")["temperature"].median().rolling(5, center=True, min_periods=1).mean()

            plateau_sel = mn[(mn.index >= PLATEAU_LO_S) & (mn.index <= PLATEAU_HI_S)]
            if plateau_sel.empty:
                continue
            plateau = float(plateau_sel.median())
            if not np.isfinite(plateau) or plateau < BRIGHT_MIN:
                continue

            t_final_sel = tc[(tc.index >= PLATEAU_LO_S) & (tc.index <= PLATEAU_HI_S)]
            T_final = float(t_final_sel.median())
            T_start = float(tc.iloc[1])  # skip bin 0 (t=0 sensor-read artifacts)

            early = mn[(mn.index >= DIP_SEARCH_LO_S) & (mn.index <= DIP_SEARCH_HI_S)]
            if early.empty:
                continue
            t_dip = float(early.idxmin())
            dip_pct = (plateau - float(early.min())) / plateau * 100.0

            after = mn[(mn.index >= t_dip) & (mn >= RECOVERY_FRACTION * plateau)]
            t_rec = float(after.index[0]) if len(after) else float("nan")

            dTdt = pd.Series(np.gradient(tc.to_numpy(), tc.index.to_numpy()) * 60.0, index=tc.index)
            T_at_rec = series_at(tc, t_rec)
            warm_frac = (T_at_rec - T_start) / (T_final - T_start) if T_final > T_start else float("nan")

            rows.append({
                "run": subj, "cam": cam + 1,
                "plateau_DN_uW": round(plateau, 3),
                "dip_pct": round(dip_pct, 2),
                "t_dip_min": round(t_dip / 60.0, 2),
                "t_rec_min": round(t_rec / 60.0, 2) if np.isfinite(t_rec) else None,
                "T_start_C": round(T_start, 1),
                "T_final_C": round(T_final, 1),
                "dTdt_max_C_min": round(float(dTdt[dTdt.index >= 60.0].max()), 1),
                "dTdt_at_rec": round(series_at(dTdt, t_rec), 2),
                "deficit_at_rec_C": round(T_final - T_at_rec, 2),
                "warm_frac_at_rec": round(warm_frac, 3),
            })

            ax.plot(mn.index / 60.0, mn / plateau, lw=1.1, label=f"cam {cam + 1}")
            ax2.plot(dTdt.index / 60.0, dTdt, ls="--", lw=0.7, alpha=0.45, color="gray")

        ax.set_title(subj, fontsize=11)
        ax.set_ylabel("mean_norm / plateau")
        ax.set_ylim(0.75, 1.08)
        ax.grid(True, alpha=0.3)
        ax.legend(ncol=4, fontsize=8, loc="lower right")
        ax2.set_ylabel("dT/dt (°C/min)", color="gray")
        ax2.tick_params(axis="y", labelcolor="gray")

    axes[-1].set_xlabel("Time (min)")
    axes[-1].set_xlim(0, 15)
    fig.suptitle("Warmup dip backtest: normalized intensity (rel. plateau) + dT/dt", y=0.995)
    fig.tight_layout()
    out = DATA_DIR / "warmup_dip_backtest.png"
    fig.savefig(out, dpi=150)
    print(f"[+] Saved {out}\n")

    t = pd.DataFrame(rows)
    print(t.to_string(index=False))

    dips = t[t["dip_pct"] > 1.0].dropna(subset=["t_rec_min"])
    if len(dips) >= 3:
        print("\n=== Consistency of thermal state at dip recovery (lower rel. spread = better predictor) ===")
        for col in ["dTdt_at_rec", "deficit_at_rec_C", "warm_frac_at_rec"]:
            v = dips[col].astype(float)
            rel = v.std() / abs(v.mean()) if v.mean() else float("inf")
            print(f"  {col:20s} mean={v.mean():8.3f}  std={v.std():7.3f}  rel_spread={rel:6.2f}")
        print("\n=== Dip depth vs thermal ramp (across events) ===")
        for col in ["dTdt_max_C_min", "T_final_C"]:
            r = np.corrcoef(t["dip_pct"].astype(float), t[col].astype(float))[0, 1]
            print(f"  corr(dip_pct, {col}) = {r:+.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
