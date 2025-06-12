#!/usr/bin/env python3
"""
Plot ONE 1024‑bin histogram (row) from our standard CSV, with three styles:
  • line      – fast line plot      (default)
  • bar       – classic bar chart
  • spectro   – 1×1024 ‘spectrogram strip’ via imshow
CSV format assumed: id, cam_id, total, 0 … 1023
"""

import argparse
import sys
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

#python plot_single_histogram.py --csv histogram.csv --cam 2 --row 444 --style bar

NUM_BINS   = 1024   # fixed by the FPGA/STM32 format
DATA_COL0  = 2      # first histogram‑bin column index
# ────────────────────────────────────────────────────────────── CLI ──
def get_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Plot one histogram row.")
    p.add_argument("--csv",   required=True, help="Path to the CSV file")
    p.add_argument("--cam",   type=int, default=0,
                   help="Camera ID to filter (default 0)")
    p.add_argument("--row",   type=int, default=0,
                   help="Logical row index *within that camera* (default 0)")
    p.add_argument("--style", choices=["line", "bar", "spectro"], default="line",
                   help="Plot style (default: line)")
    return p.parse_args()

# ─────────────────────────────────────────────────────────── main ──
def main() -> None:
    args = get_args()

    # --- load ------------------------------------------------------------
    try:
        df = pd.read_csv(args.csv)
    except FileNotFoundError:
        sys.exit(f"File not found: {args.csv}")

    cam_df = df[df["cam_id"] == args.cam].reset_index(drop=True)
    if cam_df.empty:
        sys.exit(f"No data for camera {args.cam}.")

    if not (0 <= args.row < len(cam_df)):
        sys.exit(f"Row {args.row} out of range (0–{len(cam_df)-1}).")

    row = cam_df.iloc[args.row]
    bins = row.iloc[DATA_COL0 : DATA_COL0 + NUM_BINS].astype(int).to_numpy()
    bins[1023] = 0
    # --- plot ------------------------------------------------------------
    plt.figure(figsize=(10, 4))

    if args.style == "bar":
        plt.bar(range(NUM_BINS), bins, width=1.0)
        plt.xlabel("Bin")
        plt.ylabel("Count")

    elif args.style == "line":
        plt.plot(range(NUM_BINS), bins)
        plt.xlabel("Bin")
        plt.ylabel("Count")

    else:                       # spectro
        # 1×N array → imshow for a colored strip
        plt.imshow(np.expand_dims(bins, 0), aspect="auto",
                   cmap="viridis", origin="lower")
        plt.yticks([])          # hide the single y‑tick
        plt.xlabel("Bin")
        cbar = plt.colorbar(pad=0.02)
        cbar.set_label("Count")

    total = bins.sum()
    plt.title(f"Cam {args.cam} • Row {args.row} • Total ≈ {total}")
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    main()
