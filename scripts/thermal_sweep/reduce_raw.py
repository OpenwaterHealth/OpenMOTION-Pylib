"""Reduce full raw histogram CSVs (1033 cols) to per-frame stats CSVs.

For each ``*_raw.csv`` in the sweep data dir, writes ``derived/*_stats.csv``
with: cam_id, frame_id, timestamp_s, type, mean, std, temperature, sum.
Mean/std are computed over the 1024-bin histogram (bin index = pixel value),
identical math to the script's LightCsvSink.

Skips files modified in the last 120 s (scan still writing) and files
already reduced, so it can run incrementally while the experiment runs.
"""

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

DATA = (Path(sys.argv[1]) if len(sys.argv) > 1
        else Path(r"C:\Users\openwater\Projects\openmotion-sdk\scan_off_cycle_data"))
OUT = DATA / "derived"

BIN_COLS = [str(i) for i in range(1024)]
BINS = np.arange(1024, dtype=np.float64)


def reduce_one(src: Path, dst: Path) -> None:
    t0 = time.time()
    parts = []
    for chunk in pd.read_csv(src, chunksize=4000):
        h = chunk[BIN_COLS].to_numpy(dtype=np.float64)
        tot = h.sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = (h * BINS).sum(axis=1) / tot
            var = (h * BINS * BINS).sum(axis=1) / tot - mean * mean
        std = np.sqrt(np.clip(var, 0.0, None))
        parts.append(pd.DataFrame({
            "cam_id": chunk["cam_id"].to_numpy(),
            "frame_id": chunk["frame_id"].to_numpy(),
            "timestamp_s": chunk["timestamp_s"].to_numpy(),
            "type": chunk["type"].to_numpy(),
            "mean": np.round(mean, 6),
            "std": np.round(std, 6),
            "temperature": chunk["temperature"].to_numpy(),
            "sum": tot.astype(np.int64),
        }))
    df = pd.concat(parts, ignore_index=True)
    df.to_csv(dst, index=False)
    print(f"reduced {src.name}: {len(df)} rows in {time.time() - t0:.0f}s -> {dst.name}", flush=True)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    now = time.time()
    for src in sorted(DATA.glob("*_raw.csv")):
        if now - src.stat().st_mtime < 120:
            print(f"skip (still writing): {src.name}", flush=True)
            continue
        dst = OUT / src.name.replace("_raw.csv", "_stats.csv")
        if dst.exists():
            continue
        reduce_one(src, dst)
    print("done")


if __name__ == "__main__":
    main()
