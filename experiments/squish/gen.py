#!/usr/bin/env python3
"""Generate G-code for the Beacon squish-force experiment.

Question: when molten plastic is squeezed between the nozzle and the bed, does
the reaction force deflect the bed/gantry enough for the Beacon to see it?
If yes, the Beacon signal should follow extrusion rate with a short lag.

Modes
  noise   nozzle parked at gap height, heaters on, no motion, no extrusion
  air     nozzle high above the bed, pulsed extrusion       (control: expect nothing)
  static  nozzle at gap height, no XY motion, pulsed extrusion
  line    nozzle at gap height, a straight line with alternating slow/fast segments
  dry     identical moves to 'line' with no extrusion (bed topography baseline)

Pressure advance is forced to --pa (default 0) so the commanded flow is a clean
step, and is set back to --restore-pa at the end if given.

Run it with tools/kdata.py:
  ./gen.py static --temp 240 > /tmp/squish_static.gcode
  ../../tools/kdata.py record ~/squish/static_1 --preset squish \
      --gcode-file /tmp/squish_static.gcode

Safety: the nozzle goes to --gap (default 0.30 mm) over the bed. Z offset must
be right (Beacon contact). Check --x/--y are a clean, empty spot of the plate.
Keep a hand near the e-stop on the first run.
"""
import argparse

FIL_AREA = 3.14159265 * (1.75 / 2) ** 2  # mm^2, 1.75 mm filament


def header(a, z):
    g = [
        "; squish experiment, mode=%s" % a.mode,
        "M109 S%d" % a.temp if a.mode != "dry" or a.temp_on_dry else "; no heat (dry)",
        "SET_PRESSURE_ADVANCE ADVANCE=%.4f" % a.pa,
        "G90",
        "M83",
        "G1 Z%.2f F600" % max(z, 5.0),
        "G1 X%.2f Y%.2f F6000" % (a.x, a.y),
        "G1 Z%.3f F300" % z,
        "M400",
        "G4 P%d ; quiet baseline" % int(a.settle * 1000),
    ]
    return g


def footer(a):
    g = ["M400", "G4 P%d ; quiet tail" % int(a.settle * 1000)]
    if a.retract and a.mode in ("air", "static", "line"):
        g.append("G1 E-%.2f F1800" % a.retract)
    g += ["G91", "G1 Z10 F600", "G90", "M400"]
    if a.restore_pa is not None:
        g.append("SET_PRESSURE_ADVANCE ADVANCE=%.4f" % a.restore_pa)
    else:
        g.append("M118 squish: pressure advance left at %.4f, restore it" % a.pa)
    return g


def pulses(a):
    """E-only pulse train: alternate low/high volumetric flow, then rest."""
    g = []
    for i in range(a.cycles):
        for flow in (a.flow_lo, a.flow_hi, a.flow_lo):
            e_rate = flow / FIL_AREA                # mm/s of filament
            e_len = e_rate * a.seg_time
            g.append("G1 E%.4f F%.1f ; flow %.1f mm3/s" % (e_len, e_rate * 60, flow))
        g.append("G4 P%d" % int(a.rest * 1000))
    return g


def line_moves(a, extrude):
    """Constant line cross-section; speed alternates so flow steps lo/hi."""
    g = []
    xs = a.x
    sign = 1
    area = a.line_width * a.gap               # mm^2 of deposited line
    for i in range(a.cycles):
        for flow in (a.flow_lo, a.flow_hi, a.flow_lo):
            v = flow / area                         # mm/s along X
            dx = v * a.seg_time
            xs += sign * dx
            if extrude:
                e = dx * area / FIL_AREA
                g.append("G1 X%.3f E%.5f F%.1f ; flow %.1f" % (xs, e, v * 60, flow))
            else:
                g.append("G1 X%.3f F%.1f ; dry, would be flow %.1f" % (xs, v * 60, flow))
        sign = -sign
        g.append("G1 Y%.3f F600" % (a.y + (i + 1) * a.line_spacing))
    return g


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("mode", choices=["noise", "air", "static", "line", "dry"])
    ap.add_argument("--x", type=float, default=90.0)
    ap.add_argument("--y", type=float, default=90.0)
    ap.add_argument("--gap", type=float, default=0.30, help="nozzle-bed gap, mm")
    ap.add_argument("--air-z", type=float, default=30.0)
    ap.add_argument("--temp", type=int, default=240)
    ap.add_argument("--temp-on-dry", action="store_true")
    ap.add_argument("--pa", type=float, default=0.0)
    ap.add_argument("--restore-pa", type=float)
    ap.add_argument("--flow-lo", type=float, default=2.0, help="mm3/s")
    ap.add_argument("--flow-hi", type=float, default=10.0, help="mm3/s")
    ap.add_argument("--seg-time", type=float, default=0.8, help="s per flow step")
    ap.add_argument("--rest", type=float, default=1.5, help="s between cycles")
    ap.add_argument("--cycles", type=int, default=4)
    ap.add_argument("--settle", type=float, default=2.0)
    ap.add_argument("--retract", type=float, default=0.0,
                    help="retract at end (default none, so runs start primed)")
    ap.add_argument("--line-width", type=float, default=0.6)
    ap.add_argument("--line-spacing", type=float, default=3.0)
    ap.add_argument("--noise-time", type=float, default=10.0)
    a = ap.parse_args()

    z = a.air_z if a.mode == "air" else a.gap
    g = header(a, z)
    if a.mode == "noise":
        g.append("G4 P%d" % int(a.noise_time * 1000))
    elif a.mode in ("air", "static"):
        g += pulses(a)
    elif a.mode == "line":
        g += line_moves(a, True)
    elif a.mode == "dry":
        g += line_moves(a, False)
    g += footer(a)
    print("\n".join(g))


if __name__ == "__main__":
    main()
