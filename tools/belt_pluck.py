#!/usr/bin/env python3
"""Compare belt tensions by plucking belts while the Beacon accelerometer
records, then finding each pluck's ringing frequency.

A belt span rings at f = (1/2L) * sqrt(T/m), so at equal span length the
tension ratio of two belts is (f1/f2)^2. Plucks are found automatically;
groups of plucks separated by a pause of more than --gap seconds are
treated as one belt each (e.g. 5 plucks left Y, pause, 5 plucks right Y).

Record (on the Pi, toolhead parked, motors on):
    cd ~/3dprint && python3 tools/kdata.py record ~/belts/y \\
        --stream beacon/dump_accel --duration 45
Analyse:
    ~/klippy-env/bin/python tools/belt_pluck.py ~/belts/y --names "Y left,Y right"

Options: --band 40,300 (Hz searched), --span MM and --mass KG_PER_M for a
rough absolute tension (T = 4 m L^2 f^2; 9 mm GT2 ~0.012 kg/m, 6 mm ~0.008),
--plucks to list every pluck. Needs numpy (Klipper's ~/klippy-env has it).
"""
import argparse
import json
import os
import sys

import numpy as np


def load_accel(run):
    meta = json.load(open(os.path.join(run, "meta.json")))
    for spec, s in meta["streams"].items():
        if spec.split(":")[0] == "beacon/dump_accel":
            path = os.path.join(run, s["file"])
            h = s.get("header")
            # Beacon sends this stream's header as a message, not in the
            # subscribe reply; its layout is fixed: time, x, y, z
            cols = (h.get("header") if isinstance(h, dict) else None) \
                or ["time", "x", "y", "z"]
            break
    else:
        sys.exit("%s: no beacon/dump_accel stream recorded" % run)
    it, ix, iy, iz = (cols.index(c) for c in ("time", "x", "y", "z"))
    rows = []
    with open(path) as f:
        for line in f:
            prm = json.loads(line).get("params")
            if not isinstance(prm, list):        # e.g. a {"header": ...} message
                continue
            for r in prm:
                if not isinstance(r, (list, tuple)) or len(r) < 4 or r[ix] is None:
                    continue
                rows.append((r[it], r[ix], r[iy], r[iz]))
    if len(rows) < 1000:
        sys.exit("%s: only %d accelerometer samples" % (run, len(rows)))
    a = np.array(rows, dtype=float)
    a = a[np.argsort(a[:, 0])]
    return a[:, 0], a[:, 1:]


def spectrum(seg, rate, nfft):
    """Summed power spectrum of the three axes (orientation-independent)."""
    seg = seg - seg.mean(axis=0)
    w = np.hanning(len(seg))[:, None]
    P = np.abs(np.fft.rfft(seg * w, n=nfft, axis=0)) ** 2
    return np.fft.rfftfreq(nfft, 1.0 / rate), P.sum(axis=1)


def peak_freq(f, P, lo, hi):
    sel = np.flatnonzero((f >= lo) & (f <= hi))
    k = sel[np.argmax(P[sel])]
    if 0 < k < len(P) - 1:                       # parabolic interpolation
        a, b, c = np.log(P[k - 1:k + 2] + 1e-30)
        d = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
        return f[k] + d * (f[1] - f[0])
    return f[k]


def top_peaks(f, P, lo, hi, n=3, min_sep=4.0):
    sel = (f >= lo) & (f <= hi)
    idx = [i for i in np.flatnonzero(sel)[1:-1]
           if P[i] >= P[i - 1] and P[i] >= P[i + 1] and P[i] > 0]
    idx.sort(key=lambda i: -P[i])
    if idx:
        idx = [i for i in idx if P[i] > 0.1 * P[idx[0]]]   # ignore leftovers
    out = []
    for i in idx:
        if all(abs(f[i] - f[j]) > min_sep for j in out):
            out.append(i)
        if len(out) == n:
            break
    return [(f[i], P[i]) for i in out]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("run")
    ap.add_argument("--names", default="", help="comma-separated group names")
    ap.add_argument("--band", default="40,300", help="Hz range to search")
    ap.add_argument("--gap", type=float, default=3.0,
                    help="pause (s) that separates groups")
    ap.add_argument("--window", type=float, default=0.5,
                    help="seconds of ringing analysed per pluck")
    ap.add_argument("--thresh", type=float, default=8.0,
                    help="pluck = band energy this many times the background")
    ap.add_argument("--span", type=float, help="free span length, mm")
    ap.add_argument("--mass", type=float, default=0.012, help="belt kg/m")
    ap.add_argument("--plucks", action="store_true", help="list every pluck")
    a = ap.parse_args()
    lo, hi = (float(v) for v in a.band.split(","))

    t, x = load_accel(a.run)
    rate = 1.0 / np.median(np.diff(t))
    n = int(a.window * rate)
    nfft = 1 << int(np.ceil(np.log2(rate / 0.25)))   # ~0.25 Hz bins
    print("%s: %.1f s of accelerometer data at %.0f Hz"
          % (a.run, t[-1] - t[0], rate))
    if hi > 0.45 * rate:
        hi = 0.45 * rate

    # band energy in short blocks, to find plucks
    F = np.fft.rfft(x - x.mean(axis=0), axis=0)
    ff = np.fft.rfftfreq(len(x), 1.0 / rate)
    F[(ff < lo) | (ff > hi)] = 0
    xb = np.fft.irfft(F, n=len(x), axis=0)
    blk = max(1, int(0.02 * rate))
    nb = len(xb) // blk
    e = (xb[:nb * blk] ** 2).sum(axis=1).reshape(nb, blk).mean(axis=1)
    base = np.median(e)
    hot = e > a.thresh * base

    onsets = []
    i = 0
    while i < nb:
        if hot[i]:
            onsets.append(i * blk)
            i += int((a.window + 0.2) * rate / blk) + 1   # skip this pluck's ringing
        else:
            i += 1
    if not onsets:
        sys.exit("no plucks found (try a lower --thresh, or pluck harder)")

    # background spectrum from quiet stretches, to subtract motor/fan lines
    quiet = [k for k in range(0, len(x) - n, n)
             if not hot[k // blk:(k + n) // blk + 1].any()]
    if quiet:
        fb, Pb = spectrum(x[quiet[0]:quiet[0] + n], rate, nfft)
        for k in quiet[1:]:
            Pb += spectrum(x[k:k + n], rate, nfft)[1]
        Pb /= len(quiet)
    else:
        Pb = 0.0

    plucks = []
    for k in onsets:
        s0 = k + int(0.03 * rate)                 # skip the finger contact
        if s0 + n > len(x):
            continue
        f, P = spectrum(x[s0:s0 + n], rate, nfft)
        Pe = np.clip(P - Pb, 0, None)
        plucks.append({"t": t[k], "f": peak_freq(f, Pe, lo, hi), "P": Pe})
    if not plucks:
        sys.exit("plucks too close to the end of the recording")

    groups = [[plucks[0]]]
    for p in plucks[1:]:
        if p["t"] - groups[-1][-1]["t"] > a.gap + a.window:
            groups.append([p])
        else:
            groups[-1].append(p)
    names = [s.strip() for s in a.names.split(",") if s.strip()]

    print("found %d plucks in %d groups\n" % (len(plucks), len(groups)))
    res = []
    for gi, g in enumerate(groups):
        name = names[gi] if gi < len(names) else "group %d" % (gi + 1)
        fs = np.array([p["f"] for p in g])
        Pm = np.mean([p["P"] for p in g], axis=0)
        med = float(np.median(fs))
        spread = float(np.percentile(fs, 75) - np.percentile(fs, 25)) if len(fs) > 1 else 0.0
        res.append((name, med))
        line = "%-12s %2d plucks  %6.1f Hz  (spread %.1f Hz)" % (name, len(g), med, spread)
        if a.span:
            L = a.span / 1000.0
            line += "  ~%.0f N" % (4 * a.mass * L * L * med * med)
        print(line)
        print("             strongest peaks in the averaged ringing: " +
              ", ".join("%.1f" % pf for pf, _ in top_peaks(f, Pm, lo, hi)))
        if a.plucks:
            for p in g:
                print("               t=%7.2f s  %.1f Hz" % (p["t"] - t[0], p["f"]))
        if len(g) >= 3 and spread > 0.05 * med:
            print("             (plucks disagree: check the peaks list; the belt"
                  " may ring at f and 2f)")

    if len(res) >= 2:
        print("\ntension relative to %s (same span length assumed):" % res[0][0])
        for name, med in res[1:]:
            r = (med / res[0][1]) ** 2
            print("  %-12s %.2f x  (%+.0f%%)" % (name, r, 100 * (r - 1)))
        print("within ~5% is matched as well as plucking can tell")


if __name__ == "__main__":
    main()
