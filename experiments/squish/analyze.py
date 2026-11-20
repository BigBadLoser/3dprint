#!/usr/bin/env python3
"""Analyze a squish run recorded by tools/kdata.py.

Fits   beacon_freq(t) = k * flow(t - lag) + slow drift
(on samples at the test height; converted to µm where the Beacon model covers it)
where flow(t) is the commanded volumetric flow from the extruder trapq.

Prints the Beacon sample rate, the noise floor, the best lag, the gain k
(µm of apparent bed distance per mm³/s of flow) and an SNR for the lo→hi flow
step. With --plot it saves overview.png in the run directory (needs matplotlib).

    ./analyze.py ~/squish/static_1 [--plot] [--dry ~/squish/dry_1]

With --dry, the dry run's Beacon trace (same moves, no plastic) is
time-aligned to the first move and subtracted, removing bed topography.
"""
import argparse
import json
import os
import sys

import numpy as np

FIL_AREA = np.pi * (1.75 / 2) ** 2


def load_jsonl(path):
    with open(path) as f:
        for line in f:
            yield json.loads(line)


def find_stream(run, prefix):
    meta = json.load(open(os.path.join(run, "meta.json")))
    for spec, s in meta["streams"].items():
        if spec.startswith(prefix):
            return os.path.join(run, s["file"]), s["header"]
    return None, None


def load_beacon(run):
    """All Beacon samples. dist is NaN outside the model range (0.2–5 mm);
    freq is always valid, so the analysis works on freq."""
    path, header = find_stream(run, "beacon/dump")
    if path is None:
        sys.exit("%s: no beacon/dump stream" % run)
    cols = header["header"]
    it, idist, ifreq, itemp, ipos = (
        cols.index(c) for c in ("time", "dist", "freq", "temp", "pos"))
    rows = []
    for m in load_jsonl(path):
        for r in m["params"] or []:
            if r[ifreq] is None:
                continue
            d = r[idist]
            pos = r[ipos]
            rows.append((r[it], d if d is not None else np.nan, r[ifreq],
                         r[itemp], pos[2] if pos else np.nan))
    a = np.array(rows, dtype=float)
    a = a[np.argsort(a[:, 0])]
    a[~np.isfinite(a[:, 1]), 1] = np.nan
    return {"t": a[:, 0], "dist": a[:, 1], "freq": a[:, 2], "temp": a[:, 3],
            "z": a[:, 4]}


def test_window(b):
    """Samples taken at the run's test height (where it spends most time)."""
    z = b["z"]
    if not np.isfinite(z).any():
        return np.ones_like(z, dtype=bool), float("nan")
    zt = float(np.nanmedian(z))
    # exclude the Z descent/lift (5 mm/s) but keep slow mesh-following Z
    # changes during XY moves; pad 50 ms either side of any fast Z motion
    vz = np.abs(np.gradient(z, b["t"]))
    fast = vz > 0.5
    pad = 50
    if fast.any():
        fast = np.convolve(fast.astype(float), np.ones(2 * pad + 1), "same") > 0
    return (np.abs(z - zt) < 0.2) & ~fast, zt


def um_per_hz(b, w):
    """Local slope of the Beacon model at the test height, from samples in
    this run where dist is valid near that height (e.g. the descent)."""
    dw = b["dist"][w]
    if not np.isfinite(dw).any():
        return None
    d0 = np.nanmedian(dw)
    m = np.isfinite(b["dist"]) & (np.abs(b["dist"] - d0) < 0.5)
    if m.sum() < 50 or np.ptp(b["dist"][m]) < 0.05:
        return None
    slope = np.polyfit(b["freq"][m], b["dist"][m], 1)[0]
    return slope * 1000.0


def load_trapq(run, name):
    path, _ = find_stream(run, "motion_report/dump_trapq:name=%s" % name)
    if path is None:
        return []
    moves = []
    for m in load_jsonl(path):
        p = m["params"]
        for r in (p.get("data", []) if isinstance(p, dict) else []):
            moves.append(r)
    moves.sort(key=lambda r: r[0])
    return moves


def trapq_velocity(moves, t, axis=0):
    """Commanded velocity along `axis` of the trapq at times t."""
    v = np.zeros_like(t)
    for (t0, dur, v0, acc, _pos, dirn) in moves:
        m = (t >= t0) & (t < t0 + dur)
        v[m] = (v0 + acc * (t[m] - t0)) * dirn[axis]
    return v


def line_start(moves):
    """Start time of the first X move after the descent to gap height."""
    descended = False
    for (t0, _dur, _v0, _acc, _pos, dirn) in moves:
        if dirn[2] < 0:
            descended = True
        elif descended and abs(dirn[0]) > 0.5:
            return t0
    sys.exit("could not find the start of the line in the toolhead trapq")


def per_cycle_gains(t, sig, flow, lag):
    """Split at the rest periods between pulse groups and fit each alone."""
    on = flow > 0
    edges = np.flatnonzero(np.diff(on.astype(int)) == 1) + 1
    if len(edges) < 2:
        return []
    bounds = [0] + [(edges[i - 1] + edges[i]) // 2 for i in range(1, len(edges))] + [len(t)]
    out = []
    for i in range(len(bounds) - 1):
        sl = slice(bounds[i], bounds[i + 1])
        if np.ptp(flow[sl]) == 0:
            continue
        _, _, coef, _ = fit(t[sl], sig[sl], flow[sl], [lag])
        out.append(coef[0])
    return out


def spectrum_peaks(x, rate, both, n=4096, top=5):
    """Averaged amplitude spectrum: is the noise white or does it have tones?"""
    if len(x) < 2 * n:
        return
    segs = len(x) // n
    win = np.hanning(n)
    ps = np.zeros(n // 2 + 1)
    for i in range(segs):
        seg = x[i * n:(i + 1) * n]
        seg = seg - np.polyval(np.polyfit(np.arange(n), seg, 1), np.arange(n))
        ps += np.abs(np.fft.rfft(seg * win)) ** 2
    ps /= segs
    f = np.fft.rfftfreq(n, 1.0 / rate)
    band = f > 2.0
    total = ps[band].sum()
    # fraction of noise power below/above 50 Hz
    lo = ps[band & (f < 50)].sum() / total
    print("noise power: %.0f%% below 50 Hz, %.0f%% above" % (lo * 100, (1 - lo) * 100))
    med = np.median(ps[band])
    idx = np.flatnonzero(band)
    cand = [i for i in idx[1:-1] if ps[i] > ps[i - 1] and ps[i] >= ps[i + 1]]
    cand.sort(key=lambda i: -ps[i])
    peaks = ["%.1f Hz (%.0fx)" % (f[i], ps[i] / med) for i in cand[:top]
             if ps[i] > 8 * med]
    print("spectral tones (x = times the median noise floor): "
          + (", ".join(peaks) if peaks else "none, noise is broadband"))


def fit(t, d, flow, lags):
    """Least squares over lag: d = k*flow(t-lag) + poly2(t) + c."""
    tn = (t - t.mean()) / (np.ptp(t) or 1.0)
    best = None
    for lag in lags:
        f = np.interp(t - lag, t, flow)
        A = np.column_stack([f, np.ones_like(t), tn, tn ** 2])
        coef, *_ = np.linalg.lstsq(A, d, rcond=None)
        resid = d - A @ coef
        sse = float(resid @ resid)
        if best is None or sse < best[0]:
            best = (sse, lag, coef, resid)
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("run")
    ap.add_argument("--dry", help="dry-run directory to subtract")
    ap.add_argument("--plot", action="store_true")
    ap.add_argument("--max-lag", type=float, default=0.5)
    a = ap.parse_args()

    b = load_beacon(a.run)
    rate = 1.0 / np.median(np.diff(b["t"]))
    dt_all = np.diff(b["t"])
    w, zt = test_window(b)
    k_um = um_per_hz(b, w)
    t = b["t"][w]
    sig = b["freq"][w] - b["freq"][w].mean()          # Hz

    e_moves = load_trapq(a.run, "extruder")
    flow = trapq_velocity(e_moves, t) * FIL_AREA if e_moves else np.zeros_like(t)

    if a.dry:
        bd = load_beacon(a.dry)
        wd, _ = test_window(bd)
        off = line_start(load_trapq(a.run, "toolhead")) - \
            line_start(load_trapq(a.dry, "toolhead"))
        dry = bd["freq"][wd] - bd["freq"][wd].mean()
        sig = sig - np.interp(t, bd["t"][wd] + off, dry)

    def both(hz):
        return "%.3f Hz" % hz + ("  (%.3f µm)" % abs(hz * k_um) if k_um else "")

    print("run: %s" % a.run)
    print("beacon: %d samples, %.1f Hz median rate, %.1f s total, coil %.1f–%.1f °C"
          % (len(b["t"]), rate, np.ptp(b["t"]), b["temp"].min(), b["temp"].max()))
    print("sample interval: p1 %.2f ms, p99 %.2f ms, max gap %.1f ms"
          % (np.percentile(dt_all, 1) * 1e3, np.percentile(dt_all, 99) * 1e3,
             dt_all.max() * 1e3))
    print("test window: Z %.2f, %d samples (%.1f s); Beacon freq %.0f Hz"
          % (zt, len(t), np.ptp(t) if len(t) else 0, b["freq"][w].mean()))
    print("scale: " + ("%.4f µm per Hz at this height" % k_um if k_um else
                       "no µm conversion (outside Beacon model range), Hz only"))
    tt = b["temp"][w]
    print("coil temp in window: %.2f → %.2f °C (drift %.3f °C/s)"
          % (tt[0], tt[-1], np.polyfit(t, tt, 1)[0]))
    drift = np.polyfit(t, sig, 1)[0]
    print("signal drift in window: " + both(drift) + " per s")

    quiet = flow == 0
    q = sig[quiet] if (e_moves and quiet.sum() > 50) else sig
    kk = max(1, int(rate * 0.2))
    hp = q - np.convolve(q, np.ones(kk) / kk, mode="same")
    print("noise (0.2 s high-pass): " + both(hp[kk:-kk].std()) + " RMS")
    blocks = len(q) // kk
    if blocks > 3:
        means = q[:blocks * kk].reshape(blocks, kk).mean(axis=1)
        print("noise of 0.2 s averages: " + both(np.diff(means).std() / np.sqrt(2)))
    if not e_moves or not np.any(flow):
        spectrum_peaks(q, rate, both)   # only meaningful on a quiet run

    if not e_moves or not np.any(flow):
        print("no extrusion in this window, so no flow fit (noise/dry run)")
        return

    lags = np.arange(0.0, a.max_lag, 1.0 / rate)
    sse, lag, coef, resid = fit(t, sig, flow, lags)
    gain = coef[0]
    fmax, fmin = flow.max(), flow[flow > 0].min()
    step = gain * (fmax - fmin)
    noise = resid.std()
    print("best lag: %.1f ms" % (lag * 1e3))
    print("gain: %+.5f Hz per mm³/s" % gain + (
        "  (%+.4f µm per mm³/s; + = pushed apart)" % (gain * k_um) if k_um else ""))
    print("lo→hi step (%.1f→%.1f mm³/s): %+.4f Hz" % (fmin, fmax, step)
          + ("  (%+.3f µm)" % (step * k_um) if k_um else "")
          + "; per-sample residual %.3f Hz RMS" % noise)

    # Consistency across cycles: refit each cycle separately at the global
    # lag. A real effect gives the same sign and size every cycle.
    gains = per_cycle_gains(t, sig, flow, lag)
    if len(gains) >= 2:
        g = np.array(gains)
        se = g.std(ddof=1) / np.sqrt(len(g))
        tstat = g.mean() / se if se else float("inf")
        unit = (lambda x: "%+.4f µm" % (x * k_um)) if k_um else (lambda x: "%+.3f Hz" % x)
        print("per-cycle step (%d cycles): %s" % (
            len(g), ", ".join(unit(x * (fmax - fmin)) for x in g)))
        print("cycle-to-cycle t-statistic: %.1f" % abs(tstat))
        verdict = ("CLEAR signal" if abs(tstat) > 5 else
                   "marginal: rerun with more --cycles" if abs(tstat) > 3 else
                   "not seen at this noise level")
        print("verdict: %s (|t| > 5 clear, 3–5 marginal, < 3 not seen)" % verdict)
    else:
        print("need 2+ cycles for a verdict")

    if a.plot:
        try:
            import matplotlib
        except ImportError:
            sys.exit("--plot needs matplotlib: ~/klippy-env/bin/pip install matplotlib")
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(3, 1, figsize=(11, 8), sharex=True)
        t0 = t[0]
        ax[0].plot(t - t0, flow, lw=1)
        ax[0].set_ylabel("flow mm³/s")
        ax[1].plot(t - t0, sig, lw=0.6, label="beacon")
        f_lag = np.interp(t - lag, t, flow)
        tn = (t - t.mean()) / (np.ptp(t) or 1.0)
        model = coef[0] * f_lag + coef[1] + coef[2] * tn + coef[3] * tn ** 2
        ax[1].plot(t - t0, model, lw=1, label="fit")
        ax[1].set_ylabel("freq Hz (detrended)")
        ax[1].legend(loc="upper right")
        ax[2].plot(t - t0, b["temp"][w], lw=1)
        ax[2].set_ylabel("coil °C")
        ax[2].set_xlabel("s")
        fig.tight_layout()
        out = os.path.join(a.run, "overview.png")
        fig.savefig(out, dpi=110)
        print("plot: %s" % out)


if __name__ == "__main__":
    main()
