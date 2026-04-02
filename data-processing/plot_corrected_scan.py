#!/usr/bin/env python3
"""
Plot BFI, BVI, mean, and temperature from a _corrected.csv file produced by
the OpenMOTION SDK.

Both sensor sides are shown in one figure.  The subplot grid mirrors the
physical camera layout described in docs/CameraArrangement.md:

    Col 0  Col 1  |  Col 2  Col 3
    ─────────────────────────────
    L:C1   L:C8   │  R:C1   R:C8   ← row 0 (top)
    L:C2   L:C7   │  R:C2   R:C7
    L:C3   L:C6   │  R:C3   R:C6
    L:C4   L:C5   │  R:C4   R:C5   ← row 3 (bottom)

Inactive cameras are omitted entirely.  Empty rows and columns that result
from cameras being inactive are collapsed so no whitespace is wasted.

Each subplot shows:
    Left  y-axis  — BFI (solid blue) and BVI (dashed green), scale 0–10
    Right y-axis  — Temperature (°C, dash-dot red)

Optional secondary figure (--show-signal) adds mean, std, and contrast.

Usage
-----
    python plot_corrected_scan.py --csv path/to/_corrected.csv
    python plot_corrected_scan.py --csv scan.csv --show-signal --save
"""

import argparse
import os
import sys

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Camera layout constants  (from docs/CameraArrangement.md)
# ---------------------------------------------------------------------------

# Camera number (1-indexed) → (grid_row, sensor_col) within one sensor's 4×2 grid
CAMERA_GRID_POS = {
    1: (0, 0),
    2: (1, 0),
    3: (2, 0),
    4: (3, 0),
    5: (3, 1),
    6: (2, 1),
    7: (1, 1),
    8: (0, 1),
}

# Sensor side → offset added to sensor_col to get the full plot-grid column
SENSOR_COL_OFFSET = {"left": 0, "right": 2}

SIDES = ("left", "right")


# ---------------------------------------------------------------------------
# Column name helpers
# ---------------------------------------------------------------------------

def _bfi(side, cam):      return f"bfi_{side[0]}{cam}"
def _bvi(side, cam):      return f"bvi_{side[0]}{cam}"
def _mean(side, cam):     return f"mean_{side[0]}{cam}"
def _std(side, cam):      return f"std_{side[0]}{cam}"
def _contrast(side, cam): return f"contrast_{side[0]}{cam}"
def _temp(side, cam):     return f"temp_{side[0]}{cam}"


# ---------------------------------------------------------------------------
# Grid helpers
# ---------------------------------------------------------------------------

def _active_cells(df: pd.DataFrame, sides: list[str]) -> list[tuple]:
    """
    Return (grid_row, plot_col, side, cam) for every camera that has data,
    in the order they appear in the physical layout.
    """
    cells = []
    for side in sides:
        for cam in range(1, 9):
            col = _bfi(side, cam)
            if col in df.columns and df[col].notna().any():
                grid_row, sensor_col = CAMERA_GRID_POS[cam]
                plot_col = sensor_col + SENSOR_COL_OFFSET[side]
                cells.append((grid_row, plot_col, side, cam))
    return cells


def _collapse(cells: list[tuple]) -> tuple[dict, dict, int, int]:
    """
    Collapse empty rows/columns and return
    (row_map, col_map, n_subplot_rows, n_subplot_cols).
    """
    active_rows = sorted({c[0] for c in cells})
    active_cols = sorted({c[1] for c in cells})
    row_map = {r: i for i, r in enumerate(active_rows)}
    col_map = {c: i for i, c in enumerate(active_cols)}
    return row_map, col_map, len(active_rows), len(active_cols)


def _requested_sides(df: pd.DataFrame, requested: str) -> list[str]:
    candidates = SIDES if requested == "both" else (requested,)
    return [
        s for s in candidates
        if any(
            _bfi(s, cam) in df.columns and df[_bfi(s, cam)].notna().any()
            for cam in range(1, 9)
        )
    ]


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Plot OpenMOTION corrected scan CSV")
    p.add_argument("--csv", required=True, help="Path to the _corrected.csv file")
    p.add_argument(
        "--sides", choices=["left", "right", "both"], default="both",
        help="Which sensor side(s) to plot (default: both)",
    )
    p.add_argument(
        "--show-signal", action="store_true",
        help="Also show a figure with corrected mean, std, and contrast",
    )
    p.add_argument(
        "--save", action="store_true",
        help="Save figures as PNG files next to the CSV instead of displaying",
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Figure builder
# ---------------------------------------------------------------------------

def _make_figure(
    df: pd.DataFrame,
    cells: list[tuple],
    row_map: dict,
    col_map: dict,
    n_rows: int,
    n_cols: int,
    *,
    mode: str,       # "bfi" or "signal"
    csv_path: str,
    active_sides: list[str],
) -> plt.Figure:
    """
    Build and return one figure.  mode="bfi" plots BFI/BVI/temperature;
    mode="signal" plots mean/std/contrast.
    """
    ts = df["timestamp_s"].to_numpy()

    fig_w = max(5 * n_cols, 8)
    fig_h = max(3.2 * n_rows, 5)
    fig, axes = plt.subplots(
        nrows=n_rows, ncols=n_cols,
        figsize=(fig_w, fig_h),
        sharex=True, squeeze=False,
    )

    # Hide every subplot; we'll re-enable only the active ones.
    for ax in axes.flat:
        ax.set_visible(False)

    for grid_row, plot_col, side, cam in cells:
        sr = row_map[grid_row]
        sc = col_map[plot_col]
        ax = axes[sr, sc]
        ax.set_visible(True)

        label = f"{'L' if side == 'left' else 'R'}:  Cam {cam}"
        ax.set_title(label, fontsize=9, pad=3)
        ax.grid(True, alpha=0.35)
        ax.tick_params(axis="both", labelsize=7)

        if mode == "bfi":
            bfi_vals = df[_bfi(side, cam)].to_numpy(dtype=float)
            bvi_vals = df[_bvi(side, cam)].to_numpy(dtype=float)
            ln1, = ax.plot(ts, bfi_vals, "-",  lw=1.3, color="tab:blue",  label="BFI")
            ln2, = ax.plot(ts, bvi_vals, "--", lw=1.0, color="tab:green", label="BVI")
            ax.set_ylabel("Index", fontsize=7)
            # Auto-scale to the actual data; add 5% padding so lines aren't flush with edges.
            combined = np.concatenate([bfi_vals[np.isfinite(bfi_vals)],
                                       bvi_vals[np.isfinite(bvi_vals)]])
            if combined.size:
                lo, hi = combined.min(), combined.max()
                pad = max((hi - lo) * 0.05, 0.2)
                ax.set_ylim(lo - pad, hi + pad)
            lines, labels = [ln1, ln2], ["BFI", "BVI"]

            temp_col = _temp(side, cam)
            if temp_col in df.columns and df[temp_col].notna().any():
                ax2 = ax.twinx()
                ax2.tick_params(axis="y", labelcolor="tab:red", labelsize=7)
                ax2.set_ylabel("Temp (°C)", fontsize=7, color="tab:red")
                ln3, = ax2.plot(
                    ts, df[temp_col].to_numpy(dtype=float),
                    "-.", lw=1.0, color="tab:red", label="Temp (°C)",
                )
                lines.append(ln3)
                labels.append("Temp (°C)")

        else:  # signal
            mean_vals     = df[_mean(side, cam)].to_numpy(dtype=float)
            std_vals      = df[_std(side, cam)].to_numpy(dtype=float)
            contrast_vals = df[_contrast(side, cam)].to_numpy(dtype=float)
            ln1, = ax.plot(ts, mean_vals, "-",  lw=1.3, color="tab:blue",   label="Mean (μ̃)")
            ln2, = ax.plot(ts, std_vals,  "--", lw=1.0, color="tab:orange", label="Std (σ̃)")
            ax.set_ylabel("Bin index", fontsize=7)
            lines, labels = [ln1, ln2], ["Mean (μ̃)", "Std (σ̃)"]

            ax2 = ax.twinx()
            ax2.tick_params(axis="y", labelcolor="tab:purple", labelsize=7)
            ax2.set_ylabel("Contrast", fontsize=7, color="tab:purple")
            ln3, = ax2.plot(
                ts, contrast_vals,
                "-.", lw=1.0, color="tab:purple", label="Contrast (K̃)",
            )
            lines.append(ln3)
            labels.append("Contrast (K̃)")

        ax.legend(lines, labels, fontsize=6, loc="best", framealpha=0.6)

    # x-axis labels on bottom row only (sharex handles the rest)
    for sc in range(n_cols):
        axes[n_rows - 1, sc].set_xlabel("Time (s)", fontsize=8)

    basename = os.path.splitext(os.path.basename(csv_path))[0]
    mode_label = "BFI / BVI / Temperature" if mode == "bfi" else "Mean / Std / Contrast"
    fig.suptitle(f"{basename}  —  {mode_label}", fontsize=11, fontweight="bold")

    # tight_layout must run before we read subplot positions for the headers.
    fig.tight_layout(rect=[0, 0, 1, 0.96])

    # Side header annotations — placed after layout is finalised.
    _add_side_headers(fig, axes, col_map, active_sides)

    return fig


def _add_side_headers(
    fig: plt.Figure,
    axes: np.ndarray,
    col_map: dict,
    active_sides: list[str],
) -> None:
    """
    Draw a centered "LEFT SENSOR" / "RIGHT SENSOR" label above each sensor's
    columns using figure-space coordinates.
    """
    # Collect the subplot column indices that belong to each side.
    side_subplot_cols: dict[str, list[int]] = {}
    for side in active_sides:
        offset = SENSOR_COL_OFFSET[side]
        cols_in_fig = [
            col_map[offset + sensor_col]
            for sensor_col in (0, 1)
            if (offset + sensor_col) in col_map
        ]
        if cols_in_fig:
            side_subplot_cols[side] = cols_in_fig

    n_rows, n_cols = axes.shape
    for side, sc_list in side_subplot_cols.items():
        # Average the x-centres of the relevant subplot columns in figure coords.
        x_centres = []
        for sc in sc_list:
            bbox = axes[0, sc].get_position()
            x_centres.append((bbox.x0 + bbox.x1) / 2)
        x_mid = sum(x_centres) / len(x_centres)
        top_y = axes[0, sc_list[0]].get_position().y1 + 0.01
        fig.text(
            x_mid, top_y,
            f"{'LEFT' if side == 'left' else 'RIGHT'} SENSOR",
            ha="center", va="bottom",
            fontsize=9, fontweight="bold", color="dimgray",
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()

    if not os.path.isfile(args.csv):
        print(f"ERROR: file not found: {args.csv}", file=sys.stderr)
        sys.exit(1)

    print(f"Reading: {args.csv}")
    df = pd.read_csv(args.csv)
    print(f"  {len(df)} rows, {len(df.columns)} columns")

    if "timestamp_s" not in df.columns:
        print("ERROR: 'timestamp_s' column not found — is this a _corrected.csv?",
              file=sys.stderr)
        sys.exit(1)

    active_sides = _requested_sides(df, args.sides)
    if not active_sides:
        print(f"ERROR: no data found for requested side(s): {args.sides}", file=sys.stderr)
        sys.exit(1)

    for side in active_sides:
        cams = [c for c in range(1, 9) if df[_bfi(side, c)].notna().any()
                if _bfi(side, c) in df.columns]
        print(f"  {side.capitalize()} side: cameras {cams}")

    cells = _active_cells(df, active_sides)
    row_map, col_map, n_rows, n_cols = _collapse(cells)
    print(f"  Grid: {n_rows} row(s) × {n_cols} col(s)")

    kwargs = dict(
        cells=cells, row_map=row_map, col_map=col_map,
        n_rows=n_rows, n_cols=n_cols,
        csv_path=args.csv, active_sides=active_sides,
    )

    fig_bfi = _make_figure(df, mode="bfi", **kwargs)

    if args.save:
        out = os.path.splitext(args.csv)[0] + "_bfi.png"
        fig_bfi.savefig(out, dpi=150, bbox_inches="tight")
        print(f"  Saved: {out}")

    if args.show_signal:
        fig_sig = _make_figure(df, mode="signal", **kwargs)
        if args.save:
            out = os.path.splitext(args.csv)[0] + "_signal.png"
            fig_sig.savefig(out, dpi=150, bbox_inches="tight")
            print(f"  Saved: {out}")

    if not args.save:
        plt.show()


if __name__ == "__main__":
    main()
