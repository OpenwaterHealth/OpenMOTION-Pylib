#!/usr/bin/env python3
"""
plot_telemetry.py — visualise a ConsoleTelemetry CSV produced by ScanWorkflow.

Usage:
    python scripts/plot_telemetry.py <telemetry_csv>
    python scripts/plot_telemetry.py            # auto-finds newest *_telemetry.csv

Outputs a PNG alongside the CSV (e.g. scan_…_telemetry.png) and optionally
shows an interactive window (pass --show to enable).
"""

import argparse
import glob
import os
import sys

import matplotlib
# Choose the backend before pyplot is imported.  We do a lightweight pre-parse
# of sys.argv here so the decision is made at import time rather than too late.
_want_show = "--show" in sys.argv
matplotlib.use("TkAgg" if _want_show else "Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import pandas as pd


# ---------------------------------------------------------------------------
# Column groups
# ---------------------------------------------------------------------------

TEMP_COLS   = ["tcm", "tcl", "pdc"]
TEC_COLS    = ["tec_v_raw", "tec_set_raw", "tec_curr_raw", "tec_volt_raw"]
PDU_RAW_COLS  = [f"pdu_raw_{i}"  for i in range(16)]
PDU_VOLT_COLS = [f"pdu_volt_{i}" for i in range(16)]
FLAG_COLS   = ["tec_good", "safety_se", "safety_so", "safety_ok", "read_ok"]


def load(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    # Compute relative time in seconds from the first sample.
    if "timestamp" in df.columns:
        t0 = df["timestamp"].iloc[0]
        df["t"] = df["timestamp"] - t0
    else:
        df["t"] = range(len(df))
    return df


def plot(df: pd.DataFrame, title: str) -> plt.Figure:
    fig = plt.figure(figsize=(16, 22))
    fig.suptitle(title, fontsize=12, y=0.995)

    gs = gridspec.GridSpec(
        5, 1,
        figure=fig,
        hspace=0.45,
        top=0.97,
        bottom=0.04,
        left=0.08,
        right=0.97,
    )

    t = df["t"]

    # ------------------------------------------------------------------
    # 1. Temperatures: tcm, tcl, pdc
    # ------------------------------------------------------------------
    ax1 = fig.add_subplot(gs[0])
    for col in TEMP_COLS:
        if col in df.columns:
            ax1.plot(t, df[col], label=col, linewidth=1.5)
    ax1.set_title("Temperatures")
    ax1.set_ylabel("Raw counts / °C")
    ax1.set_xlabel("Time (s)")
    ax1.legend(loc="upper right", fontsize=8)
    ax1.grid(True, alpha=0.3)

    # ------------------------------------------------------------------
    # 2. TEC values
    # ------------------------------------------------------------------
    ax2 = fig.add_subplot(gs[1])
    for col in TEC_COLS:
        if col in df.columns:
            ax2.plot(t, df[col], label=col, linewidth=1.5)
    ax2.set_title("TEC Raw Values")
    ax2.set_ylabel("Raw counts")
    ax2.set_xlabel("Time (s)")
    ax2.legend(loc="upper right", fontsize=8, ncol=2)
    ax2.grid(True, alpha=0.3)

    # ------------------------------------------------------------------
    # 3. PDU raw counts (16 channels)
    # ------------------------------------------------------------------
    ax3 = fig.add_subplot(gs[2])
    cmap = plt.cm.tab20
    for i, col in enumerate(PDU_RAW_COLS):
        if col in df.columns:
            ax3.plot(t, df[col], label=f"ch{i}", linewidth=1,
                     color=cmap(i / 16), alpha=0.85)
    ax3.set_title("PDU Raw Counts (16 channels)")
    ax3.set_ylabel("Raw counts")
    ax3.set_xlabel("Time (s)")
    ax3.legend(loc="upper right", fontsize=7, ncol=4)
    ax3.grid(True, alpha=0.3)

    # ------------------------------------------------------------------
    # 4. PDU calibrated voltages (16 channels)
    # ------------------------------------------------------------------
    ax4 = fig.add_subplot(gs[3])
    for i, col in enumerate(PDU_VOLT_COLS):
        if col in df.columns:
            ax4.plot(t, df[col], label=f"ch{i}", linewidth=1,
                     color=cmap(i / 16), alpha=0.85)
    ax4.set_title("PDU Calibrated Voltages (16 channels)")
    ax4.set_ylabel("Volts")
    ax4.set_xlabel("Time (s)")
    ax4.legend(loc="upper right", fontsize=7, ncol=4)
    ax4.grid(True, alpha=0.3)

    # ------------------------------------------------------------------
    # 5. Status / flag bits
    # ------------------------------------------------------------------
    ax5 = fig.add_subplot(gs[4])
    offsets = {col: i for i, col in enumerate(FLAG_COLS)}
    for col, offset in offsets.items():
        if col in df.columns:
            vals = pd.to_numeric(df[col], errors="coerce").fillna(0)
            ax5.step(t, vals + offset * 1.5, where="post",
                     label=col, linewidth=1.5)
    ax5.set_title("Status Flags")
    ax5.set_xlabel("Time (s)")
    ax5.set_yticks([i * 1.5 for i in range(len(FLAG_COLS))])
    ax5.set_yticklabels(FLAG_COLS, fontsize=8)
    ax5.set_ylim(-0.5, len(FLAG_COLS) * 1.5)
    ax5.grid(True, axis="x", alpha=0.3)

    return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", nargs="?",
                        help="Path to a *_telemetry.csv file, or a folder to search "
                             "for the newest *_telemetry.csv inside it. "
                             "Omit to auto-find the newest one recursively.")
    parser.add_argument("--show", action="store_true",
                        help="Open an interactive matplotlib window after saving.")
    args = parser.parse_args()

    csv_path = args.csv
    if csv_path is not None and os.path.isdir(csv_path):
        # Caller handed us a folder — find the newest *_telemetry.csv inside it.
        candidates = sorted(glob.glob(os.path.join(csv_path, "*_telemetry.csv")),
                            key=os.path.getmtime, reverse=True)
        if not candidates:
            print(f"No *_telemetry.csv files found in: {csv_path}", file=sys.stderr)
            sys.exit(1)
        csv_path = candidates[0]
        print(f"Auto-selected: {csv_path}")
    elif csv_path is None:
        candidates = sorted(glob.glob("**/*_telemetry.csv", recursive=True),
                            key=os.path.getmtime, reverse=True)
        if not candidates:
            print("No *_telemetry.csv found. Pass a path explicitly.", file=sys.stderr)
            sys.exit(1)
        csv_path = candidates[0]
        print(f"Auto-selected: {csv_path}")

    if not os.path.isfile(csv_path):
        print(f"File not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    df = load(csv_path)
    title = os.path.basename(csv_path)

    fig = plot(df, title)

    out_path = os.path.splitext(csv_path)[0] + ".png"
    fig.savefig(out_path, dpi=150)
    print(f"Saved: {out_path}")

    if args.show:
        plt.show()


if __name__ == "__main__":
    main()
