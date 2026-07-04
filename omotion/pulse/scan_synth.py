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

import csv
import logging
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterator

import numpy as np

from ..config import CAMERA_GAIN_MAP
from ..pipeline.batch import FrameBatch
from ..pipeline.pedestal import adc_gain_for_pedestal
from .synth import synth_bfi

# Explicit "openmotion.sdk.*" name (matching omotion/pipeline/*) so app hosts
# that attach handlers to the "openmotion.sdk" tree capture these lines; a bare
# __name__ ("omotion.pulse.scan_synth") falls outside that tree and is dropped.
logger = logging.getLogger("openmotion.sdk.pulse.scan_synth")

_N_COUNTS = 10000
_HISTO_BINS = 1024


def _mask_to_cams(mask) -> list:
    return [i for i in range(8) if int(mask) & (1 << i)]


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


def _read_bfi_results(path: str) -> dict:
    """Parse a bfi_results CSV → ``{(side_idx, cam): (bfi_arr, bvi_arr)}`` sorted
    by time. side_idx 0=left, 1=right."""
    rows: dict = defaultdict(dict)      # (side, cam) -> {t: (bfi, bvi)}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                side = 0 if r["side"] == "left" else 1
                cam = int(r["camera"])
                t = float(r["time_s"])
                bfi = float(r["BFI"])
            except (KeyError, ValueError, TypeError):
                continue
            try:
                bvi = float(r.get("BVI"))
            except (TypeError, ValueError):
                bvi = float("nan")
            rows[(side, cam)][t] = (bfi, bvi)
    out: dict = {}
    for key, d in rows.items():
        ts = sorted(d)
        out[key] = (np.array([d[t][0] for t in ts], dtype=np.float64),
                    np.array([d[t][1] for t in ts], dtype=np.float64))
    return out


class DemoScanSource:
    """Replay a recorded ``*_bfi_results.csv`` as raw histograms at the TOP of
    the real pipeline (no hardware).

    Inverts each recorded BFI/BVI back into a speckle contrast and mean, then a
    gaussian histogram (the inverse of the moments → dark → shot-noise → BfiBvi
    chain), and exposes a matching ``calibration`` so the pipeline recomputes
    ~the recorded BFI/BVI. It therefore feeds the *whole* pipeline — the regular
    BFI/BVI plots AND the pulse view. Warmup/dark frames are synthesised at the
    classifier's positional schedule. The iterator ends when the recording is
    exhausted, which lets a replay scan auto-stop.

    ``left_mask`` / ``right_mask`` select which cameras (of those present in the
    file) are emitted — should match the ScanRequest / pipeline masks.
    """

    def __init__(self, *, csv_path: str, metadata, left_mask: int,
                 right_mask: int, fs: float = 40.0, pedestal: float = 64.0,
                 dark_interval: int = 600, discard_count: int = 9,
                 batch_size: int = 100, terminal_dark: int = 3,
                 realtime: bool = False):
        self.metadata = metadata
        self.pedestal = float(pedestal)
        self.dark_interval = int(dark_interval)
        self._discard = int(discard_count)
        self._terminal = int(terminal_dark)
        self._fs = float(fs)
        self._batch = int(batch_size)
        self._realtime = bool(realtime)
        self._gains = np.asarray(CAMERA_GAIN_MAP, dtype=np.float64).ravel()
        self._adc = adc_gain_for_pedestal(self.pedestal)
        # Set by close() so a mid-replay Stop (cancel_scan -> source.close())
        # halts __iter__, matching LiveUsbSource. The duration guard also
        # probes this attr (getattr(source, "_stop", ...)) to detect the
        # cancel path.
        self._stop = threading.Event()

        series = _read_bfi_results(csv_path)
        if not series:
            raise ValueError(f"no BFI rows in {csv_path}")
        self._masks = {0: _mask_to_cams(left_mask), 1: _mask_to_cams(right_mask)}
        self._series = {k: v for k, v in series.items()
                        if k[1] in self._masks[k[0]]}

        # Calibration brackets: BFI 0..10 ↔ contrast [c_max..c_min],
        # BVI 0..10 ↔ mean_dc [i_max..i_min]. Chosen (not fit) — used both to
        # invert here AND by the pipeline forward, so the values round-trip.
        self._c_min, self._c_max = 0.04, 0.18
        self._i_min, self._i_max = 100.0, 200.0
        self.calibration = SyntheticCalibration(
            c_min=np.full((2, 8), self._c_min, np.float32),
            c_max=np.full((2, 8), self._c_max, np.float32),
            i_min=np.full((2, 8), self._i_min, np.float32),
            i_max=np.full((2, 8), self._i_max, np.float32))
        self._frames = self._build_frames()

    def _mean_dc(self, bvi: float) -> float:
        b = float(np.clip(bvi, 0.0, 10.0)) if np.isfinite(bvi) else 5.0
        return self._i_min + (1.0 - b / 10.0) * (self._i_max - self._i_min)

    def _std_for(self, bfi: float, mean_dc: float, cam: int) -> float:
        b = float(np.clip(bfi, 0.01, 9.99)) if np.isfinite(bfi) else 5.0
        contrast = self._c_min + (1.0 - b / 10.0) * (self._c_max - self._c_min)
        shot = self._adc * mean_dc * self._gains[cam]
        return float(((contrast * mean_dc) ** 2 + shot) ** 0.5)

    def _is_dark(self, abs_id: int) -> bool:
        if abs_id == self._discard + 1:
            return True
        if abs_id <= self._discard + 1:
            return False
        return (abs_id - 1) % self.dark_interval == 0

    def _build_frames(self) -> list:
        # Interleave both sides by time (a monotonic 0..T timeline) so a real-
        # time replay advances both sides together and matches how the sensors
        # actually stream.
        rng = np.random.default_rng(0)
        sides_cams = {}
        for side in (0, 1):
            cams = [c for c in self._masks[side] if (side, c) in self._series]
            if cams:
                sides_cams[side] = cams
        if not sides_cams:
            return []
        n = min(self._series[(side, c)][0].size
                for side, cams in sides_cams.items() for c in cams)

        frames: list = []

        def _emit(i, dark, warm, j):
            raw = i % 256
            t = (i - 1) / self._fs
            for side, cams in sides_cams.items():
                for cam in cams:
                    if warm:
                        h = _gaussian_histogram(self.pedestal + 40.0, 10.0, rng)
                    elif dark:
                        h = _gaussian_histogram(self.pedestal + 3.0, 3.0, rng)
                    else:
                        bfi = self._series[(side, cam)][0][j]
                        bvi = self._series[(side, cam)][1][j]
                        mdc = self._mean_dc(bvi)
                        h = _gaussian_histogram(self.pedestal + mdc,
                                                self._std_for(bfi, mdc, cam), rng)
                    frames.append((raw, side, cam, t, h))

        j = 0        # index into the recorded (light-frame) samples
        i = 0        # absolute frame counter (1-based)
        while j < n:
            i += 1
            warm = i <= self._discard
            dark = self._is_dark(i)
            _emit(i, dark, warm, j)
            if not warm and not dark:
                j += 1
        for _ in range(self._terminal):            # laser-off frames at the end
            i += 1
            _emit(i, dark=True, warm=False, j=0)
        return frames

    def __iter__(self) -> Iterator[FrameBatch]:
        import time as _time
        wall0 = _time.monotonic()
        f = self._frames
        n_batches = (len(f) + self._batch - 1) // self._batch
        for s in range(0, len(f), self._batch):
            # Stop (cancel_scan -> close()) halts the replay here, so a manual
            # Stop in demo mode ends the scan instead of running to completion.
            if self._stop.is_set():
                logger.info(
                    "[demo] replay halted by Stop at batch %d/%d",
                    s // self._batch, n_batches,
                )
                return
            chunk = f[s:s + self._batch]
            if self._realtime and chunk:
                # Pace the stream to ~real time: wait until wall-clock reaches
                # this batch's last frame timestamp. Wait on _stop (not sleep)
                # so a Stop interrupts the pacing immediately.
                dt = chunk[-1][3] - (_time.monotonic() - wall0)
                if dt > 0 and self._stop.wait(min(dt, 2.0)):
                    logger.info(
                        "[demo] replay halted by Stop mid-pace at batch %d/%d",
                        s // self._batch, n_batches,
                    )
                    return
            nrows = len(chunk)
            rh = np.zeros((nrows, 2, 8, _HISTO_BINS), dtype=np.uint32)
            tc = np.zeros((nrows, 2, 8), dtype=np.float32)
            cam = np.zeros(nrows, dtype=np.int8)
            fid = np.zeros(nrows, dtype=np.uint8)
            sid = np.zeros(nrows, dtype=np.int8)
            ts = np.zeros(nrows, dtype=np.float64)
            for k, (raw, side, c, t, h) in enumerate(chunk):
                rh[k, side, c] = h
                tc[k, side, c] = 30.0
                cam[k] = c
                fid[k] = raw
                sid[k] = side
                ts[k] = t
            yield FrameBatch(cam_ids=cam, frame_ids=fid, side_ids=sid,
                             raw_histograms=rh, temperature_c=tc,
                             timestamp_s=ts, pdc=None, tcm=None, tcl=None)

    def close(self) -> None:
        # Idempotent: halts __iter__ (checked at each batch + interrupts the
        # realtime pacing wait). Mirrors LiveUsbSource.close().
        if not self._stop.is_set():
            logger.info("[demo] DemoScanSource.close() -> halting replay")
        self._stop.set()
