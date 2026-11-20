# Beacon squish-force experiment

Can the Beacon see the force of plastic being squeezed between the nozzle and the bed?
If yes, pressure advance and max-flow tuning may need no extra hardware.
See `../../docs/data-inventory.md` for why this matters.

## Expectations

The K3 is stiff (SLM aluminium, steel rail backers), which works *against* us here:
a few newtons of squeeze force may move things well under a micron. The goal of this
run is a number, not a feature. The number is the SNR from `analyze.py`.

## Procedure (on the printer's CM4)

Copy this repo to the Pi, then:

```bash
cd ~/3dprint
tools/kdata.py endpoints > ~/squish_endpoints.txt   # what this config exposes
```

Home, run Z offset / contact as you normally would, heat the bed, load filament.
The generator emits `M109` itself. Pick an empty spot of the plate with `--x/--y`.

```bash
G=experiments/squish/gen.py
$G noise  --temp 240 > /tmp/sq_noise.gcode
$G air    --temp 240 > /tmp/sq_air.gcode
$G static --temp 240 > /tmp/sq_static.gcode
$G dry    --temp 240 --temp-on-dry > /tmp/sq_dry.gcode
$G line   --temp 240 --y 170 > /tmp/sq_line.gcode   # different Y: fresh bed

for m in noise air static dry line; do
  tools/kdata.py record ~/squish/$m --preset squish --gcode-file /tmp/sq_$m.gcode
done
```

Clean the blob off the plate between `static` repeats.

The dry and line runs must use the **same** `--x/--y` for the subtraction to mean anything.
The example above uses a different Y for the line run, so either regenerate dry with
`--y 170` too, or skip `--dry` for a first look.

## Analysis

```bash
experiments/squish/analyze.py ~/squish/noise
experiments/squish/analyze.py ~/squish/air --plot      # control: SNR should be ~0
experiments/squish/analyze.py ~/squish/static --plot
experiments/squish/analyze.py ~/squish/line --plot --dry ~/squish/dry
```

`--plot` needs matplotlib in the environment you run from.

### Reading the result

- **air**: gain ≈ 0. If it isn't, the signal comes from something other than squish
  (vibration, extruder motor EMI near the coil, heat). That's important to know.
- **static**: the real test. SNR > 5 is promising, 1–5 is marginal (more cycles,
  bigger flow steps or a smaller gap may rescue it), < 1 means not visible.
- Lag tells us about the melt dynamics: tens of ms is plausible for pressure build-up.

Send back the run directories (they're small JSONL) and I'll go through them properly.

## Known limitations of this first pass

- `beacon/dump` gives filter-smoothed frequency (`filter_alpha` default 0.5).
  For sharper edges, set `filter_alpha: 1.0` under `[beacon]` for the session.
- The fit assumes the response is linear in flow with a pure delay. Real squeeze flow
  is neither: blob radius grows during `static`. Good enough for yes/no.
- `analyze.py` was tested against a simulated Klippy socket only, not real hardware.
