"""Synthetic pulsatile *scan* source — cardiac-modulated speckle histograms.

Where ``synth.py`` fabricates a BFI time series, this fabricates the raw
histograms one step upstream, so a full synthetic scan can be driven through
the real science pipeline (``default_pipeline``) end-to-end with no hardware.

The trick: during systole blood flow rises, speckle decorrelates faster, and
the measured speckle contrast *drops* — which the pipeline maps to a higher
BFI. So we modulate each light frame's histogram width to hit a target
pulsatile contrast, inverting the shot-noise model
(``contrast = sqrt(std² - adc_gain·mean·gain_cam) / mean``) to pick the width.
The source also exposes the matching ``calibration`` so the caller's
``default_pipeline`` maps the contrast range onto a clean pulsatile BFI.

Frames follow the classifier's positional schedule: frame_id starts at 1,
frames 1..9 are warmup, frame 10 (and every ``dark_interval`` after) is a dark
(laser-off) frame, the rest are light.

Kept out of the pure ``synth``/``analyzer`` modules (and out of
``omotion.pulse.__init__``) because it depends on the pipeline package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

import numpy as np

from ..config import CAMERA_GAIN_MAP
from ..pipeline.batch import FrameBatch
from ..pipeline.pedestal import adc_gain_for_pedestal
from .synth import synth_bfi

_N_COUNTS = 10000
_HISTO_BINS = 1024


@dataclass
class SyntheticCalibration:
    """Minimal calibration (c_min/c_max/i_min/i_max as (2, 8)) for BfiBviStage."""
    c_min: np.ndarray
    c_max: np.ndarray
    i_min: np.ndarray
    i_max: np.ndarray


def _gaussian_histogram(mean: float, std: float,
                        rng: np.random.Generator) -> np.ndarray:
    bins = np.arange(_HISTO_BINS, dtype=np.float64)
    w = np.exp(-0.5 * ((bins - mean) / max(std, 1.0)) ** 2)
    w = np.maximum(w, 0.0)
    total = w.sum()
    if total <= 0:
        w[int(np.clip(mean, 0, _HISTO_BINS - 1))] = 1.0
        total = 1.0
    return rng.multinomial(_N_COUNTS, w / total).astype(np.uint32)


class SyntheticPulseScanSource:
    """A pipeline ``Source`` of cardiac-modulated speckle histograms.

    Usage::

        src = SyntheticPulseScanSource(metadata=meta, left_bpm=72, right_bpm=90)
        pipe = default_pipeline(metadata=meta, calibration=src.calibration,
                                pedestals=SensorPedestals(src.pedestal, src.pedestal),
                                dark_interval=src.dark_interval, enable_pulse=True)
        ScanRunner(source=src, pipeline=pipe, sinks=[...]).run()
    """

    def __init__(self, *, metadata, n_frames: int = 520, fs: float = 40.0,
                 pedestal: float = 64.0, dark_interval: int = 200,
                 discard_count: int = 9, mean_dc: float = 150.0, cam_id: int = 0,
                 left_bpm: float = 72.0, right_bpm: float = 78.0,
                 contrast_dia: float = 0.16, contrast_amp: float = 0.11,
                 right_amp_ratio: float = 0.6, pulse_shape: str = "normal",
                 hrv_frac: float = 0.03, seed: int = 0, batch_size: int = 100,
                 terminal_dark: int = 3):
        self.metadata = metadata
        self.pedestal = float(pedestal)
        self.dark_interval = int(dark_interval)
        self._discard = int(discard_count)
        self._terminal_dark = int(terminal_dark)
        self._fs = float(fs)
        self._mean_dc = float(mean_dc)
        self._cam = int(cam_id)
        self._batch = int(batch_size)
        gain_cam = float(np.asarray(CAMERA_GAIN_MAP).ravel()[self._cam])
        self._shot_var = adc_gain_for_pedestal(self.pedestal) * self._mean_dc * gain_cam

        # Calibration brackets the contrast range so BFI lands in ~[1, 9].
        c_sys = contrast_dia - contrast_amp          # systole (fastest flow)
        c_min = np.full((2, 8), max(0.0, c_sys - 0.02), dtype=np.float32)
        c_max = np.full((2, 8), contrast_dia + 0.02, dtype=np.float32)
        i_min = np.zeros((2, 8), dtype=np.float32)
        i_max = np.full((2, 8), 2.0 * self._mean_dc, dtype=np.float32)
        self.calibration = SyntheticCalibration(c_min, c_max, i_min, i_max)

        self._frames = (
            self._gen_side(0, left_bpm, contrast_dia, contrast_amp,
                           pulse_shape, hrv_frac, n_frames, seed)
            + self._gen_side(1, right_bpm, contrast_dia,
                             contrast_amp * right_amp_ratio, pulse_shape,
                             hrv_frac, n_frames, seed + 1)
        )

    def _std_for_contrast(self, c: float) -> float:
        return float(((c * self._mean_dc) ** 2 + self._shot_var) ** 0.5)

    def _is_dark(self, abs_id: int) -> bool:
        if abs_id == self._discard + 1:
            return True
        if abs_id <= self._discard + 1:
            return False
        return (abs_id - 1) % self.dark_interval == 0

    def _gen_side(self, side: int, bpm: float, c_dia: float, c_amp: float,
                  shape: str, hrv: float, n: int, seed: int) -> list:
        rng = np.random.default_rng(seed)
        _, wave = synth_bfi(duration_s=n / self._fs, fs=self._fs, bpm=bpm,
                            hrv_frac=hrv, noise=0.0, wander=0.0,
                            pulse_shape=shape, seed=seed)
        wave = wave[:n]
        m = (wave - wave.min()) / (wave.max() - wave.min() + 1e-9)  # 0..1
        out = []
        for i in range(1, n + 1):
            raw = i % 256
            t = (i - 1) / self._fs
            if i <= self._discard:
                mean, std = self.pedestal + 40.0, 10.0
            elif self._is_dark(i) or i > n - self._terminal_dark:
                # scheduled dark, or the firmware's laser-off frame(s) at scan
                # stop (so DarkCorrection's terminal-dark handling closes the
                # final interval cleanly instead of logging it as lost).
                mean, std = self.pedestal + 3.0, 3.0
            else:
                c = c_dia - c_amp * m[i - 1]      # systole (m≈1): low contrast
                mean, std = self.pedestal + self._mean_dc, self._std_for_contrast(c)
            out.append((raw, side, t, _gaussian_histogram(mean, std, rng)))
        return out

    def __iter__(self) -> Iterator[FrameBatch]:
        f = self._frames
        for s in range(0, len(f), self._batch):
            chunk = f[s:s + self._batch]
            n = len(chunk)
            rh = np.zeros((n, 2, 8, _HISTO_BINS), dtype=np.uint32)
            tc = np.zeros((n, 2, 8), dtype=np.float32)
            cam = np.full(n, self._cam, dtype=np.int8)
            fid = np.zeros(n, dtype=np.uint8)
            sid = np.zeros(n, dtype=np.int8)
            ts = np.zeros(n, dtype=np.float64)
            for i, (raw, side, t, h) in enumerate(chunk):
                rh[i, side, self._cam] = h
                tc[i, side, self._cam] = 30.0
                fid[i] = raw
                sid[i] = side
                ts[i] = t
            yield FrameBatch(cam_ids=cam, frame_ids=fid, side_ids=sid,
                             raw_histograms=rh, temperature_c=tc,
                             timestamp_s=ts, pdc=None, tcm=None, tcl=None)

    def close(self) -> None:
        pass
