"""End-to-end: replay one raw scan through the full default_pipeline twice
(realtime_dark_estimator = hybrid vs zoh), capture the LIVE side-average
trace (the thing the UI plots), and compare its 15 s-band content.

This is the direct RQ3 test: does the realtime estimator imprint a
dark-cadence oscillation on the live trace, and does zoh remove it?
"""
from __future__ import annotations
import sys
import numpy as np
from scipy.signal import welch

from omotion.pipeline.factory import default_pipeline
from omotion.pipeline.runner import ScanRunner
from omotion.pipeline.sources import CsvReplaySource
from omotion.pipeline.sinks import ScanMetadata
from omotion.pipeline.pedestal import SensorPedestals
from dataclasses import dataclass


@dataclass
class _Cal:
    c_min: np.ndarray; c_max: np.ndarray; i_min: np.ndarray; i_max: np.ndarray


def _cal():
    # Representative spans so BFI/BVI are in plausible units.
    return _Cal(
        c_min=np.full((2, 8), 0.30, np.float32),
        c_max=np.full((2, 8), 0.55, np.float32),
        i_min=np.full((2, 8), 40.0, np.float32),
        i_max=np.full((2, 8), 140.0, np.float32),
    )


class LiveSideCollector:
    channels = {"live_side"}
    critical = False
    def __init__(self): self.rows = []
    def on_scan_start(self, meta): pass
    def on_scan_stop(self): pass
    def on_complete(self): pass
    def consume(self, channel, payload):
        self.rows.append((payload.side, payload.t, payload.bfi, payload.bvi))


def run(left, right, estimator):
    meta = ScanMetadata(
        scan_id="e2e", subject_id="x", operator="x",
        started_at_iso="2026-06-11T00:00:00Z", duration_sec=0,
        left_camera_mask=0x66, right_camera_mask=0x66, reduced_mode=True,
    )
    pipe = default_pipeline(
        metadata=meta, calibration=_cal(),
        pedestals=SensorPedestals(left=64.0, right=64.0),
        realtime_dark_estimator=estimator, raw_save_max_duration_s=0,
    )
    src = CsvReplaySource(raw_csv_left=left or None, raw_csv_right=right or None,
                          batch_size_frames=200, metadata=meta)
    sink = LiveSideCollector()
    ScanRunner(source=src, pipeline=pipe, sinks=[sink]).run()
    return sink.rows


def band_amp(t, y, f0=1/15.0):
    m = np.isfinite(y)
    if m.sum() < 1024: return np.nan, np.nan, np.nan
    tt, yy = t[m], y[m] - np.nanmean(y[m])
    fs = 1.0/np.median(np.diff(tt))
    f, p = welch(yy, fs=fs, nperseg=min(8192, len(yy)))
    df = f[1]-f[0]
    band = (f>=f0*0.85)&(f<=f0*1.15)
    flank = ((f>=f0*1.5)&(f<=f0*2.5))|((f>=f0*0.3)&(f<=f0*0.6))
    amp15 = np.sqrt(2*max(p[band].sum()*df - p[flank].mean()*band.sum()*df,0))
    sel = (f>=0.03)&(f<=0.25)
    fpk = f[sel][np.argmax(p[sel])]
    return amp15, fpk, 1.0/fpk


def main(left, right):
    out = {}
    for est in ("hybrid", "zoh"):
        rows = run(left, right, est)
        a = np.array([r[1:] for r in rows])  # t, bfi, bvi
        sides = np.array([r[0] for r in rows])
        out[est] = (sides, a)
        print(f"[{est}] {len(rows)} live_side samples")
    print(f"\n{'side/metric':16s} {'hybrid 15s-amp':>15s} {'zoh 15s-amp':>13s} "
          f"{'hyb peak T':>11s} {'zoh peak T':>11s}")
    for side in (0, 1):
        for j, nm in ((1,"BFI"),(2,"BVI")):
            sh, ah = out["hybrid"]; sz, az = out["zoh"]
            mh = sh==side; mz = sz==side
            amp_h, _, Th = band_amp(ah[mh,0], ah[mh,j])
            amp_z, _, Tz = band_amp(az[mz,0], az[mz,j])
            print(f"side{side} {nm:11s} {amp_h:15.5f} {amp_z:13.5f} "
                  f"{Th:10.1f}s {Tz:10.1f}s")
    # Direct difference between the two live traces (paired on side+order).
    for side in (0,1):
        sh, ah = out["hybrid"]; sz, az = out["zoh"]
        mh, mz = sh==side, sz==side
        n = min(mh.sum(), mz.sum())
        for j, nm in ((1,"BFI"),(2,"BVI")):
            dh = ah[mh,j][:n]; dz = az[mz,j][:n]
            d = dh - dz
            print(f"side{side} {nm} hybrid-minus-zoh: "
                  f"rms={np.nanstd(d):.5f} max|diff|={np.nanmax(np.abs(d)):.5f} "
                  f"(trace rms={np.nanstd(dh):.4f})")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
