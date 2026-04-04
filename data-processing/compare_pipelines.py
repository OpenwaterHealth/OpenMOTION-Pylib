#!/usr/bin/env python3
"""
Compare BFI/BVI output between:
  1. The legacy VisualizeBloodflow pipeline (visualize_bloodflow.py)
  2. The current SciencePipeline (omotion/MotionProcessing.py)

Two modes:

  Raw mode (re-runs both pipelines from raw histogram CSVs):
    python data-processing/compare_pipelines.py --left L.csv --right R.csv [--save]

  Precomputed mode (compares already-generated output files):
    python data-processing/compare_pipelines.py \\
        --bfi-results path/to/_bfi_results.csv \\
        --corrected   path/to/_corrected.csv   [--save]

  Defaults (raw mode) use the perf-test fixture CSVs.
"""

import argparse
import os
import sys
import threading
from collections import defaultdict

import numpy as np
import matplotlib.pyplot as plt
import pandas as pd

# -----------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BLOODFLOW_APP = os.path.abspath(os.path.join(REPO_ROOT, "..", "openmotion-bloodflow-app"))
FIXTURES_DIR = os.path.join(REPO_ROOT, "tests", "fixtures")

DEFAULT_LEFT  = os.path.join(FIXTURES_DIR, "scan_owC18EHALL_20251217_160949_left_maskFF.csv")
DEFAULT_RIGHT = os.path.join(FIXTURES_DIR, "scan_owC18EHALL_20251217_160949_right_maskFF.csv")

sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, BLOODFLOW_APP)

from omotion.MotionProcessing import (
    CorrectedBatch,
    Sample,
    create_science_pipeline,
    feed_pipeline_from_csv,
)
from processing.visualize_bloodflow import VisualizeBloodflow


# -----------------------------------------------------------------------
# Constants (match what the bloodflow app uses)
# -----------------------------------------------------------------------

_CAL = VisualizeBloodflow(left_csv="", right_csv="")
BFI_C_MIN = _CAL.C_min
BFI_C_MAX = _CAL.C_max
BFI_I_MIN = _CAL.I_min
BFI_I_MAX = _CAL.I_max

# Pipeline parameters
DARK_INTERVAL = 600
DISCARD_COUNT = 9
NOISE_FLOOR   = 10

CAPTURE_HZ = 40.0


# -----------------------------------------------------------------------
# Run legacy VisualizeBloodflow
# -----------------------------------------------------------------------

def run_legacy(left_csv: str, right_csv: str | None) -> dict[tuple, dict]:
    """
    Returns {(side, cam_id_0indexed): {"frame": array, "bfi": array, ...}}
    Frame indices are the raw (unwrapped, 1-indexed) frame IDs used internally.
    """
    viz = VisualizeBloodflow(
        left_csv=left_csv,
        right_csv=right_csv,
        dark_interval=DARK_INTERVAL,
        noisy_bin_min=NOISE_FLOOR,
    )
    viz.compute()
    bfi, bvi, camera_inds, contrast, mean = viz.get_results()

    # bfi/bvi/contrast/mean shape: (ncams, nframes)
    nframes = bfi.shape[1]
    # Frame axis corresponds to time indices starting from frame 1
    # (visualize_bloodflow removes the first and last dark frames, so
    # the output spans from the first non-dark frame through to the last non-dark frame).
    # We reconstruct absolute frame numbers: output index 0 = absolute frame 1.
    abs_frames = np.arange(1, nframes + 1)

    results = {}
    for idx, cam_id in enumerate(camera_inds):
        side = viz._sides[idx]
        key = (side, int(cam_id))
        results[key] = {
            "abs_frame": abs_frames,
            "bfi":      bfi[idx],
            "bvi":      bvi[idx],
            "contrast": contrast[idx],
            "mean":     mean[idx],
        }
    return results


# -----------------------------------------------------------------------
# Run current SciencePipeline
# -----------------------------------------------------------------------

def run_sdk(left_csv: str, right_csv: str | None) -> dict[tuple, dict]:
    """
    Returns {(side, cam_id_0indexed): {"abs_frame": array, "bfi": array, ...}}
    Only corrected samples are returned (one per dark interval per camera).
    """
    lock = threading.Lock()
    batches: list[CorrectedBatch] = []

    def on_batch(b: CorrectedBatch) -> None:
        with lock:
            batches.append(b)

    right_mask = 0xFF if right_csv else 0x00
    pipeline = create_science_pipeline(
        left_camera_mask=0xFF,
        right_camera_mask=right_mask,
        bfi_c_min=BFI_C_MIN,
        bfi_c_max=BFI_C_MAX,
        bfi_i_min=BFI_I_MIN,
        bfi_i_max=BFI_I_MAX,
        on_corrected_batch_fn=on_batch,
        dark_interval=DARK_INTERVAL,
        discard_count=DISCARD_COUNT,
        noise_floor=NOISE_FLOOR,
    )

    feed_pipeline_from_csv(left_csv, "left", pipeline)
    if right_csv:
        feed_pipeline_from_csv(right_csv, "right", pipeline)
    pipeline.stop(timeout=120.0)

    # Aggregate per (side, cam_id)
    per_cam: dict[tuple, list[Sample]] = defaultdict(list)
    for batch in batches:
        for s in batch.samples:
            per_cam[(s.side, s.cam_id)].append(s)

    results = {}
    for key, samples in per_cam.items():
        samples.sort(key=lambda s: s.absolute_frame_id)
        results[key] = {
            "abs_frame": np.array([s.absolute_frame_id for s in samples]),
            "bfi":       np.array([s.bfi      for s in samples]),
            "bvi":       np.array([s.bvi      for s in samples]),
            "contrast":  np.array([s.contrast for s in samples]),
            "mean":      np.array([s.mean     for s in samples]),
        }
    return results


# -----------------------------------------------------------------------
# Align two result dicts on shared absolute frame IDs
# -----------------------------------------------------------------------

def align(legacy: dict, sdk: dict, key: tuple) -> dict | None:
    """
    Return a dict with aligned arrays for a single camera key, or None if either
    source has no data for that camera.
    """
    if key not in legacy or key not in sdk:
        return None

    leg = legacy[key]
    new = sdk[key]

    # Find common frames
    leg_frames = set(leg["abs_frame"].tolist())
    sdk_frames = set(new["abs_frame"].tolist())
    common = sorted(leg_frames & sdk_frames)
    if not common:
        return None
    common = np.array(common)

    def _select(d, frames):
        idx = np.isin(d["abs_frame"], frames)
        return {k: v[idx] for k, v in d.items()}

    la = _select(leg, common)
    sa = _select(new, common)

    return {"abs_frame": common, "legacy": la, "sdk": sa}


# -----------------------------------------------------------------------
# Summary statistics
# -----------------------------------------------------------------------

def _stats(arr: np.ndarray) -> str:
    if arr.size == 0:
        return "  (no data)"
    return (f"  mean={np.mean(arr):+.4f}  std={np.std(arr):.4f}  "
            f"min={np.min(arr):+.4f}  max={np.max(arr):+.4f}")


def print_comparison(legacy: dict, sdk: dict) -> None:
    all_keys = sorted(set(legacy) | set(sdk))

    print("\n" + "=" * 72)
    print("  Pipeline comparison  —  per camera")
    print("=" * 72)

    for key in all_keys:
        side, cam = key
        label = f"{'LEFT' if side == 'left' else 'RIGHT'} cam {cam + 1}"
        print(f"\n  {label}")

        a = align(legacy, sdk, key)
        if a is None:
            if key not in legacy:
                print("    Legacy: no data")
            if key not in sdk:
                print("    SDK:    no data")
            continue

        n = len(a["abs_frame"])
        print(f"    Aligned frames: {n}  "
              f"(legacy total {len(legacy[key]['abs_frame'])}, "
              f"sdk total {len(sdk[key]['abs_frame'])})")

        for metric in ("bfi", "bvi", "contrast", "mean"):
            leg_v = a["legacy"][metric]
            sdk_v = a["sdk"][metric]
            diff  = sdk_v - leg_v
            print(f"    {metric.upper():<10}")
            print(f"      Legacy : {_stats(leg_v)}")
            print(f"      SDK    : {_stats(sdk_v)}")
            print(f"      Diff   : {_stats(diff)}")

    print("\n" + "=" * 72)


# -----------------------------------------------------------------------
# Plot
# -----------------------------------------------------------------------

METRICS = [
    ("bfi",      "BFI",       "tab:blue"),
    ("bvi",      "BVI",       "tab:green"),
    ("contrast", "Contrast K","tab:orange"),
    ("mean",     "Mean μ₁",   "tab:purple"),
]


def plot_comparison(legacy: dict, sdk: dict, *, save: bool, out_prefix: str) -> None:
    all_keys = sorted(set(legacy) | set(sdk))

    for metric, metric_label, color in METRICS:
        keys_with_data = [k for k in all_keys if align(legacy, sdk, k) is not None]
        if not keys_with_data:
            continue

        ncols = min(4, len(keys_with_data))
        nrows = (len(keys_with_data) + ncols - 1) // ncols

        fig, axes = plt.subplots(nrows, ncols,
                                 figsize=(5 * ncols, 3.5 * nrows),
                                 squeeze=False, sharex=False)

        for ax in axes.flat:
            ax.set_visible(False)

        for idx, key in enumerate(keys_with_data):
            row, col = divmod(idx, ncols)
            ax = axes[row, col]
            ax.set_visible(True)

            a = align(legacy, sdk, key)
            t_leg = a["legacy"]["abs_frame"] / CAPTURE_HZ
            t_sdk = a["sdk"]["abs_frame"] / CAPTURE_HZ

            ax.plot(t_leg, a["legacy"][metric], color=color,    lw=1.2,
                    label="Legacy", alpha=0.85)
            ax.plot(t_sdk, a["sdk"][metric],    color="tab:red", lw=1.0,
                    linestyle="--", label="SDK", alpha=0.85)

            side, cam = key
            ax.set_title(f"{'L' if side == 'left' else 'R'}:Cam{cam+1}", fontsize=9)
            ax.grid(True, alpha=0.3)
            ax.tick_params(labelsize=7)
            ax.set_xlabel("Time (s)", fontsize=7)
            ax.set_ylabel(metric_label, fontsize=7)
            ax.legend(fontsize=6, loc="best")

        fig.suptitle(f"Pipeline comparison — {metric_label}", fontsize=12, fontweight="bold")
        fig.tight_layout(rect=[0, 0, 1, 0.96])

        if save:
            path = f"{out_prefix}_{metric}.png"
            fig.savefig(path, dpi=150, bbox_inches="tight")
            print(f"  Saved: {path}")

    # Difference plots (SDK − Legacy) for BFI and contrast
    for metric, metric_label, color in [("bfi", "BFI", "tab:blue"),
                                         ("contrast", "Contrast", "tab:orange")]:
        keys_with_data = [k for k in all_keys if align(legacy, sdk, k) is not None]
        if not keys_with_data:
            continue

        ncols = min(4, len(keys_with_data))
        nrows = (len(keys_with_data) + ncols - 1) // ncols
        fig, axes = plt.subplots(nrows, ncols,
                                 figsize=(5 * ncols, 3.5 * nrows),
                                 squeeze=False)
        for ax in axes.flat:
            ax.set_visible(False)

        for idx, key in enumerate(keys_with_data):
            row, col = divmod(idx, ncols)
            ax = axes[row, col]
            ax.set_visible(True)

            a = align(legacy, sdk, key)
            diff = a["sdk"][metric] - a["legacy"][metric]
            t    = a["abs_frame"] / CAPTURE_HZ

            ax.axhline(0, color="gray", lw=0.8, linestyle=":")
            ax.plot(t, diff, color=color, lw=1.0)
            ax.fill_between(t, diff, alpha=0.25, color=color)

            side, cam = key
            ax.set_title(f"{'L' if side == 'left' else 'R'}:Cam{cam+1}  Δ{metric_label}", fontsize=9)
            ax.grid(True, alpha=0.3)
            ax.tick_params(labelsize=7)
            ax.set_xlabel("Time (s)", fontsize=7)
            ax.set_ylabel(f"SDK − Legacy", fontsize=7)

        fig.suptitle(f"SDK − Legacy: {metric_label}", fontsize=12, fontweight="bold")
        fig.tight_layout(rect=[0, 0, 1, 0.96])

        if save:
            path = f"{out_prefix}_diff_{metric}.png"
            fig.savefig(path, dpi=150, bbox_inches="tight")
            print(f"  Saved: {path}")

    if not save:
        plt.show()


# -----------------------------------------------------------------------
# Precomputed mode: load already-generated output files
# -----------------------------------------------------------------------

def load_legacy_precomputed(bfi_results_csv: str) -> dict[tuple, dict]:
    """
    Load a _bfi_results.csv written by VisualizeBloodflow.save_results_csv().
    Format: camera, side, time_s, BFI, BVI  (camera is 1-indexed in the CSV)

    Returns {(side, cam_id_0indexed): {"abs_frame", "bfi", "bvi"}}
    where cam_id_0indexed = camera - 1.
    """
    df = pd.read_csv(bfi_results_csv)
    results = {}
    for (side, cam), grp in df.groupby(["side", "camera"]):
        grp = grp.sort_values("time_s")
        cam_id = int(cam)   # keep as raw cam_id to match SDK keys
        abs_frames = np.round(grp["time_s"].values * CAPTURE_HZ).astype(int)
        results[(side, cam_id)] = {
            "abs_frame": abs_frames,
            "bfi":       grp["BFI"].values.astype(float),
            "bvi":       grp["BVI"].values.astype(float),
            "contrast":  np.full(len(grp), np.nan),   # not in output
            "mean":      np.full(len(grp), np.nan),
        }
    return results


def load_sdk_precomputed(corrected_csv: str) -> dict[tuple, dict]:
    """
    Load a _corrected.csv written by the SDK SciencePipeline streaming writer.
    Format: frame_id, timestamp_s, bfi_l1..r8, bvi_l1..r8, mean_l1..r8,
            std_l1..r8, contrast_l1..r8, temp_l1..r8

    Column suffix is cam_id+1 (1-indexed), so bfi_l2 → side=left, cam_id=1.

    Returns {(side, cam_id_0indexed): {"abs_frame", "bfi", "bvi", "contrast", "mean"}}
    """
    df = pd.read_csv(corrected_csv)
    results = {}

    for side_letter, side_name in [("l", "left"), ("r", "right")]:
        for col_num in range(1, 9):          # 1-indexed suffix in column name
            cam_id_0 = col_num - 1           # 0-indexed cam_id
            bfi_col  = f"bfi_{side_letter}{col_num}"
            bvi_col  = f"bvi_{side_letter}{col_num}"
            mean_col = f"mean_{side_letter}{col_num}"
            con_col  = f"contrast_{side_letter}{col_num}"

            if bfi_col not in df.columns:
                continue
            mask = df[bfi_col].notna()
            if not mask.any():
                continue

            sub = df[mask].copy()
            results[(side_name, cam_id_0)] = {
                "abs_frame": sub["frame_id"].values.astype(int),
                "bfi":       sub[bfi_col].values.astype(float),
                "bvi":       sub[bvi_col].values.astype(float) if bvi_col in df.columns else np.full(mask.sum(), np.nan),
                "contrast":  sub[con_col].values.astype(float) if con_col in df.columns else np.full(mask.sum(), np.nan),
                "mean":      sub[mean_col].values.astype(float) if mean_col in df.columns else np.full(mask.sum(), np.nan),
            }
    return results


def align_by_time(legacy: dict, sdk: dict, key: tuple,
                  tol_frames: float = 0.6) -> dict | None:
    """
    Align legacy and SDK results for one camera by nearest time match.

    Legacy uses time_s = frame_index/40Hz; SDK uses frame_id (absolute frame number).
    We convert legacy time_s → frame number and match within tol_frames.
    """
    if key not in legacy or key not in sdk:
        return None

    leg = legacy[key]
    sdk_ = sdk[key]

    # Build a lookup from SDK frame_id → index
    sdk_frames = sdk_["abs_frame"]
    sdk_lookup = {f: i for i, f in enumerate(sdk_frames)}

    # For each legacy frame, find closest SDK frame
    leg_idx, sdk_idx = [], []
    for li, lf in enumerate(leg["abs_frame"]):
        # Check exact or ±1 frame
        for offset in (0, 1, -1):
            if lf + offset in sdk_lookup:
                leg_idx.append(li)
                sdk_idx.append(sdk_lookup[lf + offset])
                break

    if not leg_idx:
        return None

    leg_idx = np.array(leg_idx)
    sdk_idx = np.array(sdk_idx)

    def _sel(d, idx):
        return {k: v[idx] for k, v in d.items()}

    return {
        "abs_frame": leg["abs_frame"][leg_idx],
        "legacy":    _sel(leg, leg_idx),
        "sdk":       _sel(sdk_, sdk_idx),
    }


def print_comparison_precomputed(legacy: dict, sdk: dict) -> None:
    """Like print_comparison but uses align_by_time and skips nan metrics."""
    all_keys = sorted(set(legacy) | set(sdk))

    print("\n" + "=" * 72)
    print("  Pipeline comparison (precomputed)  —  per camera")
    print("=" * 72)

    for key in all_keys:
        side, cam = key
        label = f"{'LEFT' if side == 'left' else 'RIGHT'} cam_id={cam}"
        print(f"\n  {label}")

        a = align_by_time(legacy, sdk, key)
        if a is None:
            if key not in legacy: print("    Legacy: no data")
            if key not in sdk:    print("    SDK:    no data")
            continue

        n = len(a["abs_frame"])
        print(f"    Aligned frames: {n}  "
              f"(legacy total {len(legacy[key]['abs_frame'])}, "
              f"sdk total {len(sdk[key]['abs_frame'])})")

        for metric in ("bfi", "bvi", "contrast", "mean"):
            leg_v = a["legacy"][metric]
            sdk_v = a["sdk"][metric]
            # Skip if both are all-NaN (not in precomputed output)
            if np.all(np.isnan(leg_v)) and np.all(np.isnan(sdk_v)):
                continue
            diff = sdk_v - leg_v
            print(f"    {metric.upper():<10}")
            if not np.all(np.isnan(leg_v)):
                print(f"      Legacy : {_stats(leg_v[~np.isnan(leg_v)])}")
            else:
                print(f"      Legacy : (not available)")
            if not np.all(np.isnan(sdk_v)):
                print(f"      SDK    : {_stats(sdk_v[~np.isnan(sdk_v)])}")
            else:
                print(f"      SDK    : (not available)")
            valid = ~(np.isnan(leg_v) | np.isnan(sdk_v))
            if valid.any():
                print(f"      Diff   : {_stats(diff[valid])}")

    print("\n" + "=" * 72)


def plot_comparison_precomputed(legacy: dict, sdk: dict, *,
                                save: bool, out_prefix: str) -> None:
    """Like plot_comparison but uses align_by_time."""
    all_keys = sorted(set(legacy) | set(sdk))

    for metric, metric_label, color in METRICS:
        keys_with_data = [k for k in all_keys
                          if align_by_time(legacy, sdk, k) is not None]
        if not keys_with_data:
            continue

        # Skip if metric is all-NaN in both
        valid_keys = []
        for k in keys_with_data:
            a = align_by_time(legacy, sdk, k)
            if not (np.all(np.isnan(a["legacy"][metric])) and
                    np.all(np.isnan(a["sdk"][metric]))):
                valid_keys.append(k)
        if not valid_keys:
            continue

        ncols = min(4, len(valid_keys))
        nrows = (len(valid_keys) + ncols - 1) // ncols
        fig, axes = plt.subplots(nrows, ncols,
                                 figsize=(5 * ncols, 3.5 * nrows),
                                 squeeze=False)
        for ax in axes.flat:
            ax.set_visible(False)

        for idx, key in enumerate(valid_keys):
            row, col = divmod(idx, ncols)
            ax = axes[row, col]
            ax.set_visible(True)

            a = align_by_time(legacy, sdk, key)
            t = a["abs_frame"] / CAPTURE_HZ

            leg_v = a["legacy"][metric]
            sdk_v = a["sdk"][metric]

            if not np.all(np.isnan(leg_v)):
                ax.plot(t, leg_v, color=color,     lw=1.2, label="Legacy", alpha=0.85)
            if not np.all(np.isnan(sdk_v)):
                ax.plot(t, sdk_v, color="tab:red", lw=1.0, linestyle="--",
                        label="SDK", alpha=0.85)

            side, cam = key
            ax.set_title(f"{'L' if side == 'left' else 'R'}:cam_id={cam}", fontsize=9)
            ax.grid(True, alpha=0.3)
            ax.tick_params(labelsize=7)
            ax.set_xlabel("Time (s)", fontsize=7)
            ax.set_ylabel(metric_label, fontsize=7)
            ax.legend(fontsize=6, loc="best")

        fig.suptitle(f"Pipeline comparison — {metric_label}", fontsize=12, fontweight="bold")
        fig.tight_layout(rect=[0, 0, 1, 0.96])

        if save:
            path = f"{out_prefix}_{metric}.png"
            fig.savefig(path, dpi=150, bbox_inches="tight")
            print(f"  Saved: {path}")

    # Difference plot (BFI only — most meaningful)
    keys_with_data = [k for k in all_keys if align_by_time(legacy, sdk, k) is not None]
    valid_keys = [k for k in keys_with_data
                  if not (np.all(np.isnan(align_by_time(legacy, sdk, k)["legacy"]["bfi"])) or
                          np.all(np.isnan(align_by_time(legacy, sdk, k)["sdk"]["bfi"])))]
    if valid_keys:
        ncols = min(4, len(valid_keys))
        nrows = (len(valid_keys) + ncols - 1) // ncols
        fig, axes = plt.subplots(nrows, ncols,
                                 figsize=(5 * ncols, 3.5 * nrows),
                                 squeeze=False)
        for ax in axes.flat:
            ax.set_visible(False)

        for idx, key in enumerate(valid_keys):
            row, col = divmod(idx, ncols)
            ax = axes[row, col]
            ax.set_visible(True)

            a = align_by_time(legacy, sdk, key)
            t    = a["abs_frame"] / CAPTURE_HZ
            diff = a["sdk"]["bfi"] - a["legacy"]["bfi"]

            ax.axhline(0, color="gray", lw=0.8, linestyle=":")
            ax.plot(t, diff, color="tab:blue", lw=1.0)
            ax.fill_between(t, diff, alpha=0.25, color="tab:blue")

            side, cam = key
            ax.set_title(f"{'L' if side == 'left' else 'R'}:cam_id={cam}  ΔBFI", fontsize=9)
            ax.grid(True, alpha=0.3)
            ax.tick_params(labelsize=7)
            ax.set_xlabel("Time (s)", fontsize=7)
            ax.set_ylabel("SDK − Legacy", fontsize=7)

        fig.suptitle("SDK − Legacy: BFI", fontsize=12, fontweight="bold")
        fig.tight_layout(rect=[0, 0, 1, 0.96])
        if save:
            path = f"{out_prefix}_diff_bfi.png"
            fig.savefig(path, dpi=150, bbox_inches="tight")
            print(f"  Saved: {path}")

    if not save:
        plt.show()


# -----------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compare legacy vs SDK pipeline outputs")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--left", default=DEFAULT_LEFT,
                   help="Left raw histogram CSV (raw mode, default: perf test fixture)")
    p.add_argument("--right", default=DEFAULT_RIGHT,
                   help="Right raw histogram CSV (raw mode, pass '' to skip)")
    p.add_argument("--bfi-results",
                   help="Pre-computed _bfi_results.csv from VisualizeBloodflow (precomputed mode)")
    p.add_argument("--corrected",
                   help="Pre-computed _corrected.csv from SDK pipeline (precomputed mode)")
    p.add_argument("--save", action="store_true", help="Save PNGs instead of showing")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    precomputed_mode = bool(args.bfi_results or args.corrected)

    if precomputed_mode:
        if not args.bfi_results or not args.corrected:
            print("ERROR: precomputed mode requires both --bfi-results and --corrected",
                  file=sys.stderr)
            sys.exit(1)
        for path in (args.bfi_results, args.corrected):
            if not os.path.isfile(path):
                print(f"ERROR: file not found: {path}", file=sys.stderr)
                sys.exit(1)

        print(f"Legacy results: {os.path.basename(args.bfi_results)}")
        print(f"SDK corrected:  {os.path.basename(args.corrected)}")

        print("\nLoading legacy precomputed results...")
        legacy = load_legacy_precomputed(args.bfi_results)
        print(f"  Cameras: {sorted(legacy)}")

        print("\nLoading SDK corrected CSV...")
        sdk = load_sdk_precomputed(args.corrected)
        print(f"  Cameras: {sorted(sdk)}")

        print_comparison_precomputed(legacy, sdk)

        out_prefix = os.path.splitext(args.corrected)[0].replace("_corrected", "") + "_compare"
        print("\nGenerating plots...")
        plot_comparison_precomputed(legacy, sdk, save=args.save, out_prefix=out_prefix)

    else:
        left_csv  = args.left
        right_csv = args.right if args.right else None

        for path in filter(None, [left_csv, right_csv]):
            if not os.path.isfile(path):
                print(f"ERROR: file not found: {path}", file=sys.stderr)
                sys.exit(1)

        print(f"Left CSV:  {os.path.basename(left_csv)}")
        print(f"Right CSV: {os.path.basename(right_csv) if right_csv else '(none)'}")

        print("\nRunning legacy VisualizeBloodflow...")
        legacy = run_legacy(left_csv, right_csv)
        print(f"  Cameras: {sorted(legacy)}")

        print("\nRunning SDK SciencePipeline...")
        sdk = run_sdk(left_csv, right_csv)
        print(f"  Cameras: {sorted(sdk)}")

        print_comparison(legacy, sdk)

        out_prefix = os.path.splitext(left_csv)[0].replace("_left_maskFF", "") + "_compare"
        print("\nGenerating plots...")
        plot_comparison(legacy, sdk, save=args.save, out_prefix=out_prefix)


if __name__ == "__main__":
    main()
