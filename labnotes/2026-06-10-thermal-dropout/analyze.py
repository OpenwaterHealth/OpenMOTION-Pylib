#!/usr/bin/env python3
"""Analysis + figures for the 2026-06-10 thermal-dropout campaign.

Reads data/cycle_*/{verdict.json,samples.csv} and data/cool_*/cooling_curve.csv,
writes figures into figs/ and prints summary stats for the report.

Artifact cycles (comm-wedge, NOT thermal): 0, 1  — excluded from recovery stats.
"""

from __future__ import annotations

import csv
import glob
import json
import os
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
FIGS = os.path.join(HERE, "figs")
os.makedirs(FIGS, exist_ok=True)

ARTIFACT_CYCLES = {0, 1}  # imu_on comm wedge — see NOTEBOOK.md 03:21
TRIP_C = 115.0

MODE_LABEL = {
    "mains_off": "mains OFF (full power cut)",
    "idle_fan_on": "idle (cams powered, fan on)",
    "cams_off_fan_on": "camera rails off, fan on",
    "cams_off_fan_off": "camera rails off, fan off",
}
MODE_ORDER = ["mains_off", "cams_off_fan_on", "cams_off_fan_off", "idle_fan_on"]


def load_verdicts() -> list[dict]:
    out = []
    for p in sorted(glob.glob(os.path.join(DATA, "cycle_*", "verdict.json"))):
        try:
            with open(p, encoding="utf-8") as f:
                out.append(json.load(f))
        except Exception as e:
            print(f"!! unreadable {p}: {e}")
    return out


def load_samples(cycle: int):
    p = os.path.join(DATA, f"cycle_{cycle:03d}", "samples.csv")
    if not os.path.exists(p):
        return []
    rows = []
    with open(p, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                rows.append((float(r["t_s"]), r["side"], int(r["cam"]),
                             float(r["temp_c"]) if r["temp_c"] else None))
            except (ValueError, KeyError):
                continue
    return rows


# ── 1. dropout-event stats ─────────────────────────────────────────────────

def dropout_events(verdicts):
    ev = []
    for v in verdicts:
        for key, c in v.get("cameras", {}).items():
            if c.get("dropout_kind") == "dropout" and c.get("temp_at_drop_c"):
                ev.append({"cycle": v["cycle"], "cam": key,
                           "t_s": c["dropout_t_s"], "temp": c["temp_at_drop_c"]})
    return ev


# ── 2. recovery trials ─────────────────────────────────────────────────────

def recovery_trials(verdicts):
    """One row per (cycle with prev_dropped): mode, dur, n_recovered, n."""
    rows = []
    for v in verdicts:
        if v["cycle"] in ARTIFACT_CYCLES:
            continue
        # prev_dropped inherited from an artifact cycle isn't a thermal trip
        # (cameras were only comm-wedged), so the "recovery" is meaningless.
        if v["cycle"] - 1 in ARTIFACT_CYCLES:
            continue
        rec = {k: c.get("recovered") for k, c in v.get("cameras", {}).items()
               if c.get("was_dropped_last_cycle") and k.startswith("right")}
        rec = {k: r for k, r in rec.items() if r is not None}
        if not rec or v.get("cool_mode_before") is None:
            continue
        rows.append({
            "cycle": v["cycle"],
            "mode": v["cool_mode_before"],
            "dur_s": v.get("off_time_before_s"),
            "n_ok": sum(1 for r in rec.values() if r),
            "n": len(rec),
        })
    return rows


def fig_recovery(rows):
    fig, ax = plt.subplots(figsize=(9, 4.2))
    for r in rows:
        y = MODE_ORDER.index(r["mode"])
        frac = r["n_ok"] / r["n"]
        color = "#2a9d2a" if frac == 1.0 else ("#cc2222" if frac == 0.0 else "#e6a700")
        ax.scatter(r["dur_s"], y, s=260, c=color, zorder=3,
                   edgecolors="k", linewidths=0.8)
        ax.annotate(f"{r['n_ok']}/{r['n']}", (r["dur_s"], y),
                    ha="center", va="center", fontsize=8, zorder=4,
                    color="white", fontweight="bold")
        ax.annotate(f"c{r['cycle']}", (r["dur_s"], y + 0.22),
                    ha="center", va="bottom", fontsize=7, color="#666")
    ax.set_xscale("log")
    ax.set_yticks(range(len(MODE_ORDER)))
    ax.set_yticklabels([MODE_LABEL[m] for m in MODE_ORDER])
    ax.set_ylim(-0.6, len(MODE_ORDER) - 0.4)
    ax.set_xlabel("cooling duration before bring-up (s, log scale)")
    ax.set_title("Tripped-camera recovery vs cooling mode & duration "
                 "(right module; green = all recovered, red = none)")
    ax.grid(True, axis="x", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(FIGS, "fig_recovery_matrix.png"), dpi=150)
    plt.close(fig)


# ── 3. die-temperature traces ──────────────────────────────────────────────

def fig_die_temps(verdicts):
    heat_cycles = [v["cycle"] for v in verdicts
                   if v.get("scan", {}).get("trigger_iso")
                   and load_samples(v["cycle"])]
    n = len(heat_cycles)
    if not n:
        return
    cols = min(3, n)
    rows_n = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows_n, cols, figsize=(5.2 * cols, 3.4 * rows_n),
                             squeeze=False, sharey=True)
    cmap = plt.get_cmap("tab10")
    for i, cyc in enumerate(heat_cycles):
        ax = axes[i // cols][i % cols]
        samples = load_samples(cyc)
        by_cam = defaultdict(list)
        for t, side, cam, temp in samples:
            if temp is not None:
                by_cam[cam].append((t, temp))
        for cam, pts in sorted(by_cam.items()):
            pts.sort()
            ax.plot([p[0] for p in pts], [p[1] for p in pts],
                    color=cmap(cam), lw=1.2, label=f"cam{cam}")
        v = next(x for x in verdicts if x["cycle"] == cyc)
        for key, c in v.get("cameras", {}).items():
            if c.get("dropout_kind") == "dropout" and c.get("dropout_t_s"):
                cam = int(key.split(":")[1])
                ax.scatter([c["dropout_t_s"]], [c.get("temp_at_drop_c") or TRIP_C],
                           marker="x", s=70, color=cmap(cam), zorder=5)
        ax.axhline(TRIP_C, color="red", ls="--", lw=1, alpha=0.7)
        fans = v.get("fans", "?")
        cool = f"{v.get('cool_mode_before')} {v.get('off_time_before_s')}s"
        ax.set_title(f"cycle {cyc} (fans {fans}; after {cool})", fontsize=9)
        ax.set_xlabel("scan time (s)")
        ax.set_ylabel("die temp (°C)")
        ax.grid(alpha=0.3)
    axes[0][0].legend(fontsize=7, ncol=2, loc="lower right")
    for j in range(n, rows_n * cols):
        axes[j // cols][j % cols].axis("off")
    fig.suptitle("Per-camera die temperature during heat scans — "
                 f"x = dropout; red line = {TRIP_C:.0f} °C trip line", y=1.0)
    fig.tight_layout()
    fig.savefig(os.path.join(FIGS, "fig_die_temps.png"), dpi=150)
    plt.close(fig)


# ── 4. cooling curves ──────────────────────────────────────────────────────

def fig_cooling():
    fig, ax = plt.subplots(figsize=(8, 4))
    plotted = False
    for p in sorted(glob.glob(os.path.join(DATA, "cool_*", "cooling_curve.csv"))):
        name = os.path.basename(os.path.dirname(p))
        ts, temps = [], []
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    t = float(r["t_s"])
                    temp = float(r["imu_temp_c"])
                except (ValueError, KeyError):
                    continue
                if temp and temp > 1.0:
                    ts.append(t)
                    temps.append(temp)
        if len(ts) > 2:
            ax.plot(ts, temps, lw=1.4, label=name)
            plotted = True
    if not plotted:
        plt.close(fig)
        return
    ax.set_xlabel("cooling time (s)")
    ax.set_ylabel("IMU (board) temp (°C)")
    ax.set_title("Powered-cooling curves (IMU/board temperature)\n"
                 "Note: trip persisted through ALL of these — board temp is not the variable")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(FIGS, "fig_cooling_curves.png"), dpi=150)
    plt.close(fig)


def main():
    verdicts = load_verdicts()
    ev = dropout_events(verdicts)
    temps = [e["temp"] for e in ev]
    print(f"dropout events with temp: {len(temps)}")
    if temps:
        mean = sum(temps) / len(temps)
        var = sum((t - mean) ** 2 for t in temps) / len(temps)
        print(f"  die temp at drop: mean={mean:.1f} °C  sd={var ** 0.5:.2f}  "
              f"min={min(temps):.1f}  max={max(temps):.1f}")
    per_cam = defaultdict(list)
    for e in ev:
        per_cam[e["cam"]].append(e["temp"])
    for cam, ts in sorted(per_cam.items()):
        print(f"  {cam}: n={len(ts)} mean={sum(ts)/len(ts):.1f}")

    rows = recovery_trials(verdicts)
    print("\nrecovery trials (artifacts excluded):")
    for r in sorted(rows, key=lambda r: (r["mode"], r["dur_s"])):
        print(f"  c{r['cycle']:>2} {r['mode']:<18} {r['dur_s']:>6.0f}s -> "
              f"{r['n_ok']}/{r['n']}")

    fig_recovery(rows)
    fig_die_temps(verdicts)
    fig_cooling()
    print(f"\nfigures -> {FIGS}")


if __name__ == "__main__":
    main()
