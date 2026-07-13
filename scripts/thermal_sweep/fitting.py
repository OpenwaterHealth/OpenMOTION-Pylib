"""Dependency-free exponential fitting for the off-sweep analysis.

Model: y(t) = y_inf + (y0 - y_inf) * exp(-t / tau)

For a FIXED tau the model is linear in (y_inf, amplitude), so we solve
that by linear least squares and 1-D search tau: coarse log-spaced grid,
then golden-section refinement. No scipy needed.
"""

import numpy as np


def _lsq_for_tau(t: np.ndarray, y: np.ndarray, tau: float):
    """Best (y_inf, amp) and SSE for fixed tau, where y = y_inf + amp*exp(-t/tau)."""
    e = np.exp(-t / tau)
    a = np.column_stack([np.ones_like(e), e])
    coef, *_ = np.linalg.lstsq(a, y, rcond=None)
    resid = y - a @ coef
    return coef, float(resid @ resid)


def _lsq_for_tau2(t, y, tau1, tau2):
    """Best (c, a1, a2) and SSE for y = c + a1*exp(-t/tau1) + a2*exp(-t/tau2)."""
    a = np.column_stack([np.ones_like(t), np.exp(-t / tau1), np.exp(-t / tau2)])
    coef, *_ = np.linalg.lstsq(a, y, rcond=None)
    resid = y - a @ coef
    return coef, float(resid @ resid)


def fit_exp2(t, y, tau1_bounds=(3.0, 300.0), tau2_bounds=(60.0, 5000.0)):
    """Fit y = c + a1*exp(-t/tau1) + a2*exp(-t/tau2), tau1 < tau2.
    Coarse 2-D log grid + one local refinement pass. Returns dict or None."""
    t = np.asarray(t, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    keep = np.isfinite(t) & np.isfinite(y)
    t, y = t[keep], y[keep]
    if t.size < 12:
        return None
    ts = t - t.min()

    def search(g1, g2):
        best = (None, np.inf, None, None)
        for t1 in g1:
            for t2 in g2:
                if t2 <= t1 * 2.0:
                    continue
                coef, sse = _lsq_for_tau2(ts, y, t1, t2)
                if sse < best[1]:
                    best = (coef, sse, t1, t2)
        return best

    g1 = np.geomspace(*tau1_bounds, 24)
    g2 = np.geomspace(*tau2_bounds, 24)
    coef, sse, t1, t2 = search(g1, g2)
    if coef is None:
        return None
    # local refine around the winner
    g1r = np.geomspace(t1 / 1.6, t1 * 1.6, 15)
    g2r = np.geomspace(t2 / 1.6, t2 * 1.6, 15)
    coef2, sse2, t1b, t2b = search(g1r, g2r)
    if coef2 is not None and sse2 < sse:
        coef, sse, t1, t2 = coef2, sse2, t1b, t2b
    c, a1, a2 = coef
    return {
        "tau1_s": float(t1), "tau2_s": float(t2),
        "c": float(c), "a1": float(a1), "a2": float(a2),
        "y0": float(c + a1 + a2),
        "rmse": float(np.sqrt(sse / t.size)),
        "n": int(t.size),
    }


def fit_exp(t, y, tau_bounds=(1.0, 20000.0)):
    """Fit y(t) = y_inf + amp*exp(-t/tau). Returns dict with tau, y_inf, y0,
    amp, rmse. t in seconds (need not start at 0)."""
    t = np.asarray(t, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    keep = np.isfinite(t) & np.isfinite(y)
    t, y = t[keep], y[keep]
    if t.size < 8:
        return None
    t0 = t.min()
    ts = t - t0

    lo, hi = tau_bounds
    grid = np.geomspace(lo, hi, 60)
    sses = [_lsq_for_tau(ts, y, g)[1] for g in grid]
    i = int(np.argmin(sses))

    # Golden-section refine around the best grid point.
    a = grid[max(0, i - 1)]
    b = grid[min(len(grid) - 1, i + 1)]
    phi = (np.sqrt(5.0) - 1.0) / 2.0
    x1 = b - phi * (b - a)
    x2 = a + phi * (b - a)
    f1 = _lsq_for_tau(ts, y, x1)[1]
    f2 = _lsq_for_tau(ts, y, x2)[1]
    for _ in range(60):
        if b - a < 1e-3 * b:
            break
        if f1 <= f2:
            b, x2, f2 = x2, x1, f1
            x1 = b - phi * (b - a)
            f1 = _lsq_for_tau(ts, y, x1)[1]
        else:
            a, x1, f1 = x1, x2, f2
            x2 = a + phi * (b - a)
            f2 = _lsq_for_tau(ts, y, x2)[1]
    tau = 0.5 * (a + b)
    (y_inf, amp), sse = _lsq_for_tau(ts, y, tau)
    return {
        "tau_s": float(tau),
        "y_inf": float(y_inf),
        "amp": float(amp),
        "y0": float(y_inf + amp),
        "rmse": float(np.sqrt(sse / t.size)),
        "n": int(t.size),
        "t_start": float(t0),
    }
