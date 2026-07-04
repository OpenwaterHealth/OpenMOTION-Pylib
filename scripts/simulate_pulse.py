#!/usr/bin/env python3
"""Simulate a pulsatile blood-flow scan end-to-end and render the analysis.

Generates synthetic cardiac-modulated speckle histograms, runs them through
the real science pipeline (``default_pipeline`` with ``enable_pulse=True``),
and plots the resulting left/right pulse waveforms — the ensemble template
over its min/max envelope, the per-side BFI time series, and a left-vs-right
pulse-shape statistics table. No hardware required.

    python scripts/simulate_pulse.py --out pulse_simulation.png \
        --left-bpm 72 --right-bpm 88 --right-amp-ratio 0.55

See omotion/pulse/scan_synth.py (SyntheticPulseScanSource) and
omotion/pulse/analyzer.py (PulseWaveformAnalyzer).
"""

from __future__ import annotations

import argparse

import numpy as np

from omotion.pipeline.factory import default_pipeline
from omotion.pipeline.pedestal import SensorPedestals
from omotion.pipeline.runner import ScanRunner
from omotion.pipeline.sinks import ScanMetadata
from omotion.pulse.scan_synth import SyntheticPulseScanSource


class _Capture:
    channels = {"pulse", "live_side"}

    def on_scan_start(self, meta):
        self.pulse = []
        self.tv = {0: [], 1: []}

    def consume(self, channel, payload):
        if channel == "pulse":
            self.pulse.append(payload)
        elif channel == "live_side" and np.isfinite(payload.bfi):
            self.tv[int(payload.side)].append((payload.t, payload.bfi))

    def on_complete(self):
        pass


def _pearson(a, b):
    a = np.asarray(a) - np.mean(a)
    b = np.asarray(b) - np.mean(b)
    d = np.sqrt(np.sum(a * a) * np.sum(b * b))
    return float(np.sum(a * b) / d) if d > 0 else float("nan")


def run(args) -> "_Capture":
    meta = ScanMetadata(
        scan_id="pulse_sim", subject_id="sim", operator="sim",
        started_at_iso="2026-01-01T00:00:00Z", duration_sec=15,
        left_camera_mask=0x01, right_camera_mask=0x01, reduced_mode=True,
    )
    src = SyntheticPulseScanSource(
        metadata=meta, n_frames=args.frames, left_bpm=args.left_bpm,
        right_bpm=args.right_bpm, right_amp_ratio=args.right_amp_ratio,
        pulse_shape=args.shape, seed=args.seed,
    )
    pipe = default_pipeline(
        metadata=meta, calibration=src.calibration,
        pedestals=SensorPedestals(left=src.pedestal, right=src.pedestal),
        dark_interval=src.dark_interval, enable_pulse=True,
    )
    cap = _Capture()
    ScanRunner(source=src, pipeline=pipe, sinks=[cap]).run()
    return cap


def render(cap, out_path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def last(side):
        s = [p for p in cap.pulse if p.side == side and p.beat_count > 0]
        if not s:
            raise SystemExit(f"no beats detected for {side} side")
        return s[-1]

    L, R = last("left"), last("right")
    plt.rcParams.update({
        "axes.facecolor": "#141417", "figure.facecolor": "#1A1A1C",
        "text.color": "#DDD", "axes.edgecolor": "#444", "axes.labelcolor": "#BBB",
        "xtick.color": "#999", "ytick.color": "#999", "font.size": 10})
    fig = plt.figure(figsize=(13, 8.5))
    gs = fig.add_gridspec(3, 2, height_ratios=[3, 2, 1.4], hspace=0.42, wspace=0.18)

    for col, (snap, name, color) in enumerate(
            [(L, "LEFT", "#2ECC71"), (R, "RIGHT", "#4A90E2")]):
        ax = fig.add_subplot(gs[0, col])
        ph = np.array(snap.phase) * 100
        ax.fill_between(ph, snap.env_min, snap.env_max, color=color, alpha=0.15,
                        label="min/max envelope")
        ax.fill_between(ph, snap.env_p25, snap.env_p75, color=color, alpha=0.28,
                        label="p25–p75")
        ax.plot(ph, snap.template, color=color, lw=2.6, label="template (avg pulse)")
        lp = np.array(snap.live_phase) * 100
        if lp.size > 1:
            ax.plot(lp, snap.live_value, color="#EEE", lw=1.4, alpha=0.9, label="live beat")
        f = snap.features
        ax.set_title(f"{name}   {f.hr_bpm:.0f} bpm   PI {f.pi:.2f}   RI {f.ri:.2f}",
                     color=color, fontweight="bold")
        ax.set_xlabel("cardiac phase (%)"); ax.set_ylabel("BFI"); ax.set_xlim(0, 100)
        ax.legend(loc="upper right", fontsize=7, framealpha=0.2); ax.grid(alpha=0.15)

    axb = fig.add_subplot(gs[1, :])
    for side, color, name in [(0, "#2ECC71", "left"), (1, "#4A90E2", "right")]:
        tv = np.array(cap.tv[side])
        if tv.size:
            axb.plot(tv[:, 0] - tv[:, 0].min(), tv[:, 1], color=color, lw=1.0,
                     label=f"{name} side-avg BFI")
    axb.set_title("Blood-flow-index time series "
                  "(synthetic speckle histograms → full science pipeline)", fontsize=10)
    axb.set_xlabel("time (s)"); axb.set_ylabel("BFI")
    axb.legend(loc="upper right", fontsize=8, framealpha=0.2); axb.grid(alpha=0.15)

    axt = fig.add_subplot(gs[2, :]); axt.axis("off")

    def cell(f, k, d):
        v = getattr(f, k)
        return f"{v:.{d}f}" if np.isfinite(v) else "—"

    rows = [("Heart rate (bpm)", "hr_bpm", 0), ("Pulse amplitude (BFI)", "amp", 2),
            ("Pulsatility index", "pi", 2), ("Resistivity index", "ri", 2),
            ("Area under curve", "auc", 2), ("Rise time (ms)", "rise_time_ms", 0),
            ("Beats analysed", "beat_count", 0), ("Template consistency", "consistency", 2)]
    data = [[lbl, cell(L.features, k, d), cell(R.features, k, d)] for lbl, k, d in rows]
    tab = axt.table(cellText=data, colLabels=["Pulse-shape metric", "Left", "Right"],
                    loc="center", cellLoc="center", colWidths=[0.5, 0.25, 0.25])
    tab.auto_set_font_size(False); tab.set_fontsize(9); tab.scale(1, 1.35)
    for (rr, _), c in tab.get_celld().items():
        c.set_edgecolor("#333")
        c.set_facecolor("#26262E" if rr == 0 else "#1C1C22")
        c.set_text_props(color="#DDD", fontweight="bold" if rr == 0 else "normal")

    sim = _pearson(L.template, R.template)
    fig.suptitle("OpenMotion pulse-waveform simulation — full pipeline "
                 f"(histograms→BFI→pulse)   |   L/R shape similarity r = {sim:.3f}",
                 fontsize=12, fontweight="bold", y=0.98)
    fig.savefig(out_path, dpi=110, bbox_inches="tight", facecolor="#1A1A1C")
    print(f"saved {out_path}")
    for s, snap in (("left", L), ("right", R)):
        f = snap.features
        print(f"  {s}: HR={f.hr_bpm:.1f} bpm  PI={f.pi:.2f}  RI={f.ri:.2f}  "
              f"amp={f.amp:.2f}  beats={snap.beat_count}")
    print(f"  L/R shape similarity r = {sim:.3f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="pulse_simulation.png")
    ap.add_argument("--frames", type=int, default=560)
    ap.add_argument("--left-bpm", type=float, default=72.0)
    ap.add_argument("--right-bpm", type=float, default=88.0)
    ap.add_argument("--right-amp-ratio", type=float, default=0.55)
    ap.add_argument("--shape", default="normal",
                    help="normal | high_pi | low_pi | damped | noisy")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    render(run(args), args.out)


if __name__ == "__main__":
    main()
