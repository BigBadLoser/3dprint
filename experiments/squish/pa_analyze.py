#!/usr/bin/env python3
"""Analyse a moving pressure-advance squish run (made with pa_gen.py).

    ./pa_analyze.py ~/squish/pa_1 [--dry ~/squish/pa_dry_1]

For every pass it measures the squeeze-force blip right after the speed-up
and right after the slow-down, relative to the steady level that follows:

    A_up   = ∫ (force − steady fast level)  over 0.35 s after the speed-up
    A_down = ∫ (force − steady slow level)  over 0.50 s after the slow-down
    M      = A_down − A_up

Too little PA: the line thins after speeding up (A_up < 0) and bulges after
slowing down (A_down > 0), so M > 0. Too much PA: M < 0. The suggested PA is
where a straight-line fit of M against PA crosses zero.

Motion artifacts (gantry tilt while accelerating) are the same in every pass
and shift M by a constant, which moves the zero crossing. --dry subtracts a
run with identical moves and no plastic, which removes them.
"""
import argparse
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze as A  # noqa: E402


def passes(run, gap_tol=0.05):
    """Find passes in the toolhead trapq: runs of +X moves at the lowest Z.

    Returns a list of dicts with start/end and the speed-up and slow-down
    times (start of the accel and decel segments between slow and fast)."""
    moves = [m for m in A.load_trapq(run, "toolhead")]
    xs = [m for m in moves if m[5][0] > 0.9 and m[1] > 0]
    if not xs:
        return []
    zmin = min(m[4][2] for m in xs)
    xs = [m for m in xs if m[4][2] < zmin + gap_tol]
    groups, cur = [], [xs[0]]
    for m in xs[1:]:
        prev = cur[-1]
        if m[0] - (prev[0] + prev[1]) < 0.05:
            cur.append(m)
        else:
            groups.append(cur)
            cur = [m]
    groups.append(cur)
    out = []
    for gm in groups:
        vmax = max(m[2] + max(m[3], 0) * m[1] for m in gm)
        t_acc = t_dec = None
        for m in gm:
            v0, acc = m[2], m[3]
            if acc > 0 and v0 > 0.05 * vmax and t_acc is None:
                t_acc = m[0]
            if acc < 0 and t_acc is not None and t_dec is None and \
                    v0 + acc * m[1] > 0.05 * vmax:
                t_dec = m[0]
        if t_acc is None or t_dec is None:
            continue
        out.append({"t0": gm[0][0], "t1": gm[-1][0] + gm[-1][1],
                    "t_acc": t_acc, "t_dec": t_dec, "vmax": vmax})
    return out


def pass_pas(run):
    """(number of warm-up passes, PA of each measured pass) from the G-code."""
    p = os.path.join(run, "experiment.gcode")
    if not os.path.exists(p):
        return 0, None
    txt = open(p).read()
    m = re.search(r"; warmup passes: (\d+)", txt)
    return (int(m.group(1)) if m else 0,
            [float(x.group(1)) for x in re.finditer(r"; pass \d+ pa=([\d.]+)", txt)])


def signal(run, comb_hz=10.0):
    """Beacon signal in µm (+ = nozzle and bed pushed apart), hum removed."""
    b = A.load_beacon(run)
    rate = 1.0 / np.median(np.diff(b["t"]))
    w, _ = A.test_window(b)
    k = A.um_per_hz(b, w)
    x = b["freq"] - np.median(b["freq"][w])
    if comb_hz > 0:
        n = max(1, int(round(rate / comb_hz)))
        x = np.convolve(x, np.ones(n) / n, mode="same")
    if k:
        return b["t"], x * k, rate, "µm"
    return b["t"], -x, rate, "Hz (sign flipped)"


def mean_in(t, s, a, b):
    m = (t >= a) & (t < b)
    return s[m].mean() if m.sum() > 5 else np.nan


def area(t, s, a, b, ref):
    m = (t >= a) & (t < b)
    if m.sum() < 5:
        return np.nan
    return float(np.trapezoid(s[m] - ref, t[m]) if hasattr(np, "trapezoid")
                 else np.trapz(s[m] - ref, t[m]))


def metrics(t, s, p, up_win=0.35, down_win=0.50):
    ta, td, t1 = p["t_acc"], p["t_dec"], p["t1"]
    fast_ref = mean_in(t, s, td - 0.15, td - 0.02)
    slow_ref = mean_in(t, s, ta - 0.5, ta - 0.05)
    slow_after = mean_in(t, s, t1 - 0.35, t1 - 0.05)
    a_up = area(t, s, ta, ta + up_win, fast_ref)
    a_down = area(t, s, td, td + down_win, slow_after)
    return {"slow": slow_ref, "fast": fast_ref, "a_up": a_up,
            "a_down": a_down, "M": a_down - a_up}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("run")
    ap.add_argument("--dry")
    ap.add_argument("--comb-hz", type=float, default=10.0,
                    help="moving average over one period of this tone (chamber\n"
                         "heater PWM); 0 = off, for runs with the chamber heater off")
    ap.add_argument("--skip", default="",
                    help="comma-separated measured pass numbers to leave out")
    a = ap.parse_args()

    t, s, rate, unit = signal(a.run, a.comb_hz)
    ps = passes(a.run)
    nwarm, pas = pass_pas(a.run)
    ps = ps[nwarm:]
    if not ps:
        sys.exit("no passes found in the toolhead trapq")
    if pas is None or len(pas) != len(ps):
        sys.exit("found %d passes but %s PA values in experiment.gcode"
                 % (len(ps), "no" if pas is None else len(pas)))

    dry = None
    if a.dry:
        td_, sd_, _, _ = signal(a.dry, a.comb_hz)
        pd = passes(a.dry)[pass_pas(a.dry)[0]:]
        if len(pd) != len(ps):
            sys.exit("dry run has %d passes, wet run %d" % (len(pd), len(ps)))
        dry = (td_, sd_, pd)

    print("run: %s  (%d passes, signal in %s%s)"
          % (a.run, len(ps), unit, ", dry subtracted" if dry else ""))
    skip = {int(x) for x in a.skip.split(",") if x.strip()}
    rows = []
    for i, (p, pa) in enumerate(zip(ps, pas)):
        if i in skip:
            continue
        m = (t >= p["t0"] - 0.6) & (t <= p["t1"] + 0.1)
        tp, sp = t[m], s[m].copy()
        if dry:
            tdry, sdry, pdry = dry
            off = p["t_acc"] - pdry[i]["t_acc"]
            sp = sp - np.interp(tp, tdry + off, sdry)
        r = metrics(tp, sp, p)
        r["pa"] = pa
        r["i"] = i
        rows.append(r)

    print("(slow/fast levels relative to the first pass's slow level)")
    print("%4s %7s %9s %9s %9s %9s %9s" % ("pass", "PA", "slow", "fast",
                                          "A_up", "A_down", "M"))
    for r in rows:
        print("%4d %7.4f %9.3f %9.3f %9.4f %9.4f %9.4f" % (
            r["i"], r["pa"], r["slow"] - rows[0]["slow"], r["fast"] - rows[0]["slow"],
            r["a_up"], r["a_down"], r["M"]))

    pa_arr = np.array([r["pa"] for r in rows])
    M = np.array([r["M"] for r in rows])
    ok = np.isfinite(M)
    print("\nper-PA mean M:")
    for v in sorted(set(pa_arr[ok])):
        sel = ok & (pa_arr == v)
        print("  PA %.4f: M %+.4f (n=%d, spread %.4f)" % (
            v, M[sel].mean(), sel.sum(), M[sel].std() if sel.sum() > 1 else 0))
    if ok.sum() >= 3 and np.ptp(pa_arr[ok]) > 0:
        coef, cov = np.polyfit(pa_arr[ok], M[ok], 1, cov=True)
        slope, icpt = coef
        resid = M[ok] - np.polyval(coef, pa_arr[ok])
        r2 = 1 - resid.var() / M[ok].var() if M[ok].var() > 0 else 0
        print("\nfit: M = %+.4f %+.4f·PA   (R² %.2f)" % (icpt, slope, r2))
        if slope < 0:
            pa0 = -icpt / slope
            # delta method for pa0 = -b/a with coef = (a, b)
            va, vb, cab = cov[0, 0], cov[1, 1], cov[0, 1]
            se = np.sqrt(max(0.0, vb + pa0 ** 2 * va + 2 * pa0 * cab)) / abs(slope)
            print("M crosses zero at PA = %.4f ± %.4f" % (pa0, se))
            if not (pa_arr.min() <= pa0 <= pa_arr.max()):
                print("  (outside the tested range: widen --pa-list)")
        else:
            print("M does not fall as PA rises: no usable PA signal in this run")
        if r2 < 0.5:
            print("  low R²: treat the crossing as rough")
    if not dry:
        print("\nwithout --dry, motion artifacts can shift the crossing")


if __name__ == "__main__":
    main()
