#!/usr/bin/env python3
"""
Plot weighted average (μ) and standard deviation (σ) for cameras 0-7 in a
4 × 2 grid:
    Row 0 (top)    : cams 3 | 4
    Row 1          : cams 2 | 5
    Row 2          : cams 1 | 6
    Row 3 (bottom) : cams 0 | 7
"""

import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

CSV_FILE     = "histogram.csv"
NUM_BINS     = 1024
FRAME_ID_MAX = 256          # frame_id wraps 255 → 0

# Physical layout order (row-major, top-left → bottom-right)
CAMERA_ORDER = [3, 4, 2, 5, 1, 6, 0, 7]


def logical_frame_index(series: pd.Series) -> pd.Series:
    """Turn the raw frame_id series (0-255 rollover) into a monotonic index."""
    rollovers = (series.diff() < 0).cumsum()
    return rollovers * FRAME_ID_MAX + series


def cam_stats(cam_df: pd.DataFrame):
    """Return frame_ids, μ, σ and (optional) temperature array for one camera."""
    cam_df = cam_df.copy()
    cam_df["logical_frame_index"] = logical_frame_index(cam_df["frame_id"])
    cam_df.sort_values("logical_frame_index", inplace=True)

    histo = cam_df.iloc[:, 2 : 2 + NUM_BINS].to_numpy()
    histo[:, -1] = 0                      # zero-out bin 1023 if needed

    sums = histo.sum(axis=1)
    bins = np.arange(NUM_BINS)

    mu = np.divide(histo @ bins, sums, out=np.zeros_like(sums, float),
                   where=sums != 0)

    second_moment = np.divide(histo @ (bins ** 2), sums,
                              out=np.zeros_like(sums, float),
                              where=sums != 0)
    sigma = np.sqrt(np.clip(second_moment - mu ** 2, 0, None))

    temp = cam_df["temperature"].to_numpy() if "temperature" in cam_df else None
    frame_ids = cam_df["logical_frame_index"].to_numpy()

    return frame_ids, mu, sigma, temp


def main():
    df = pd.read_csv(CSV_FILE)

    fig, axes = plt.subplots(nrows=4, ncols=2, figsize=(12, 10), sharex=False)
    axes = axes.flatten()  # easier indexing

    for ax, cam_id in zip(axes, CAMERA_ORDER):
        cam_df = df[df["cam_id"] == cam_id]
        ax.set_title(f"Camera {cam_id}")

        if cam_df.empty:
            ax.text(0.5, 0.5, "No data", ha="center", va="center")
            ax.set_axis_off()
            continue

        frame_ids, mu, sigma, temp = cam_stats(cam_df)

        ln_mu, = ax.plot(frame_ids, mu,   "o-", markersize=3,
                         label="μ (weighted avg)")
        ln_sg, = ax.plot(frame_ids, sigma, "s--", markersize=3,
                         color="tab:orange", label="σ (std dev)")

        ax.set_ylabel("Bin index")
        ax.grid(True)

        lines, labels = [ln_mu, ln_sg], [ln_mu.get_label(), ln_sg.get_label()]

        if temp is not None:
            ax2 = ax.twinx()
            ln_tp, = ax2.plot(frame_ids, temp, "d-.", markersize=3,
                              color="tab:red", label="Temp (°C)")
            ax2.set_ylabel("Temperature (°C)")
            lines.append(ln_tp)
            labels.append(ln_tp.get_label())

        ax.legend(lines, labels, fontsize="x-small", loc="best")

    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
