#!/usr/bin/env python3
"""Belt tension from plucks, heard by the Beacon accelerometer.

Successor to belt_pluck.py, which took the loudest peak in a band and so
jumped between the belt's tone, twice the tone and the toolhead's own
ringing (Y left came out 293 vs 188 Hz). This one:

  1. learns the background (fans, motors) from a hands-off stretch at the
     start of the recording;
  2. learns the toolhead's own lines from a few taps on the toolhead, and
     ignores belt candidates that sit on them;
  3. reads each pluck after the first 30 ms (the frame rings briefly, a belt
     rings on) and looks for the tone f and twice the tone 2f together: a
     plucked belt pulls on its carriage twice per swing, so 2f is often the
     louder line. A pluck needs one line >= 12 dB over the background and
     the other >= 6 dB;
  4. rejects knocks (no 2f, dies away quickly) and plucks that disagree.

A span rings at f = sqrt(T/m) / (2L), so at equal span length the tension
ratio of two belts is (f1/f2)^2, and T = 4 m L^2 f^2.

Recording, one file per session (on the Pi, toolhead parked near the belts,
motors on, chamber heater off):

    python3 tools/kdata.py record ~/belts/s1 --stream beacon/dump_accel --duration 90

  - 0-4 s    hands off (background; --quiet sets the length)
  - then     tap one solid spot on the toolhead 3-5 times, about 1 s apart
  - pause    > 3 s (--gap)
  - then     5+ plucks on the first belt, ~1 s apart, pause, the next belt, ...

Analyse (anywhere with numpy):

    python3 tools/belt_tension.py ~/belts/s1 --names "Y left,Y right" --plucks

Options: --target HZ narrows the search to 0.55-1.5 x target; --band LO,HI
otherwise (default 40,200); --span MM and --mass KG_PER_M add a rough
absolute tension (9 mm GT2 ~0.012 kg/m, 6 mm ~0.008); --no-taps if the
recording has no tap group; --json FILE writes everything for later use.
Names are compared in pairs (1st vs 2nd, 3rd vs 4th).
"""
import argparse
import json
import math
import os
import sys

import numpy as np

SKIP = 0.03          # s after a pluck's onset: let the frame ring out
WINDOW = 0.6         # s of ringing read per pluck (less if the next comes sooner)
MIN_WINDOW = 0.2     # s: shorter than this and the pluck is not read
TAP_SKIP = 0.005     # s after a tap's onset
TAP_WINDOW = 0.12    # s read per tap (toolhead lines die within ~0.1 s)
DEAD = 0.45          # s: no new event sooner than this after the last
BLOCK = 0.01         # s: energy blocks for finding events
STRONG_DB = 12.0     # one line of a pluck must be this far over the background
WEAK_DB = 6.0        # and the other this far
ALONE_DB = 15.0      # a single line is enough when its partner is on a toolhead line
FRESH_DB = 6.0       # a pluck's line must rise this much at the onset
FRESH_WIN = 0.15     # s compared before the onset and after SKIP
KNOCK_TAU = 0.08     # s: a 1/e amplitude decay shorter than this is a knock, not a belt
TAP_MAX_TAU = 0.2    # s: tap lines ringing longer than this are belts, not toolhead
AGREE_HZ = 1.0       # plucks within this of the group median are averaged
SEARCH_MAX = 500.0   # Hz

_T95 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45, 7: 2.36,
        8: 2.31, 9: 2.26, 10: 2.23, 15: 2.13, 20: 2.09, 30: 2.04}


# ---------------------------------------------------------------- loading

def load_accel(run):
    """(t, xyz) from a kdata.py recording directory, sorted by time."""
    with open(os.path.join(run, "meta.json")) as f:
        meta = json.load(f)
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
        raise SystemExit("%s: no beacon/dump_accel stream recorded" % run)
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
        raise SystemExit("%s: only %d accelerometer samples" % (run, len(rows)))
    a = np.array(rows, dtype=float)
    a = a[np.argsort(a[:, 0], kind="stable")]
    return a[:, 0], a[:, 1:]


def uniform(t, x):
    """Resample onto an even grid; returns (t, x, fs, gaps) where gaps are
    (start, end) times of holes longer than 3 sample intervals."""
    d = np.diff(t)
    med = float(np.median(d))
    ok = d <= 3 * med
    dt = float(d[ok].mean())          # mean, not median: timestamps are rounded
    fs = 1.0 / dt
    gaps = [(t[i], t[i + 1]) for i in np.flatnonzero(~ok)]
    tu = np.arange(t[0], t[-1], dt)
    xu = np.column_stack([np.interp(tu, t, x[:, k]) for k in range(x.shape[1])])
    return tu, xu, fs, gaps


# ---------------------------------------------------------------- spectra

def psd(seg, fs, nfft):
    """Power spectral density summed over the axes (orientation-independent).
    Normalised so that broadband noise reads the same for any window length."""
    seg = seg - seg.mean(axis=0)
    w = np.hanning(len(seg))
    X = np.fft.rfft(seg * w[:, None], n=nfft, axis=0)
    return (np.abs(X) ** 2).sum(axis=1) / (fs * (w * w).sum())


def nfft_for(n, fs, res=0.05):
    return 1 << int(math.ceil(math.log2(max(n, fs / res))))


def running_max(p, h):
    if h < 1:
        return p
    v = np.lib.stride_tricks.sliding_window_view(np.pad(p, h, mode="edge"), 2 * h + 1)
    return v.max(axis=1)


def background(x, fs, i0, i1):
    """Background PSD from samples i0..i1: median over 0.6 s windows, then a
    running max over one main-lobe width so steady lines are fully covered."""
    n = int(WINDOW * fs)
    nfft = nfft_for(n, fs, 0.2)
    starts = list(range(i0, i1 - n + 1, n // 2))
    if len(starts) < 2:
        raise SystemExit("background stretch too short: need at least %.1f s "
                         "hands off at the start (--quiet)" % (1.5 * WINDOW))
    P = np.median([psd(x[s:s + n], fs, nfft) for s in starts], axis=0)
    f = np.fft.rfftfreq(nfft, 1.0 / fs)
    h = int(round((2.0 / WINDOW) / (f[1] - f[0])))
    return f, running_max(P, h)


def find_peaks(f, P, B, lo, hi, min_db, sep):
    """Local maxima between lo and hi standing min_db over B, at least sep
    apart (strongest kept). Frequency by parabolic interpolation on log P."""
    snr = 10 * np.log10(P / B)
    sel = np.flatnonzero((f >= lo) & (f <= hi))
    sel = sel[(sel > 0) & (sel < len(P) - 1)]
    cand = [i for i in sel if P[i] >= P[i - 1] and P[i] >= P[i + 1] and snr[i] >= min_db]
    cand.sort(key=lambda i: -P[i])
    kept = []
    for i in cand:
        if all(abs(f[i] - f[j]) >= sep for j in kept):
            kept.append(i)
    out = []
    for i in kept:
        a, b, c = np.log(P[i - 1:i + 2])
        den = a - 2 * b + c
        d = 0.5 * (a - c) / den if den != 0 else 0.0
        out.append({"f": float(f[i] + d * (f[1] - f[0])), "snr": float(snr[i]), "i": int(i)})
    return out


def line_level(seg, fs, freq):
    """Power of the line at freq in seg (Hann-windowed single DFT bin, summed
    over axes). Only comparable between segments of the same length."""
    n = len(seg)
    w = np.hanning(n)
    e = np.exp(-2j * np.pi * freq * np.arange(n) / fs) * w
    s = seg - seg.mean(axis=0)
    return float((np.abs(e @ s) ** 2).sum())


def decay_tau(seg, fs, freq):
    """1/e amplitude decay time (s) of the line at freq, from its level in the
    first vs the second half of seg. inf if it doesn't decay."""
    n = len(seg) // 2
    if n < 8:
        return float("inf")
    p = [line_level(seg[:n], fs, freq), line_level(seg[n:2 * n], fs, freq)]
    if p[1] <= 0 or p[1] >= p[0]:
        return float("inf")
    return 2 * (n / fs) / math.log(p[0] / p[1])


# ---------------------------------------------------------------- events

def find_events(x, fs, lo, hi, quiet_n, thresh_db):
    """Onset sample indices of taps/plucks: band energy in short blocks rising
    thresh_db over the background's median. A new event needs DEAD s since the
    last and either the energy to have dropped below threshold in between or a
    fresh rise of 6 dB."""
    F = np.fft.rfft(x - x.mean(axis=0), axis=0)
    ff = np.fft.rfftfreq(len(x), 1.0 / fs)
    F[(ff < lo) | (ff > hi)] = 0
    xb = np.fft.irfft(F, n=len(x), axis=0)
    blk = max(1, int(BLOCK * fs))
    nb = len(xb) // blk
    e = (xb[:nb * blk] ** 2).sum(axis=1).reshape(nb, blk).mean(axis=1)
    qb = max(1, quiet_n // blk)
    base = float(np.median(e[:qb]))
    thr = base * 10 ** (thresh_db / 10)
    dead = int(DEAD / BLOCK)
    onsets, last, armed, low = [], -10 ** 9, True, 0
    quiet_hot = bool((e[:qb] > thr).any())
    for i in range(qb, nb):
        # re-arm only after 50 ms clearly below threshold: a decaying ring
        # hovering at the threshold must not count again
        low = low + 1 if e[i] < thr / 2 else 0
        if low >= 5:
            armed = True
        if e[i] <= thr:
            continue
        # a fresh pluck during ringing: 6 dB over the loudest of the last
        # 0.2 s (beats between lines don't do that)
        rise = i >= 20 and e[i] > 4 * e[i - 20:i].max()
        if i - last >= dead and (armed or rise):
            onsets.append(i * blk)
            last, armed = i, False
    return onsets, quiet_hot


def group_events(onsets, fs, gap):
    groups = []
    for o in onsets:
        if groups and (o - groups[-1][-1]) / fs <= gap:
            groups[-1].append(o)
        else:
            groups.append([o])
    return groups


# ---------------------------------------------------------------- taps

def learn_toolhead(x, fs, taps, B_f, B_P, lo, hi):
    """Toolhead lines from the taps: lines >= STRONG_DB that die away quickly,
    seen in at least two taps (or the only tap). Returns [(centre, guard)]."""
    seen = []
    for k, o in enumerate(taps):
        s0 = o + int(TAP_SKIP * fs)
        end = taps[k + 1] if k + 1 < len(taps) else len(x)
        s1 = min(s0 + int(TAP_WINDOW * fs), end)
        if s1 - s0 < int(0.1 * fs):
            continue
        seg = x[s0:s1]
        nfft = nfft_for(len(seg), fs)
        f = np.fft.rfftfreq(nfft, 1.0 / fs)
        P = psd(seg, fs, nfft)
        B = np.interp(f, B_f, B_P)
        for p in find_peaks(f, P, B, lo, hi, STRONG_DB, 2.0 / (len(seg) / fs)):
            tau = decay_tau(seg, fs, p["f"])
            if tau < TAP_MAX_TAU:
                # a line decaying with tau is 1/(2 pi tau) Hz wide at -3 dB;
                # the short window's own resolution would overstate it
                seen.append((p["f"], 1.0 / (2 * math.pi * tau), k))
    seen.sort()
    clusters = []
    for fr, hw, k in seen:
        if clusters and fr - clusters[-1][-1][0] < 2.0:
            clusters[-1].append((fr, hw, k))
        else:
            clusters.append([(fr, hw, k)])
    need = 2 if len(taps) >= 2 else 1
    lines = []
    for c in clusters:
        if len({k for _, _, k in c}) >= need:
            fc = float(np.median([fr for fr, _, _ in c]))
            hw = float(np.median([h for _, h, _ in c]))
            lines.append((fc, max(1.0, 1.5 * hw + 0.5)))
    return lines


def on_toolhead(freq, lines):
    return any(abs(freq - fc) <= g for fc, g in lines)


# ---------------------------------------------------------------- plucks

def pluck_freq(peaks, lines, flo, fhi):
    """Choose the belt's tone from a pluck's peaks. Returns (freq, info) or
    (None, reason)."""
    def near(fc, tol):
        best = None
        for p in peaks:
            if abs(p["f"] - fc) <= tol and (best is None or p["snr"] > best["snr"]):
                best = p
        return best

    fcs = [p["f"] for p in peaks if flo <= p["f"] <= fhi] + \
          [p["f"] / 2 for p in peaks if flo <= p["f"] / 2 <= fhi]
    cands = []
    for fc in fcs:
        tol = max(0.8, 0.01 * fc)
        one, two = near(fc, tol), near(2 * fc, 2 * tol)
        m1, m2 = on_toolhead(fc, lines), on_toolhead(2 * fc, lines)
        s1 = one["snr"] if one and not one.get("masked") else None
        s2 = two["snr"] if two and not two.get("masked") else None
        if s1 is not None and s2 is not None and max(s1, s2) >= STRONG_DB \
                and min(s1, s2) >= WEAK_DB:
            # 2f pins f twice as finely: weight it 4x in variance terms
            w1, w2 = 10 ** (s1 / 10), 4 * 10 ** (s2 / 10)
            est = (w1 * one["f"] + w2 * two["f"] / 2) / (w1 + w2)
            cands.append({"f": est, "kind": "f+2f", "score": s1 + s2,
                          "snr_f": s1, "snr_2f": s2})
        elif s1 is not None and s2 is None and m2 and s1 >= ALONE_DB:
            cands.append({"f": one["f"], "kind": "f (2f on toolhead line)",
                          "score": s1, "snr_f": s1, "snr_2f": None})
        elif s2 is not None and s1 is None and m1 and s2 >= ALONE_DB:
            cands.append({"f": two["f"] / 2, "kind": "2f (f on toolhead line)",
                          "score": s2, "snr_f": None, "snr_2f": s2})
    if not cands:
        if any(p.get("masked") for p in peaks if p["snr"] >= STRONG_DB):
            return None, "only toolhead lines"
        if not any(p["snr"] >= STRONG_DB for p in peaks):
            return None, "weak"
        return None, "no f/2f pair"
    paired = [c for c in cands if c["kind"] == "f+2f"]
    pool = paired or cands
    best = max(pool, key=lambda c: c["score"])
    # octave check: if a paired candidate sits at half the choice (its 2f is
    # our f line), the lower one is the tone
    for c in paired:
        if abs(c["f"] * 2 - best["f"]) <= max(0.8, 0.01 * best["f"]) * 2:
            best = c
            break
    return best["f"], best


def read_pluck(x, fs, o, s0, s1, B_f, B_P, lines, flo, fhi, nyq):
    seg = x[s0:s1]
    nfft = nfft_for(len(seg), fs)
    f = np.fft.rfftfreq(nfft, 1.0 / fs)
    P = psd(seg, fs, nfft)
    B = np.interp(f, B_f, B_P)
    T = len(seg) / fs
    peaks = find_peaks(f, P, B, 0.8 * flo, min(2.2 * fhi, nyq), WEAK_DB, 2.0 / T)
    # a peak inside a toolhead line's guard is the toolhead's only if it also
    # dies like one; a belt line there rings on and still counts
    for p in peaks:
        p["masked"] = on_toolhead(p["f"], lines) and decay_tau(seg, fs, p["f"]) < TAP_MAX_TAU
    fr, info = pluck_freq(peaks, lines, flo, fhi)
    if fr is None:
        return {"ok": False, "why": info}
    lines_used = [fr] if info["snr_f"] is not None else []
    if info["snr_2f"] is not None:
        lines_used.append(2 * fr)
    tau = max(decay_tau(seg, fs, fl) for fl in lines_used)
    # fresh: the line must start at this onset, not be the last pluck's tail
    m = int(FRESH_WIN * fs)
    fresh = None
    if o >= m:
        fresh = max(10 * math.log10(max(line_level(x[s0:s0 + m], fs, fl), 1e-30)
                                    / max(line_level(x[o - m:o], fs, fl), 1e-30))
                    for fl in lines_used)
    out = {"ok": True, "f": fr, "kind": info["kind"], "snr_f": info["snr_f"],
           "snr_2f": info["snr_2f"], "tau": None if tau == float("inf") else tau,
           "rise_db": fresh}
    if fresh is not None and fresh < FRESH_DB:
        out.update(ok=False, why="no fresh ringing at %.2f Hz (rise %.0f dB): "
                   "leftover from the last pluck, a knock or a fan" % (fr, fresh))
    elif tau < KNOCK_TAU:
        out.update(ok=False, why="knock (dies in %.0f ms)" % (tau * 1000))
    return out


# ---------------------------------------------------------------- stats

def summarise(plucks):
    good = [p["f"] for p in plucks if p["ok"]]
    if not good:
        return None
    med = float(np.median(good))
    agree = [v for v in good if abs(v - med) <= AGREE_HZ]
    for p in plucks:
        if p["ok"] and abs(p["f"] - med) > AGREE_HZ:
            p["outlier"] = True
    mean = float(np.mean(agree))
    n = len(agree)
    sd = float(np.std(agree, ddof=1)) if n > 1 else None
    u95 = None
    if n > 1:
        k = max(d for d in _T95 if d <= n - 1)
        u95 = _T95[k] * sd          # where the next pluck will likely land
    return {"n_good": len(good), "n_agree": n, "median": med, "mean": mean,
            "sd": sd, "pluck_u95": u95,
            "mean_u95": (u95 / math.sqrt(n)) if u95 is not None else None}


# ---------------------------------------------------------------- main

def analyse(run, names=(), band=(40.0, 200.0), target=None, quiet=4.0, gap=3.0,
            taps=True, thresh_db=12.0):
    t, x = load_accel(run)
    t, x, fs, gaps = uniform(t, x)
    nyq = 0.45 * fs
    flo, fhi = (0.55 * target, 1.5 * target) if target else band
    res = {"run": run, "fs": fs, "seconds": float(t[-1] - t[0]), "band": [flo, fhi],
           "gaps": gaps, "warnings": []}
    if 2 * fhi > nyq:
        res["warnings"].append("sample rate %.0f Hz can't hear 2f above %.0f Hz; "
                               "plucks above %.0f Hz rely on f alone" % (fs, nyq, nyq / 2))
    quiet_n = int(quiet * fs)
    B_f, B_P = background(x, fs, 0, quiet_n)
    onsets, quiet_hot = find_events(x, fs, 0.8 * flo, min(SEARCH_MAX, nyq), quiet_n, thresh_db)
    if quiet_hot:
        res["warnings"].append("something loud happened in the first %.1f s "
                               "(meant to be hands off): background may be off" % quiet)
    groups = group_events(onsets, fs, gap)
    if not groups:
        raise SystemExit("no taps or plucks found (pluck harder, or lower --thresh)")

    lines = []
    if taps:
        tg = groups.pop(0)
        lines = learn_toolhead(x, fs, tg, B_f, B_P, 25.0, min(SEARCH_MAX, nyq))
        res["taps"] = {"n": len(tg), "t": [float(t[o] - t[0]) for o in tg],
                       "lines": [{"f": fc, "guard": g} for fc, g in lines]}
        if len(tg) < 3:
            res["warnings"].append("only %d tap(s) in the first group: toolhead "
                                   "lines are less certain" % len(tg))
    all_on = [o for g in groups for o in g]
    res["groups"] = []
    for gi, g in enumerate(groups):
        name = names[gi] if gi < len(names) else "group %d" % (gi + 1)
        plucks = []
        for o in g:
            k = all_on.index(o)
            nxt = all_on[k + 1] if k + 1 < len(all_on) else len(x)
            s0 = o + int(SKIP * fs)
            s1 = min(s0 + int(WINDOW * fs), nxt - int(BLOCK * fs), len(x))
            p = {"t": float(t[o] - t[0])}
            if any(a <= t[s1 - 1] and b >= t[s0] for a, b in gaps):
                p.update(ok=False, why="data gap")
            elif (s1 - s0) / fs < MIN_WINDOW:
                p.update(ok=False, why="next event too soon")
            else:
                p.update(read_pluck(x, fs, o, s0, s1, B_f, B_P, lines, flo, fhi, nyq))
            plucks.append(p)
        res["groups"].append({"name": name, "plucks": plucks,
                              "summary": summarise(plucks)})
    return res


def fmt(v, f="%.2f"):
    return "-" if v is None else f % v


def report(res, span=None, mass=0.012, show_plucks=False):
    out = []
    out.append("%s: %.1f s at %.0f Hz, tone searched %.0f-%.0f Hz"
               % (res["run"], res["seconds"], res["fs"], res["band"][0], res["band"][1]))
    for w in res["warnings"]:
        out.append("warning: " + w)
    if res["gaps"]:
        out.append("warning: %d data gap(s) in the recording" % len(res["gaps"]))
    if "taps" in res:
        tl = res["taps"]["lines"]
        out.append("taps: %d, toolhead lines: %s" % (
            res["taps"]["n"],
            ", ".join("%.1f±%.1f" % (l["f"], l["guard"]) for l in tl) or "none"))
    out.append("")
    L = span / 1000.0 if span else None
    for g in res["groups"]:
        s = g["summary"]
        n = len(g["plucks"])
        if s is None:
            out.append("%-12s %2d events, no good plucks" % (g["name"], n))
        else:
            line = "%-12s %6.2f Hz  (%d of %d plucks agree; ±%s Hz per pluck, ±%s Hz mean)" % (
                g["name"], s["mean"], s["n_agree"], n, fmt(s["pluck_u95"]), fmt(s["mean_u95"]))
            if L:
                line += "  ~%.0f N" % (4 * mass * L * L * s["mean"] ** 2)
            out.append(line)
        if show_plucks:
            for p in g["plucks"]:
                if p["ok"]:
                    tag = " (outlier)" if p.get("outlier") else ""
                    out.append("    t=%6.2f  %7.2f Hz  %-24s f %s dB, 2f %s dB, decay %s ms%s" % (
                        p["t"], p["f"], p["kind"], fmt(p["snr_f"], "%.0f"),
                        fmt(p["snr_2f"], "%.0f"),
                        "long" if p["tau"] is None else "%.0f" % (p["tau"] * 1000), tag))
                else:
                    out.append("    t=%6.2f  rejected: %s" % (p["t"], p["why"]))
    gs = [g for g in res["groups"] if g["summary"]]
    pairs = [(gs[i], gs[i + 1]) for i in range(0, len(gs) - 1, 2)]
    if pairs:
        out.append("\npairs (same span length assumed):")
        for a, b in pairs:
            fa, fb = a["summary"]["mean"], b["summary"]["mean"]
            r = (fb / fa) ** 2
            out.append("  %s vs %s: %+.2f Hz, tension %.3f x (%+.1f%%)"
                       % (b["name"], a["name"], fb - fa, r, 100 * (r - 1)))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("run")
    ap.add_argument("--names", default="", help="comma-separated belt names, in pluck order")
    ap.add_argument("--band", default="40,200", help="Hz range for the tone (not 2f)")
    ap.add_argument("--target", type=float, help="expected tone, Hz: search 0.55-1.5x")
    ap.add_argument("--quiet", type=float, default=4.0, help="hands-off seconds at the start")
    ap.add_argument("--gap", type=float, default=3.0, help="pause (s) that separates groups")
    ap.add_argument("--no-taps", action="store_true", help="the recording has no tap group")
    ap.add_argument("--thresh", type=float, default=12.0, help="event threshold, dB over background")
    ap.add_argument("--span", type=float, help="free span length, mm")
    ap.add_argument("--mass", type=float, default=0.012, help="belt kg/m")
    ap.add_argument("--plucks", action="store_true", help="list every pluck")
    ap.add_argument("--json", help="write the full result to this file")
    a = ap.parse_args()
    names = [s.strip() for s in a.names.split(",") if s.strip()]
    band = tuple(float(v) for v in a.band.split(","))
    res = analyse(a.run, names, band, a.target, a.quiet, a.gap, not a.no_taps, a.thresh)
    print(report(res, a.span, a.mass, a.plucks))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
