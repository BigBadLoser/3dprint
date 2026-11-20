# Data inventory: what the K3 can measure today

Goal: before building any coordinator or UI, find out which signals the printer
already exposes, how fast and how cleanly, and where the gaps are.

Every row marked **measured** must be confirmed on the K3 with `tools/kdata.py`.
Everything else here comes from reading the source code (beacon_klipper `beacon.py`
and Kalico `klippy/extras/*`) and still needs checking on the real machine.

## Hardware

| Part | Notes relevant to data |
|---|---|
| Beacon Rev H | Eddy coil (distance to bed), coil thermistor, accelerometer, contact detection in firmware |
| Chube Conduction hotend | Ceramic heater, run with Kalico MPC (gives a heater-power signal) |
| Mirrored Sherpa Micro | Extruder stepper; which Kraken slot/driver it is on matters (see TMC row) |
| BTT Kraken + CM4 | 4× TMC5160 (SPI, 48 V capable) + 4× TMC2209 (UART) |
| Kalico | Has `load_cell`, `load_cell_probe`, `ldc1612`, `tmc/stallguard_dump`, MPC |

## Signals available without new hardware

All of these are reachable on the Klippy API socket (`~/printer_data/comms/klippy.sock`).
Bulk streams are timestamped in **print_time**, so they share a single timebase and can be lined up exactly.

| Signal | Endpoint | Fields | Rate | Timebase | Useful for |
|---|---|---|---|---|---|
| Beacon coil | `beacon/dump` | dist, temp, pos, freq, time | firmware-defined, **measure** | print_time | squish-force test, bed/gantry deflection, thermal drift |
| Beacon coil (raw counts) | `BEACON_STREAM FILENAME=…` (G-code, writes CSV on the Pi) | time, data (raw), data_smooth, freq, dist, temp, pos | same | print_time | same, but unfiltered |
| Beacon accelerometer | `beacon/dump_accel` | time, x, y, z | **measure** | print_time | input shaper, extruder skip/click detection |
| Commanded toolhead motion | `motion_report/dump_trapq name=toolhead` | time, duration, start_v, accel, start_pos, direction | per move (exact) | print_time | ground-truth XY/Z motion |
| Commanded extruder motion | `motion_report/dump_trapq name=extruder` | same | per move (exact) | print_time | ground-truth commanded flow, **before** PA |
| Extruder steps | `motion_report/dump_stepper name=extruder` | interval, count, add | every step | MCU clock → print_time | actual E motion, **after** PA |
| Extruder StallGuard | `tmc/stallguard_dump name=extruder` | time, sg_result, cs_actual | ~1 kHz on 5160/SPI, slower on 2209/UART | print_time | motor load ≈ extrusion force (coarse) |
| Heater / MPC | `objects/subscribe` → `extruder` | temperature, power, and MPC fields (`loss_filament` etc.) | ~4 Hz status | host eventtime | flow-dependent melt power → max volumetric flow |

### Notes from reading the code

- **The `beacon/dump` frequency is smoothed.** It comes from `data_smooth`, an alpha-beta filter
  (`filter_alpha` 0.5 by default). That's fine as a first look. For the fastest transients, either use
  `BEACON_STREAM` to get raw `data`, or set `filter_alpha: 1.0` temporarily so the filter passes data through.
- **`dist` in `beacon/dump` has the bed-mesh Z compensation subtracted.** For deflection work we use `freq`
  (or raw `data`) and convert it ourselves, so the mesh doesn't leak into the signal.
- **The Beacon sample rate is set by the firmware.** `beacon.py` computes it from the MCU clock and
  doesn't hard-code it. The capture tool reports the measured rate.
- **The motion dumps solve the timing problem CNC Kitchen hit.** Their tuner had to guess when extrusion
  started and stopped. Here the exact commanded extruder velocity at every instant, in the same clock as
  the sensors, comes from `dump_trapq`/`dump_stepper`.
- **StallGuard is only meaningful in spreadCycle above some speed.** On the extruder at low speed it may be
  garbage. It's captured anyway because it costs nothing.
- **Kalico's MPC exposes heater power and its own estimate of filament heat loss.** Power drawn to melt
  filament scales with volumetric flow. That's low bandwidth, so it's no use for PA, but it's a candidate
  for finding the **melt limit** (max volumetric flow) with no extra hardware.

## Calibration targets vs. signals

| Target | Best signal we have today | Status |
|---|---|---|
| Bed mesh, Z offset, thermal Z | Beacon scan + contact | ✅ done (already set up) |
| Heater model | Kalico MPC | ✅ done |
| Input shaper | Beacon accelerometer | ✅ works, but results are "iffy", worth revisiting |
| Pressure advance | Beacon squish-force (**unproven**), StallGuard (coarse) | ❓ test needed |
| Max volumetric flow | MPC power, StallGuard, accel (skip clicks) | ❓ test needed |
| Flow ratio / extrusion multiplier | none | ❌ gap |
| First-layer inspection | none (Beacon can't see plastic) | ❌ gap |
| Clog / jam detection | StallGuard, MPC power (coarse) | ❓ |

## Gaps and how to fill them

1. **Melt pressure (for PA, max flow, clogs).** Run the Beacon squish-force test first
   (`experiments/squish/`). If the SNR isn't there, add a load cell or a flexure watched by an LDC1612
   in the extruder→nozzle force path. Kalico already has the drivers (`load_cell`: HX71x, ADS1220,
   ADS131M0x, CS1237; `ldc1612`).
2. **Plastic geometry (flow ratio, first layer).** A laser-line triangulation scanner
   (prior art: [rubedo](https://github.com/furrysalamander/rubedo)). Lessons from rubedo's code:
   - It syncs camera frames to position with fixed `sleep()` delays at constant speed. We should
     timestamp frames and map them through `dump_trapq` instead.
   - Its output is in pixels and frames, not mm. The Beacon can provide a height reference for
     calibrating ours.
   - Its results depend heavily on the build surface: matte black PEI worked best.
3. **Filament actually moving vs. commanded.** Nothing measures it today. Options are an encoder-style
   filament motion sensor or an angle sensor on the extruder. Kalico's `angle` module has a dump endpoint.

## First experiments

| # | Experiment | Answers |
|---|---|---|
| 0 | `kdata.py endpoints` on the K3 | Which endpoints actually exist with your config |
| 1 | Beacon noise floor: stationary at gap height, heaters on, no motion | Baseline noise in µm and sample rate |
| 2 | Squish test, *air* mode (nozzle high, extrude) | Control: should show nothing |
| 3 | Squish test, *static* mode (nozzle at gap, pulsed extrusion) | Does squeeze force show up in the Beacon signal? |
| 4 | Squish test, *line* + *dry* modes | Same with XY motion; dry run subtracts bed topography |
| 5 | StallGuard + MPC power during a flow ramp | Can we see the melt limit? |
