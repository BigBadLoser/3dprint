#!/usr/bin/env python3
"""G-code for the moving pressure-advance squish test.

Each pass is one straight, heavily squished line along +X with constant
cross-section and a speed change in the middle:

    slow (v_slow) ──accel──> fast (v_fast) ──decel──> slow

Klipper only applies pressure advance to moves with XY motion, which is why
this has to move. With the right PA the deposited line stays uniform through
the speed changes, so the squeeze force the Beacon feels stays flat. Too
little PA: the line thins after accelerating and bulges after decelerating
(force dips, then spikes). Too much: the reverse.

Every pass uses the same moves; only PA differs (interleaved, repeated), so
motion artifacts are common to all passes and cancel when passes are
compared. Between passes the nozzle lifts, travels back and drops onto a new
row. A matching --dry run (same moves, no plastic) removes the bed shape.

    ./pa_gen.py --temp 250 --restore-pa 0.06 > ~/squish/pa.gcode
    ./pa_gen.py --dry --temp 250 --restore-pa 0.06 > ~/squish/pa_dry.gcode

Defaults: 0.2 mm gap, 1.0 mm line (2x a 0.5 mm nozzle), 20 → 100 mm/s, so
4 → 20 mm³/s. Rows from Y 35 upward, X 45 to 135.
"""
import argparse

FIL_AREA = 3.14159265 * (1.75 / 2) ** 2


def pa_order(values, repeats):
    """Interleave: each repeat walks the list in a different order so slow
    drift (heat, blob, bed) doesn't line up with PA."""
    out = []
    for r in range(repeats):
        if r % 2 == 0:
            out += values
        else:
            out += values[::-1]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--dry", action="store_true", help="same moves, no extrusion")
    ap.add_argument("--pa-list", default="0,0.02,0.04,0.06,0.08,0.10")
    ap.add_argument("--repeats", type=int, default=4)
    ap.add_argument("--warmup", type=int, default=1,
                    help="unmeasured passes first, at --warmup-pa, to prime")
    ap.add_argument("--warmup-pa", type=float, default=0.06)
    ap.add_argument("--dry-retract", type=float, default=1.0,
                    help="dry run: small retract before the passes")
    ap.add_argument("--dry-temp", type=int, default=170,
                    help="dry run nozzle temperature: below flow, so no ooze")
    ap.add_argument("--gap", type=float, default=0.20)
    ap.add_argument("--width", type=float, default=1.0, help="line width, mm")
    ap.add_argument("--v-slow", type=float, default=20.0, help="mm/s")
    ap.add_argument("--v-fast", type=float, default=100.0, help="mm/s")
    ap.add_argument("--accel", type=float, default=5000.0, help="mm/s²")
    ap.add_argument("--x0", type=float, default=45.0)
    ap.add_argument("--x1", type=float, default=135.0)
    ap.add_argument("--slow-len", type=float, default=20.0,
                    help="mm of slow line before and after the fast part")
    ap.add_argument("--y", type=float, default=35.0)
    ap.add_argument("--row", type=float, default=3.0, help="row spacing, mm")
    ap.add_argument("--hop", type=float, default=1.0)
    ap.add_argument("--settle", type=float, default=0.5,
                    help="pause at gap height before each pass, s")
    ap.add_argument("--temp", type=int, default=240)
    ap.add_argument("--park", default="",
                    help="macro to run before heating/cooling, e.g.\n"
                         "Go_To_Purge_Location, so ooze lands in the bucket")
    ap.add_argument("--wipe", default="",
                    help="macro to run after reaching temperature, e.g.\n"
                         "nozzle_clean")
    ap.add_argument("--restore-pa", type=float)
    ap.add_argument("--smooth-time", type=float, default=0.04,
                    help="pressure_advance_smooth_time for the test (s); match\n"
                         "what the slicer's start G-code sets for prints")
    a = ap.parse_args()

    pas = [float(v) for v in a.pa_list.split(",")]
    order = pa_order(pas, a.repeats)
    epm = a.width * a.gap / FIL_AREA          # mm filament per mm of line
    fs, ff = a.v_slow * 60, a.v_fast * 60
    xa, xb = a.x0 + a.slow_len, a.x1 - a.slow_len
    flow_fast = a.width * a.gap * a.v_fast
    order = [None] * a.warmup + order
    if a.y + a.row * (len(order) - 1) > 170:
        raise SystemExit("too many rows for the bed; reduce --repeats or --row")

    g = [
        "; pa squish test%s: %d passes, PA order %s" % (
            " (DRY)" if a.dry else "", len(order) - a.warmup,
            ",".join("%g" % p for p in order[a.warmup:])),
        "; line %.2f x %.2f mm, %g -> %g mm/s, %.1f -> %.1f mm3/s" % (
            a.width, a.gap, a.v_slow, a.v_fast,
            a.width * a.gap * a.v_slow, flow_fast),
        "G90",
        "G1 Z5 F600",
    ]
    if a.park:
        g.append(a.park)
    if a.dry:
        g.append("M106 S255 ; part fan speeds up the cool-down (not recorded)")
    g.append("M109 S%d" % (a.dry_temp if a.dry else a.temp))
    if a.dry:
        g.append("M107")
    if a.wipe:
        g.append(a.wipe)
    g += [
        "SET_VELOCITY_LIMIT ACCEL=%d" % a.accel,
        "SET_PRESSURE_ADVANCE SMOOTH_TIME=%.3f" % a.smooth_time,
        "G90",
        "M83",
        "G1 Z5 F600",
        "; warmup passes: %d" % a.warmup,
        "; --- record from here ---",
    ]
    if a.dry and a.dry_retract:
        g.append("G1 E-%.2f F1800 ; retract so the dry run doesn't ooze" % a.dry_retract)
    k = 0
    for i, pa in enumerate(order):
        y = a.y + i * a.row
        if pa is None:
            label, pa = "; warmup pass pa=%.4f" % a.warmup_pa, a.warmup_pa
        else:
            label = "; pass %d pa=%.4f" % (k, pa)
            k += 1
        g += [
            label,
            "SET_PRESSURE_ADVANCE ADVANCE=%.4f" % pa,
            "G1 X%.2f Y%.2f F9000" % (a.x0, y),
            "G1 Z%.3f F600" % a.gap,
            "G4 P%d" % int(a.settle * 1000),
        ]
        for x_to, f in ((xa, fs), (xb, ff), (a.x1, fs)):
            seg = x_to - (a.x0 if f == fs and x_to == xa else
                          xa if x_to == xb else xb)
            if a.dry:
                g.append("G1 X%.3f F%.0f" % (x_to, f))
            else:
                g.append("G1 X%.3f E%.5f F%.0f" % (x_to, seg * epm, f))
        g += ["G91", "G1 Z%.2f F600" % a.hop, "G90"]
    g += ["M400", "G91", "G1 Z10 F600", "G90", "M400"]
    if a.dry and a.dry_retract:
        g.append("G1 E%.2f F600 ; undo the dry-run retract" % a.dry_retract)
    if a.dry:
        g.append("M104 S%d ; back to print temperature" % a.temp)
    if a.restore_pa is not None:
        g.append("SET_PRESSURE_ADVANCE ADVANCE=%.4f" % a.restore_pa)
    else:
        g.append("M118 pa test: pressure advance left changed, restore it")
    print("\n".join(g))


if __name__ == "__main__":
    main()
