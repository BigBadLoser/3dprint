#!/usr/bin/env python3
"""Write a fake belt-pluck recording in kdata.py's format, for testing
belt_tension.py without the printer.

    python3 tools/belt_synth.py /tmp/fake --belts 110,104 && \\
        python3 tools/belt_tension.py /tmp/fake --names "Y left,Y right" --plucks

Layout: hands-off background, taps on the toolhead, then a group of plucks
per belt. The background has broadband noise plus a fan line; taps ring the
toolhead's modes briefly; a pluck rings at f and (louder) 2f for a long time
and also kicks the toolhead modes. Options add knocks and a weak 2f.
"""
import argparse
import json
import os

import numpy as np


def make(belts=(110.0, 104.0), fs=3200.0, plucks=6, taps=3, knock_in=None,
         modes=((132.0, 0.04), (215.0, 0.025)), fan=240.0, noise=30.0,
         amp=400.0, ratio_2f=1.6, tau=0.5, jitter=0.05, seed=1):
    rng = np.random.default_rng(seed)
    events = []            # (time, kind, belt freq)
    t = 5.0
    for _ in range(taps):
        events.append((t, "tap", None))
        t += 1.0
    t += 4.0
    for bi, fb in enumerate(belts):
        for k in range(plucks):
            events.append((t, "pluck", fb + rng.normal(0, jitter)))
            t += 1.1
            if knock_in == bi and k == plucks // 2:
                events.append((t, "tap", None))
                t += 1.1
        t += 4.0
    dur = t + 1.0
    n = int(dur * fs)
    tt = np.arange(n) / fs
    x = rng.normal(0, noise, (n, 3))
    x += 0.8 * noise * np.sin(2 * np.pi * fan * tt)[:, None] * np.array([1.0, 0.5, 0.2])
    x[:, 2] += 9810.0                                 # gravity on z
    for te, kind, fb in events:
        i0 = int(te * fs)
        s = tt[i0:] - tt[i0]
        a = amp * rng.uniform(0.7, 1.3)
        sig = np.zeros((len(s), 3))
        dirs = rng.normal(size=(3, 3))
        dirs /= np.linalg.norm(dirs, axis=1)[:, None]
        if kind == "tap":
            sig[:min(len(s), 3)] += 3 * a * dirs[0]   # impulse
            for (fm, tm), d in zip(modes, dirs[1:]):
                sig += (a * np.exp(-s / tm) * np.sin(2 * np.pi * fm * s))[:, None] * d
        else:
            env = np.exp(-s / tau)
            sig += (0.5 * a * env * np.sin(2 * np.pi * fb * s))[:, None] * dirs[0]
            sig += (0.5 * a * ratio_2f * env * np.sin(2 * np.pi * 2 * fb * s + 0.7))[:, None] * dirs[1]
            for (fm, tm), d in zip(modes, dirs[1:]):  # the pluck kicks the toolhead too
                sig += (0.4 * a * np.exp(-s / tm) * np.sin(2 * np.pi * fm * s))[:, None] * d
        x[i0:] += sig
    return tt, x, events


def write(outdir, tt, x, t0=1000.0, chunk=64):
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "accel.jsonl"), "w") as f:
        for i in range(0, len(tt), chunk):
            rows = [[round(t0 + tt[j], 6)] + [round(v, 2) for v in x[j]]
                    for j in range(i, min(i + chunk, len(tt)))]
            f.write(json.dumps({"recv": t0 + tt[i], "params": rows}) + "\n")
    meta = {"streams": {"beacon/dump_accel": {"file": "accel.jsonl", "header": None}},
            "synthetic": True}
    with open(os.path.join(outdir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("outdir")
    ap.add_argument("--belts", default="110,104", help="belt tones, Hz")
    ap.add_argument("--fs", type=float, default=3200.0)
    ap.add_argument("--plucks", type=int, default=6)
    ap.add_argument("--knock-in", type=int, help="add a knock to this belt group (0-based)")
    ap.add_argument("--ratio-2f", type=float, default=1.6, help="2f amplitude / f amplitude")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    belts = [float(v) for v in a.belts.split(",")]
    tt, x, ev = make(belts, a.fs, a.plucks, knock_in=a.knock_in,
                     ratio_2f=a.ratio_2f, seed=a.seed)
    write(a.outdir, tt, x)
    print("wrote %s: %.1f s, %d events" % (a.outdir, tt[-1], len(ev)))


if __name__ == "__main__":
    main()
