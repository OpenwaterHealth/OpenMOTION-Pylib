#!/usr/bin/env python3
"""
Plot ONE 1024‑bin histogram row (line, bar, or 1×1024 spectrogram strip).

New option:
    --log            Show counts on a logarithmic scale

    python plot_single_histogram_old.py --csv 20250207/histo_data_cam5_g1_light_0075um.csv --cam 4 --row 90 --style bar --log
"""

import argparse
import sys
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm     # <-- for spectro log scale

NUM_BINS   = 1024      # fixed by FPGA/STM32 format
DATA_COL0  = 1         # index of first histogram‑bin column

# ───────────────────────────────────────────────────────── CLI
def get_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Plot one histogram row.")
    p.add_argument("--csv",   required=True, help="Input CSV")
    p.add_argument("--cam",   type=int, default=0, help="Camera ID")
    p.add_argument("--row",   type=int, default=0,
                   help="Row index within that camera")
    p.add_argument("--style", choices=["line", "bar", "spectro"],
                   default="line", help="Plot style")
    p.add_argument("--log", action="store_true",
                   help="Use logarithmic intensity scale")
    return p.parse_args()

# ─────────────────────────────────────────────────────── main
def main() -> None:
    args = get_args()

    # ---------- load ----------------------------------------------------
    try:
        df = pd.read_csv(args.csv)
    except FileNotFoundError:
        sys.exit(f"File not found: {args.csv}")
    cam_df = df
    # cam_df = df[df["cam_id"] == args.cam].reset_index(drop=True)
    # if cam_df.empty:
    #     sys.exit(f"No data for camera {args.cam}")

    if not (0 <= args.row < len(cam_df)):
        sys.exit(f"Row {args.row} out of range (0–{len(cam_df)-1})")

    row  = cam_df.iloc[args.row]
    bins = row.iloc[DATA_COL0 : DATA_COL0 + NUM_BINS].astype(int).to_numpy()
    total = bins.sum()

    # If we’re going log and have zeros, bump them to 1 so log(0) is avoided
    if args.log:
        bins = np.where(bins == 0, 1, bins)

    # ---------- plot ----------------------------------------------------
    plt.figure(figsize=(10, 4))

    if args.style == "bar":
        plt.bar(range(NUM_BINS), bins, width=1.0)
        if args.log:
            plt.yscale("log")
        plt.xlabel("Bin")
        plt.ylabel("Count")

    elif args.style == "line":
        plt.plot(range(NUM_BINS), bins)
        if args.log:
            plt.yscale("log")
        plt.xlabel("Bin")
        plt.ylabel("Count")

    else:  # spectro
        norm = LogNorm(vmin=bins.min(), vmax=bins.max()) if args.log else None
        plt.imshow(np.expand_dims(bins, 0), aspect="auto", origin="lower",
                   cmap="viridis", norm=norm)
        plt.yticks([])
        plt.xlabel("Bin")
        cbar = plt.colorbar(pad=0.02)
        cbar.set_label("Count (log)" if args.log else "Count")

    plt.title(f"Cam {args.cam} • Row {args.row} • Total ≈ {total}")
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    main()
