#!/usr/bin/env python3
"""
Plot weighted‑average histogram position for *every* camera in a CSV.

CSV format assumed:
    frame_id, cam_id, 0, 1, …, 1023
"""

import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

CSV_FILE      = "histo_data_strickland_060325.csv"   # change as needed
NUM_BINS      = 1024
FRAME_ID_MAX  = 256               # frame_id wraps 255 → 0

# ───────────────────────────────────────────────────────── helpers
def logical_indices_for_camera(cam_df: pd.DataFrame) -> np.ndarray:
    """Return logical frame indices handling 8‑bit rollover (0‑255)."""
    indices, last_id, rolls = [], None, 0
    for fid in cam_df["id"]:
        if last_id is not None and fid < last_id:
            rolls += 1
        indices.append(rolls * FRAME_ID_MAX + fid)
        last_id = fid
    return np.asarray(indices)

# ───────────────────────────────────────────────────────── main
def plot_weighted_average_all(csv_path: str) -> None:
    df = pd.read_csv(csv_path)
    if df.empty:
        print("CSV is empty.")
        return

    unique_cams = sorted(df["cam_id"].unique())
    if not unique_cams:
        print("No cam_id column or no cameras found.")
        return

    plt.figure(figsize=(10, 4))
    bin_idx = np.arange(NUM_BINS)

    for cam_id in unique_cams:
        cam_df = df[df["cam_id"] == cam_id].copy()
        if cam_df.empty:
            continue

        # Logical frame index & sort
        cam_df["logical_frame_index"] = logical_indices_for_camera(cam_df)
        cam_df.sort_values("logical_frame_index", inplace=True)

        # Histogram matrix (bins start at column 2)
        histo_mat = cam_df.iloc[:, 2 : 2 + NUM_BINS].to_numpy()
        histo_mat[:, -1] = 0                       # zero out bin 1023

        sums          = histo_mat.sum(axis=1)
        weighted_sums = histo_mat @ bin_idx        # dot product
        w_avg = np.divide(weighted_sums, sums,
                          out=np.zeros_like(sums, dtype=float),
                          where=sums != 0)

        plt.plot(cam_df["logical_frame_index"], w_avg,
                 marker="o", markersize=3, linestyle="-",
                 label=f"cam {cam_id}")

    plt.title("Weighted Histogram Average vs. Frame (bin 1023 zeroed)")
    plt.xlabel("Logical Frame ID")
    plt.ylabel("Weighted Average Bin")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.show()

# ───────────────────────────────────────────────────────── runner
if __name__ == "__main__":
    plot_weighted_average_all(CSV_FILE)
