#!/usr/bin/env python3
"""Measure line width from a phone photo of a pa_gen.py pattern (experimental).

    ./photo_width.py photo.jpg --gcode ~/squish/pa_7/experiment.gcode \
        --start left --first bottom

--start   which side of the photo the lines START on (the end with the round
          blob where the nozzle pauses): left or right
--first   which row was printed first (the warm-up row, usually missing its
          start): top or bottom

For each row it measures the line's width from its outer edges (robust to
ASA lifting off the bed, which darkens the middle), then compares the width
just after the speed-up with the steady fast section. Too little PA: thinner
after the speed-up (< 1). Too much: wider (> 1). The PA where the per-PA mean
crosses 1.00 is where the line stays uniform.

Photo tips: straight down, even light from the side (no glare), lines filling
the frame, full resolution. Needs numpy and Pillow.
"""
import argparse
import collections
import re
import sys

import numpy as np
from PIL import Image


def gcode_layout(path):
    """PA order, warm-up count, X range and slow length from pa_gen G-code."""
    txt = open(path).read()
    pas = [float(m) for m in re.findall(r"; pass \d+ pa=([\d.]+)", txt)]
    m = re.search(r"; warmup passes: (\d+)", txt)
    nwarm = int(m.group(1)) if m else 0
    moves = re.findall(r"G1 X([\d.]+) (?:E[\d.]+ )?F(\d+)", txt)
    xs = [float(x) for x, _ in moves[:3]]
    starts = re.findall(r"G1 X([\d.]+) Y[\d.]+ F9000", txt)
    x0 = float(starts[0]) if starts else 45.0
    return pas, nwarm, x0, xs  # xs = [end of slow, end of fast, end of line]


def find_rows(o, n):
    H, W = o.shape
    prof = o[:, int(W * 0.3):int(W * 0.7)].mean(axis=1)
    prof = prof - np.convolve(prof, np.ones(41) / 41, "same")
    peaks = np.array([y for y in range(5, H - 5)
                      if prof[y] == prof[y - 5:y + 6].max() and prof[y] > 6])
    if len(peaks) < max(4, n // 3):
        sys.exit("found only %d rows; check lighting/contrast" % len(peaks))
    # fit an evenly spaced grid of n rows to the detected peaks
    best = None
    lo, hi = np.median(np.diff(peaks)) * 0.7, np.median(np.diff(peaks)) * 1.3
    for sp in np.arange(lo, hi, 0.05):
        for y0 in np.arange(peaks[0] - 1.5 * sp, peaks[0] + 0.5 * sp, 0.5):
            k = np.round((peaks - y0) / sp)
            inside = (k >= 0) & (k < n)
            if inside.sum() < 0.6 * len(peaks):
                continue
            err = np.abs(peaks[inside] - (y0 + k[inside] * sp)).mean() \
                + 3.0 * (~inside).sum()
            if best is None or err < best[0]:
                best = (err, y0, sp)
    if best is None:
        sys.exit("could not fit a %d-row grid" % n)
    return best[1], best[2]


def row_width(o, yc, half):
    band = o[yc - half:yc + half + 1, :]
    bg = np.median(np.r_[o[yc - half - 3:yc - half, :],
                         o[yc + half + 1:yc + half + 4, :]], axis=0)
    d = band - bg
    thr = 0.5 * np.percentile(d.max(axis=0), 90)
    width = np.zeros(d.shape[1])
    for x in range(d.shape[1]):
        idx = np.flatnonzero(d[:, x] > thr)
        if len(idx) >= 2:
            a, b = idx[0], idx[-1]
            fa = ((thr - d[a - 1, x]) / (d[a, x] - d[a - 1, x])
                  if a > 0 and d[a, x] != d[a - 1, x] else 0)
            fb = ((d[b, x] - thr) / (d[b, x] - d[b + 1, x])
                  if b + 1 < d.shape[0] and d[b, x] != d[b + 1, x] else 0)
            width[x] = (b - a) + (1 - fa) + fb
    return width


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("photo")
    ap.add_argument("--gcode", required=True, help="experiment.gcode of the run")
    ap.add_argument("--start", choices=["left", "right"], required=True)
    ap.add_argument("--first", choices=["top", "bottom"], required=True)
    ap.add_argument("--rows", action="store_true", help="print every row")
    a = ap.parse_args()

    pas, nwarm, x0, (xs_slow, xs_fast, x1) = gcode_layout(a.gcode)
    nrows = nwarm + len(pas)
    im = np.asarray(Image.open(a.photo).convert("RGB")).astype(float)
    o = im[..., 0] - im[..., 2]          # orange-ness: works for orange/red/yellow
    y0, sp = find_rows(o, nrows)
    row_mm = 3.0                          # pa_gen default --row
    pxmm = sp / row_mm
    half = int(sp * 0.45)
    print("photo %s: %d rows, %.1f px per row → %.2f px/mm"
          % (a.photo, nrows, sp, pxmm))

    g = collections.defaultdict(list)
    for k in range(nrows):
        yc = int(round(y0 + k * sp))
        idx = k if a.first == "top" else nrows - 1 - k   # print order
        w = np.convolve(row_width(o, yc, half), np.ones(5) / 5, "same") / pxmm
        on = np.flatnonzero(w > 0.3)
        if len(on) < 50:
            continue
        xa, xb = on.min(), on.max()
        xs = np.arange(len(w))
        frac = (xs - xa) / (xb - xa) if a.start == "left" else (xb - xs) / (xb - xa)
        X = x0 + frac * (x1 - x0)

        def med(X0, X1):
            sel = (X >= X0) & (X < X1)
            v = w[sel]
            return np.median(v) if len(v) else np.nan

        up = med(xs_slow + 0.5, xs_slow + 5.0)
        fast = med(xs_slow + 15.0, xs_fast - 3.0)
        ratio = up / fast if fast > 0 else np.nan
        if idx < nwarm:
            label = "warm-up"
        else:
            pa = pas[idx - nwarm]
            label = "pass %2d PA %.3f" % (idx - nwarm, pa)
            if np.isfinite(ratio) and 0.3 < ratio < 3:
                g[pa].append(ratio)
        if a.rows:
            print("  %-18s after speed-up %.3f mm, steady fast %.3f mm, ratio %.3f"
                  % (label, up, fast, ratio))

    print("\nPA      width after speed-up / steady fast  (1.00 = uniform)")
    lv = sorted(g)
    mv = []
    for v in lv:
        r = np.array(g[v])
        mv.append(r.mean())
        print("%.3f   %.3f ± %.3f  (n=%d)" % (v, r.mean(), r.std(), len(r)))
    for j in range(len(lv) - 1):
        if (mv[j] - 1) * (mv[j + 1] - 1) <= 0 and mv[j] != mv[j + 1]:
            x = lv[j] + (lv[j + 1] - lv[j]) * (1 - mv[j]) / (mv[j + 1] - mv[j])
            print("\nuniform width at PA ≈ %.4f" % x)
            break
    else:
        if len(lv) >= 2:
            c = np.polyfit(lv, mv, 1)
            print("\nno crossing in range; straight-line extrapolation: PA ≈ %.4f"
                  % ((1 - c[1]) / c[0]))


if __name__ == "__main__":
    main()
