#!/usr/bin/env python3
"""Belt tension from a three-point gauge: two anvils a distance S apart rest
on the belt, a plunger between them pushes the belt a depth D past the
anvil line. For small deflection

    F = 4 * T * D / S        (F = plunger force, T = belt tension)

Belt bending stiffness is ignored, which slightly overstates T; for matching
the two belts of an AWD axis that error cancels.

Modes
  deflect  you push with a known force (kitchen/luggage scale) and measure D
           ./belt_gauge.py deflect --span 40 --force-g 300 --depth 1.6
  fixed    plunger fixed at depth D, force read by a load cell
           ./belt_gauge.py fixed --span 40 --depth 1.5 --force-g 450
  spring   spring-loaded plunger: prints a scale (retraction -> tension)
           ./belt_gauge.py spring --span 40 --protrusion 4 --k 0.4 --preload 0.5
           k in N/mm (measure: press the plunger on a scale at two
           compressions); preload in N at full protrusion.
  pluck    frequency method for comparison
           ./belt_gauge.py pluck --length 150 --freq 110 --mass 0.008
"""
import argparse

G = 9.80665


def tension(force_n, span_mm, depth_mm):
    return force_n * span_mm / (4.0 * depth_mm)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    d = sub.add_parser("deflect")
    d.add_argument("--span", type=float, required=True, help="anvil spacing, mm")
    d.add_argument("--force-g", type=float, required=True, help="push force, grams")
    d.add_argument("--depth", type=float, required=True, help="deflection, mm")
    f = sub.add_parser("fixed")
    f.add_argument("--span", type=float, required=True)
    f.add_argument("--depth", type=float, required=True)
    f.add_argument("--force-g", type=float, required=True)
    s = sub.add_parser("spring")
    s.add_argument("--span", type=float, required=True)
    s.add_argument("--protrusion", type=float, required=True,
                   help="plunger tip past the anvil line when unloaded, mm")
    s.add_argument("--k", type=float, required=True, help="spring rate, N/mm")
    s.add_argument("--preload", type=float, default=0.0,
                   help="spring force at full protrusion, N")
    s.add_argument("--step", type=float, default=0.25, help="scale step, mm")
    p = sub.add_parser("pluck")
    p.add_argument("--length", type=float, required=True, help="free span, mm")
    p.add_argument("--freq", type=float, required=True, help="Hz")
    p.add_argument("--mass", type=float, default=0.008,
                   help="belt mass per metre, kg/m (GT2 6 mm ~0.008)")
    a = ap.parse_args()

    if a.mode in ("deflect", "fixed"):
        T = tension(a.force_g / 1000.0 * G, a.span, a.depth)
        print("tension: %.1f N" % T)
        if a.depth / a.span > 0.08:
            print("note: deflection is large for this span; "
                  "small-angle formula is getting rough")
    elif a.mode == "spring":
        print("plunger pushed back (mm)  belt deflection (mm)  force (N)  tension (N)")
        c = 0.0
        while c < a.protrusion - 0.2 + 1e-9:
            depth = a.protrusion - c
            F = a.preload + a.k * c
            print("%10.2f %22.2f %13.2f %11.1f" % (c, depth, F, tension(F, a.span, depth)))
            c += a.step
        print("\nmark these on the body next to the plunger's top; same mark "
              "on both belts of an axis = matched tension")
    elif a.mode == "pluck":
        L = a.length / 1000.0
        print("tension: %.1f N" % (4 * a.mass * L * L * a.freq * a.freq))


if __name__ == "__main__":
    main()
