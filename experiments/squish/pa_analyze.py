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


def passes(run, gap_tol=0.5):
    """Find passes in the toolhead trapq: runs of +X moves near the lowest Z.

    gap_tol must cover bed-mesh Z changes across the rows but stay below the
    1 mm lift between passes.

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
                    "t_acc": t_acc, "t_dec": t_dec, "vmax": vmax,
                    "y": gm[0][4][1]})
    return out


def pass_rows(run):
    """Measured passes from the G-code: list of (pass number, PA, row Y)."""
    p = os.path.join(run, "experiment.gcode")
    if not os.path.exists(p):
        return None
    rows, cur = [], None
    for line in open(p):
        m = re.match(r"; pass (\d+) pa=([\d.]+)", line)
        if m:
            cur = (int(m.group(1)), float(m.group(2)))
            continue
        if line.startswith("; warmup pass"):
            cur = None
            continue
        m = re.match(r"G1 X[\d.]+ Y([\d.]+) F", line)
        if m and cur is not None:
            rows.append((cur[0], cur[1], float(m.group(1))))
            cur = None
    return rows


def match_rows(rows, ps, tol=1.0):
    """Pair each G-code row with the detected pass that starts on its Y."""
    out = {}
    for n, pa, y in rows:
        best = min(ps, key=lambda p: abs(p["y"] - y), default=None)
        if best is not None and abs(best["y"] - y) < tol:
            out[n] = best
    return out


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
    grows = pass_rows(a.run)
    if not grows:
        sys.exit("no pass list in experiment.gcode (was the run made with pa_gen.py?)")
    wet = match_rows(grows, passes(a.run))
    missing = [n for n, _, _ in grows if n not in wet]
    if missing:
        print("passes not found in the motion data (left out): %s"
              % ",".join(map(str, missing)))

    dry = None
    if a.dry:
        td_, sd_, _, _ = signal(a.dry, a.comb_hz)
        dmatch = match_rows(pass_rows(a.dry) or grows, passes(a.dry))
        dmiss = [n for n, _, _ in grows if n in wet and n not in dmatch]
        if dmiss:
            print("passes missing from the dry run (no subtraction for them, "
                  "left out): %s" % ",".join(map(str, dmiss)))
        dry = (td_, sd_, dmatch)

    skip = {int(x) for x in a.skip.split(",") if x.strip()}
    used = [n for n, _, _ in grows if n in wet and n not in skip
            and (not dry or n in dry[2])]
    print("run: %s  (%d passes used%s, signal in %s%s)"
          % (a.run, len(used), ", skipped %s" % ",".join(map(str, sorted(skip)))
             if skip else "", unit, ", dry subtracted" if dry else ""))
    rows = []
    for i, pa, _y in grows:
        if i in skip or i not in wet:
            continue
        if dry and i not in dry[2]:
            continue
        p = wet[i]
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
    levels = sorted(set(pa_arr[ok]))
    means = []
    for v in levels:
        sel = ok & (pa_arr == v)
        means.append(M[sel].mean())
        print("  PA %.4f: M %+.4f (n=%d, spread %.4f)" % (
            v, means[-1], sel.sum(), M[sel].std() if sel.sum() > 1 else 0))
    # first sign change in the per-PA means, linearly interpolated: does not
    # assume M is a straight line over the whole range
    for j in range(len(levels) - 1):
        if means[j] > 0 >= means[j + 1]:
            x = levels[j] + (levels[j + 1] - levels[j]) * means[j] / (means[j] - means[j + 1])
            print("\nper-PA means cross zero at PA ≈ %.4f (between %.3f and %.3f)"
                  % (x, levels[j], levels[j + 1]))
            break
    else:
        print("\nper-PA means never cross zero in the tested range")
    if ok.sum() >= 3 and np.ptp(pa_arr[ok]) > 0:
        coef, cov = np.polyfit(pa_arr[ok], M[ok], 1, cov=True)
        slope, icpt = coef
        resid = M[ok] - np.polyval(coef, pa_arr[ok])
        r2 = 1 - resid.var() / M[ok].var() if M[ok].var() > 0 else 0
        print("straight-line fit: M = %+.4f %+.4f·PA   (R² %.2f)" % (icpt, slope, r2))
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
