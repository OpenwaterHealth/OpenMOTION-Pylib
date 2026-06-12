"""Pure-software tests for scripts/demod_characterization.py (analyze path).

Synthetic per-frame data with known dark / demod / normal populations;
verifies classification, periodicity detection, and the S / M / F ratios.
"""

import csv
import importlib.util
import json
import math
import random
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "demod_characterization.py"
spec = importlib.util.spec_from_file_location("demod_characterization", _SCRIPT)
dc = importlib.util.module_from_spec(spec)
sys.modules["demod_characterization"] = dc  # dataclass field-type resolution needs this
spec.loader.exec_module(dc)

# Synthetic scene parameters
PEDESTAL = 20.0
LIGHT_MEAN = 220.0
K_NORMAL = 0.30          # normal-frame contrast
K_DEMOD = 0.02           # modulated-frame contrast (the floor)
DARK_STD = 4.0
N_FRAMES = 600
DARK_EVERY = 7
DEMOD_EVERY = 10


def _write_run(run_dir: Path, demod_every: int | None, k_light: float = K_NORMAL,
               n_frames: int = N_FRAMES) -> None:
    """Write a synthetic frames.csv. demod_every=None -> no demod frames;
    k_light applies to all light frames (use K_DEMOD to fake a continuous run).
    """
    rng = random.Random(42)
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(run_dir / "frames.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(dc.CSV_HEADERS)
        for fid in range(1, n_frames + 1):
            dark = (fid % DARK_EVERY) == 0
            demod = (demod_every is not None and (fid % demod_every) == 0 and not dark)
            if dark:
                mean, std = PEDESTAL, DARK_STD
            else:
                k = K_DEMOD if demod else k_light
                mean = LIGHT_MEAN
                std = k * (LIGHT_MEAN - PEDESTAL)
            mean += rng.gauss(0, 0.5)
            std = max(std + rng.gauss(0, std * 0.02), 0.1)
            w.writerow([fid, fid * 0.025, 0, 3, 100000, f"{mean:.4f}", f"{std:.4f}"])
    (run_dir / "meta.json").write_text(json.dumps({"label": run_dir.name}))


def test_analyze_classifies_dark_demod_normal(tmp_path):
    _write_run(tmp_path / "interleave", DEMOD_EVERY)
    series = dc.load_run(tmp_path / "interleave")
    assert (0, 3) in series
    r = dc.analyze_cam(0, 3, series[(0, 3)])

    expected_dark = N_FRAMES // DARK_EVERY
    expected_demod = sum(1 for fid in range(1, N_FRAMES + 1)
                         if fid % DEMOD_EVERY == 0 and fid % DARK_EVERY != 0)
    assert r.n_dark == expected_dark
    assert r.n_demod == expected_demod
    assert abs(r.pedestal - PEDESTAL) < 1.0


def test_analyze_ratios_and_periodicity(tmp_path):
    _write_run(tmp_path / "interleave", DEMOD_EVERY)
    series = dc.load_run(tmp_path / "interleave")
    r = dc.analyze_cam(0, 3, series[(0, 3)])

    # S = K_demod / K_normal
    assert r.S == pytest.approx(K_DEMOD / K_NORMAL, rel=0.15)
    # Intensity identical on demod frames
    assert r.M == pytest.approx(1.0, abs=0.02)
    # Neighbors are clean in the synthetic data (no ring-down simulated)
    assert r.k_neighbor == pytest.approx(K_NORMAL, rel=0.1)
    # Modal demod spacing is the configured interval. (Dark collisions make
    # some spacings 20, so the matching fraction is high but not 1.0.)
    assert r.demod_interval_mode == DEMOD_EVERY
    assert r.demod_interval_frac > 0.5


def test_analyze_no_demod_run_finds_none(tmp_path):
    _write_run(tmp_path / "baseline", None)
    series = dc.load_run(tmp_path / "baseline")
    r = dc.analyze_cam(0, 3, series[(0, 3)])
    assert r.n_demod == 0
    assert math.isnan(r.S)
    assert r.k_normal == pytest.approx(K_NORMAL, rel=0.1)


def test_analyze_cli_with_reference_floor(tmp_path, capsys):
    """End-to-end CLI: interleave run against a continuous-modulation
    reference -> F ratio ~ 1 when the single-frame floor matches."""
    _write_run(tmp_path / "interleave", DEMOD_EVERY)
    # Continuous run: every light frame at the modulated floor
    _write_run(tmp_path / "continuous", None, k_light=K_DEMOD)

    rc = dc.main(["analyze", str(tmp_path / "interleave"),
                  "--reference", str(tmp_path / "continuous")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "S = K(demod)/K(normal)" in out
    assert (tmp_path / "interleave" / "report.txt").exists()

    # F column: K_demod / K_floor should be ~1.0
    series = dc.load_run(tmp_path / "interleave")
    r = dc.analyze_cam(0, 3, series[(0, 3)])
    ref = dc.analyze_cam(0, 3, dc.load_run(tmp_path / "continuous")[(0, 3)])
    assert r.k_demod / ref.k_normal == pytest.approx(1.0, rel=0.1)


def test_hist_moments_matches_numpy():
    import numpy as np
    rng = np.random.default_rng(7)
    hist = np.zeros(1024, dtype=np.float64)
    samples = rng.normal(300, 40, 50000).clip(0, 1023).astype(int)
    np.add.at(hist, samples, 1)
    counts, mean, std = dc.hist_moments(hist)
    assert counts == 50000
    assert mean == pytest.approx(samples.mean(), abs=0.01)
    assert std == pytest.approx(samples.std(), abs=0.01)
