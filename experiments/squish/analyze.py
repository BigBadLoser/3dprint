#!/usr/bin/env python3
"""Analyze a squish run recorded by tools/kdata.py.

Fits   beacon_dist(t) = k * flow(t - lag) + slow drift
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
    path, header = find_stream(run, "beacon/dump")
    if path is None:
        sys.exit("%s: no beacon/dump stream" % run)
    cols = header["header"]
    it, idist, ifreq, itemp = (cols.index(c) for c in ("time", "dist", "freq", "temp"))
    rows = []
    for m in load_jsonl(path):
        for r in m["params"] or []:
            if r[idist] is None:
                continue
            rows.append((r[it], r[idist], r[ifreq], r[itemp]))
    a = np.array(rows, dtype=float)
    a = a[np.argsort(a[:, 0])]
    return {"t": a[:, 0], "dist": a[:, 1], "freq": a[:, 2], "temp": a[:, 3]}


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
    t = b["t"]
    dt = np.diff(t)
    rate = 1.0 / np.median(dt)
    d_um = (b["dist"] - b["dist"].mean()) * 1000.0

    e_moves = load_trapq(a.run, "extruder")
    flow = trapq_velocity(e_moves, t) * FIL_AREA if e_moves else np.zeros_like(t)

    if a.dry:
        bd = load_beacon(a.dry)
        off = line_start(load_trapq(a.run, "toolhead")) - \
            line_start(load_trapq(a.dry, "toolhead"))
        dry_um = (bd["dist"] - bd["dist"].mean()) * 1000.0
        d_um = d_um - np.interp(t, bd["t"] + off, dry_um)

    print("run: %s" % a.run)
    print("beacon: %d samples, %.1f Hz median rate, %.1f s, coil %.1f–%.1f °C"
          % (len(t), rate, np.ptp(t), b["temp"].min(), b["temp"].max()))
    print("sample interval jitter: p1 %.2f ms, p99 %.2f ms, max gap %.1f ms"
          % (np.percentile(dt, 1) * 1e3, np.percentile(dt, 99) * 1e3, dt.max() * 1e3))

    quiet = flow == 0
    if e_moves and quiet.sum() > 50:
        # noise = high-pass residual in quiet periods (remove slow drift)
        q = d_um[quiet]
        k = max(1, int(rate * 0.2))
        hp = q - np.convolve(q, np.ones(k) / k, mode="same")
        print("noise (quiet, 0.2 s high-pass): %.3f µm RMS" % hp[k:-k].std())
    else:
        print("noise (whole trace, detrended): %.3f µm RMS"
              % np.std(d_um - np.polyval(np.polyfit(t, d_um, 2), t)))

    if not e_moves or not np.any(flow):
        print("no extrusion in this run, so no flow fit (noise/dry run)")
        return

    lags = np.arange(0.0, a.max_lag, 1.0 / rate)
    sse, lag, coef, resid = fit(t, d_um, flow, lags)
    gain = coef[0]
    fmax, fmin = flow.max(), flow[flow > 0].min()
    step = gain * (fmax - fmin)
    noise = resid.std()
    print("best lag: %.1f ms" % (lag * 1e3))
    print("gain: %+.4f µm per mm³/s  (positive = nozzle/bed pushed apart)" % gain)
    print("lo→hi step (%.1f→%.1f mm³/s): %+.3f µm vs residual %.3f µm RMS → SNR %.1f"
          % (fmin, fmax, step, noise, abs(step) / noise if noise else float("inf")))
    print("verdict hint: SNR > 5 promising, 1–5 marginal (average more cycles), < 1 not visible")

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
        ax[1].plot(t - t0, d_um, lw=0.6, label="beacon")
        f_lag = np.interp(t - lag, t, flow)
        tn = (t - t.mean()) / (np.ptp(t) or 1.0)
        model = coef[0] * f_lag + coef[1] + coef[2] * tn + coef[3] * tn ** 2
        ax[1].plot(t - t0, model, lw=1, label="fit")
        ax[1].set_ylabel("dist µm")
        ax[1].legend(loc="upper right")
        ax[2].plot(t - t0, b["temp"], lw=1)
        ax[2].set_ylabel("coil °C")
        ax[2].set_xlabel("s")
        fig.tight_layout()
        out = os.path.join(a.run, "overview.png")
        fig.savefig(out, dpi=110)
        print("plot: %s" % out)


if __name__ == "__main__":
    main()
