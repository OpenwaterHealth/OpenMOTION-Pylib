import argparse
import csv
from pathlib import Path
from typing import Dict, List

import matplotlib.pyplot as plt
import numpy as np


HIST_BINS = np.arange(1024, dtype=np.float64)
HIST_BINS_SQ = HIST_BINS * HIST_BINS
CHANNELS = [f"l{i}" for i in range(1, 9)] + [f"r{i}" for i in range(1, 9)]


def _find_scan_data_dir(cli_value: str | None) -> Path:
    if cli_value:
        p = Path(cli_value).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"scan_data directory does not exist: {p}")
        return p

    cwd = Path.cwd()
    candidates = [
        cwd / "scan_data",
        cwd.parent / "scan_data",
        cwd.parent.parent / "scan_data",
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()
    raise FileNotFoundError(
        "Could not find scan_data automatically. Pass --scan-data-dir explicitly."
    )


def _is_valid_corrected_csv(path: Path) -> bool:
    try:
        with path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, [])
        required = {"frame_id", "timestamp_s", "bfi_l1", "bvi_l1"}
        return required.issubset(set(header))
    except Exception:
        return False


def _latest_corrected_csv(scan_data_dir: Path) -> Path:
    # The SDK now writes the merged dark-baseline-corrected CSV without a
    # `_corrected` suffix (see openwaterhealth/openmotion-bloodflow-app#44).
    # Match both the new bare-stem layout and the legacy `_corrected.csv`
    # name so historical scans keep loading.  Per-side raw histogram CSVs
    # use a `_raw.csv` suffix and are excluded.
    seen: set[Path] = set()
    candidates: list[Path] = []
    for pattern in ("scan_*.csv", "scan_*_corrected.csv"):
        for p in scan_data_dir.glob(pattern):
            rp = p.resolve()
            if rp in seen:
                continue
            name = p.name
            if name.endswith("_raw.csv"):
                continue
            if name.endswith("_telemetry.csv"):
                continue
            # Skip per-side raw histogram CSVs (mask suffix without _raw is
            # only produced by pre-rename SDK builds; new builds emit
            # ..._mask##_raw.csv.  We handle both by gating on cam_id below.)
            if "_mask" in name and not name.endswith("_corrected.csv"):
                continue
            seen.add(rp)
            candidates.append(p)
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for c in candidates:
        if _is_valid_corrected_csv(c):
            return c
    raise FileNotFoundError(f"No valid corrected CSV found in {scan_data_dir}")


def _read_corrected(path: Path):
    frame_ids: List[int] = []
    timestamps: List[float] = []
    bfi: Dict[str, List[float]] = {ch: [] for ch in CHANNELS}
    bvi: Dict[str, List[float]] = {ch: [] for ch in CHANNELS}

    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            frame_ids.append(int(float(row["frame_id"])))
            timestamps.append(float(row["timestamp_s"]))
            for ch in CHANNELS:
                bfi_key = f"bfi_{ch}"
                bvi_key = f"bvi_{ch}"
                bfi[ch].append(float(row[bfi_key]) if row.get(bfi_key) else np.nan)
                bvi[ch].append(float(row[bvi_key]) if row.get(bvi_key) else np.nan)

    if not frame_ids:
        raise ValueError(f"No data rows in corrected CSV: {path}")

    # Ensure zero-based timeline.
    t0 = timestamps[0]
    timestamps = [t - t0 for t in timestamps]
    return frame_ids, timestamps, bfi, bvi


def _read_raw_metrics(raw_csv: Path, side_prefix: str):
    metrics: Dict[str, Dict[int, tuple[float, float]]] = {}
    hist_cols = [str(i) for i in range(1024)]

    with raw_csv.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cam_id = int(float(row["cam_id"]))
            frame_id = int(float(row["frame_id"]))
            channel = f"{side_prefix}{cam_id + 1}"

            hist = np.array([float(row[c]) for c in hist_cols], dtype=np.float64)
            row_sum = float(row.get("sum", 0.0)) or float(hist.sum())
            if row_sum <= 0:
                mean_val = 0.0
                contrast = 0.0
            else:
                mean_val = float(np.dot(hist, HIST_BINS) / row_sum)
                mean2 = float(np.dot(hist, HIST_BINS_SQ) / row_sum)
                var = max(0.0, mean2 - (mean_val * mean_val))
                contrast = float(np.sqrt(var) / mean_val) if mean_val > 0 else 0.0

            if channel not in metrics:
                metrics[channel] = {}
            # Keep first sample for a given frame/channel.
            metrics[channel].setdefault(frame_id, (mean_val, contrast))

    return metrics


def _load_mean_contrast(corrected_csv: Path, frame_ids: List[int]):
    # Strip the legacy `_corrected.csv` suffix if present; otherwise drop
    # the bare `.csv` extension.  Either layout yields the shared scan stem
    # used to discover the per-side raw histogram CSVs.
    if corrected_csv.name.endswith("_corrected.csv"):
        stem = corrected_csv.name[: -len("_corrected.csv")]
    else:
        stem = corrected_csv.stem
    scan_data_dir = corrected_csv.parent
    # Match both the new `_raw.csv` suffix and the legacy bare mask suffix
    # so historical scan_data folders keep loading.
    left = list(scan_data_dir.glob(f"{stem}_left_mask*_raw.csv")) or list(
        scan_data_dir.glob(f"{stem}_left_mask*.csv")
    )
    right = list(scan_data_dir.glob(f"{stem}_right_mask*_raw.csv")) or list(
        scan_data_dir.glob(f"{stem}_right_mask*.csv")
    )

    raw_metrics: Dict[str, Dict[int, tuple[float, float]]] = {}
    if left:
        raw_metrics.update(_read_raw_metrics(left[0], "l"))
    if right:
        raw_metrics.update(_read_raw_metrics(right[0], "r"))

    mean: Dict[str, List[float]] = {ch: [] for ch in CHANNELS}
    contrast: Dict[str, List[float]] = {ch: [] for ch in CHANNELS}
    for ch in CHANNELS:
        frame_map = raw_metrics.get(ch, {})
        for fid in frame_ids:
            vals = frame_map.get(fid)
            if vals is None:
                mean[ch].append(np.nan)
                contrast[ch].append(np.nan)
            else:
                mean[ch].append(vals[0])
                contrast[ch].append(vals[1])
    return mean, contrast, left[0] if left else None, right[0] if right else None


def _plot_metric(ax, x, per_channel: Dict[str, List[float]], title: str, y_label: str):
    series = []
    for ch in CHANNELS:
        y = np.array(per_channel[ch], dtype=np.float64)
        if np.all(np.isnan(y)):
            continue
        series.append(y)
        ax.plot(x, y, alpha=0.25, linewidth=0.8)

    if series:
        avg = np.nanmean(np.vstack(series), axis=0)
        ax.plot(x, avg, color="black", linewidth=2.0, label="avg")
        ax.legend(loc="upper right")
    ax.set_title(title)
    ax.set_ylabel(y_label)
    ax.grid(True, alpha=0.2)


def main():
    parser = argparse.ArgumentParser(
        description="View corrected scan CSV (BFI/BVI) with mean/contrast overlays."
    )
    parser.add_argument(
        "--csv",
        type=str,
        default=None,
        help="Path to corrected CSV. If omitted, latest valid in scan_data is used.",
    )
    parser.add_argument(
        "--scan-data-dir",
        type=str,
        default=None,
        help="Optional scan_data directory override.",
    )
    parser.add_argument(
        "--save",
        type=str,
        default=None,
        help="Optional output image path for the plotted figure.",
    )
    parser.add_argument(
        "--no-show",
        action="store_true",
        help="Do not open an interactive window.",
    )
    args = parser.parse_args()

    scan_data_dir = _find_scan_data_dir(args.scan_data_dir)
    corrected_csv = (
        Path(args.csv).expanduser().resolve() if args.csv else _latest_corrected_csv(scan_data_dir)
    )
    if not corrected_csv.exists():
        raise FileNotFoundError(f"Corrected CSV not found: {corrected_csv}")

    frame_ids, timestamps, bfi, bvi = _read_corrected(corrected_csv)
    mean, contrast, left_raw, right_raw = _load_mean_contrast(corrected_csv, frame_ids)

    print(f"Corrected CSV: {corrected_csv}")
    print(f"Left raw CSV:  {left_raw if left_raw else 'not found'}")
    print(f"Right raw CSV: {right_raw if right_raw else 'not found'}")

    fig, axes = plt.subplots(4, 1, figsize=(14, 12), sharex=True)
    _plot_metric(axes[0], timestamps, bfi, "Corrected BFI", "BFI")
    _plot_metric(axes[1], timestamps, bvi, "Corrected BVI", "BVI")
    _plot_metric(axes[2], timestamps, mean, "Mean (from raw histogram)", "Mean")
    _plot_metric(axes[3], timestamps, contrast, "Contrast (from raw histogram)", "Contrast")
    axes[3].set_xlabel("Time (s, normalized to 0 at first frame)")
    fig.suptitle(corrected_csv.name)
    fig.tight_layout()

    if args.save:
        out = Path(args.save).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, dpi=160)
        print(f"Saved figure: {out}")

    if not args.no_show:
        plt.show()


if __name__ == "__main__":
    main()
