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
| Mirrored Sherpa Micro | Extruder motor LDO-36STH20-1004AHG(XH): 1.0 A rated, 0.095 Nm; was run at 0.6 A (skips past ~25 mm³/s), raised to 0.8 A |
| BTT Kraken + CM4 | Socketed (plug-in) stepper drivers; currently all TMC2209 (UART). 48 V-capable drivers on hand (type TBD) |
| Kalico | Has `load_cell`, `load_cell_probe`, `ldc1612`, `tmc/stallguard_dump`, MPC |

## What the K3's config tells us (checked 2026-11-20)

From `kdata.py endpoints` and printer.cfg:

- **Kalico v0.12.0-715.** It's too old to have `tmc/stallguard_dump`; current Kalico has it.
- **All drivers are TMC2209 in spreadCycle** (`stealthchop_threshold: 0`). The 2209's StallGuard
  (`SG_RESULT`) only works in StealthChop, so **extruder StallGuard is effectively unavailable**
  even after a Kalico update. It would need a 5160 slot, or StealthChop on the extruder (not recommended).
- **MPC is active** on the extruder (`control = mpc`, `heater_power: 100`). The filament constants are set
  for ASA (density 1.07, heat capacity 1.7), so `loss_filament` is only right for ASA-like materials.
- **The Beacon sits 23 mm left of the nozzle** (`x_offset: -23`). It measures the bed 23 mm away from where
  the nozzle presses, so in the squish test it sees gantry lift plus bed tilt, not the spot under the nozzle.
- **The bed is held on springs** (the position_max comment). That's compliance in the force path, which
  should *help* the squish test compared with a fully rigid bed.
- **The Z gap at print temperature depends on the macro chain.** Contact homing runs at 150 °C, then
  `_BEACON_SET_NOZZLE_TEMP_OFFSET` corrects for nozzle growth, then `print_start` adds a further −0.03 mm.
  Experiments that rely on a small gap should run after `print_start`.
- Pressure advance in config: 0.06. Nozzle: 0.5 mm. Bed: 180 × 180 mm, mesh area X 20–150, Y 25–155.

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
| Pressure advance | Beacon squish-force while moving (signal **proven**, PA method in progress); StallGuard unusable (2209 in spreadCycle) | 🟡 in progress |
| Max volumetric flow | Beacon squish-force, MPC power, accel (skip clicks) | ❓ test needed |
| Flow ratio / extrusion multiplier | none | ❌ gap |
| First-layer inspection | none (Beacon can't see plastic) | ❌ gap |
| Clog / jam detection | StallGuard, MPC power (coarse) | ❓ |

## Motion system (2026-11-20)

| Finding | Detail |
|---|---|
| Beacon accelerometer orientation | Mounted rotated ~90°: `accel_axes_map: y, -x, z` (was default x,y,z, so earlier shaper results had X and Y swapped) |
| Resonances before repair (Shake&Tune v6) | X: double peak ~121 + 132 Hz (X motion also excites Y around 120 Hz); Y: single ~120 Hz. Old config had X 77.6 / Y 83.2 Hz |
| Resonances after repair (2026-11-22) | X: single clean peak 132 Hz (ζ 0.025), low-frequency junk and broadband bands gone. Y: dominant 132 Hz (ζ 0.026) plus a small pair at 80.5 / 87 Hz (~25% of main) that survived belt matching; finger test at 85 Hz did not single out the one ASA bracket |
| Shaper chosen (after repair) | X MZV 131 Hz (ζ 0.025, ~50k mm/s² max), Y EI 126 Hz (ζ 0.026, ~29k mm/s² max) |
| Vibration profile after repair (2026-11-22, 3000 mm/s², shaper on) | Peaks at 61 mm/s (largest, mostly X; full-step rate ~300 Hz meets the motor resonance S&T fits at 275 Hz, ζ 0.116), 103, 128 (mostly Y) and 140 mm/s. Smooth zones: up to 57, 90–100, 107–125 and **168–300 mm/s** (before repair only 107–129, 165–182, 266–284; the ~195 mm/s peak is gone). Symmetry 44%. Header reports TMC Autotune on X/Y (toff 1, tbl 1, CoolStep semin 2). The "Y motor diff" line was not a real mismatch: config has 1.1 A on both Y motors (1.088 A is the 2209's nearest current step); X/X1 1.2 A |
| Y homing grinds (2026-11-23) | TEST_SPEED_SAFE showed Y/Y1 mcu positions moving by exact multiples of 128 microsteps (15 and 4 electrical cycles) between homes, X/X1 0–1: Y motors slip against the frame before StallGuard triggers (sg4_thrs 120, less sensitive than the original 132). Retuned with X centred (pre-Y hook moves X to 86): 138 hits the frame, 142 quiet, 146 stops short. **Set to 140** (biased toward the grinding side: a slip at the frame is harmless, an early trigger crashes). X (homing_speed 50): 1.2 A 140 hits / 144 early; home_current 0.8 A shifted the window to 144–148 without widening it and was unreliable in practice; homing_speed 30 never triggered (ground into the frame at sgthrs 120 and 160). X back to 1.2 A, sgthrs 142; window only ~3 wide, physical X endstop is the fallback. X also triggered early partway along when homed again from X20 straight after being homed (motors energised); fine after a 3 s wait or after switching the X motors off first, so a short `home_gcode_pre_x` pause is back. `sg4_thrs` in `[autotune_tmc]` overrides `driver_SGTHRS` (autotune writes it at klippy:ready); driver_SGTHRS lines removed. `homing_retract_dist: 20` restored on X/Y (in Kalico it is the back-off and `min_home_dist`, no second home) |
| Intermittent X noise | Low-frequency (10–45 Hz) energy, a 61 Hz peak and broadband bands appeared in 2 of 3 X runs with nothing changed; cause unknown |
| Driver noise | X/Y 2209s in spreadCycle add a lot of standstill vibration (MEASURE_AXES_NOISE ~9 all off → ~130–180 motors on); position-dependent whine on an X motor; autotune changed whine to hiss but did not lower the numbers |
| Driver heat | stepper_y1 tripped OTPW at 1.2 A during CREATE_VIBRATIONS_PROFILE; bay fans at 40% |
| Beacon accel bug | ZeroDivisionError in beacon.py `_process_samples` when a batch has one sample; patched locally (`if count > 1 else 0.0`), reported upstream |

## Mechanical state found (2026-11-20) and repairs (2026-11-22)

Found while chasing noisy accelerometer data:
- **Beacon mount loose (2–3 mm play).** The Beacon hangs from the bottom
  duct, clamped to both 3628 fans with through-bolts. Bolts had lost
  preload. **Fixed:** re-tightened with Loctite 243 (over-tightening made one
  fan rub; loosened and re-snugged). Axes map afterwards: mount rotation
  0.9° (was ~8° off), angle errors X 5.4° / Y 3.9° (was 13.2° / 4.6°).
- **3 lower + 1 upper XY motor brackets cracked** (dual-shear mounts, S80
  motors with ~80 mm shafts; each shaft carries its axis's drive pulley near
  the top bearing and the other axis's idler at the bottom). **Fixed:**
  replaced, 3 in PPA (printed slightly wet: heavy stringing, layer shifts,
  sanded to fit, but much stronger than ABS in a vise test) and 1 in ASA.
  Belts retensioned by feel and matched side to side. Plan to reprint the
  PPA set from dried filament on the repaired machine.
- **Gantry racked** after an earlier crash caused by unreliable sensorless
  homing (autotune on XY changed StallGuard behaviour). Autotune was kept
  on X/Y with `sg4_thrs` set to the homing thresholds (X 140, Y 120), plus
  two-stage homing. 2026-11-22: CoolStep disabled (`semin: 0`) on X/Y and
  the extruder, extruder run_current 0.6 → 0.8 A, Beacon `home_gcode_pre/post`
  hooks removed (Kalico clears the stall flag and handles the back-off with
  `homing_retract_dist` / `min_home_dist`).
- Standstill accelerometer noise with motors on is unchanged by the repairs
  (~136 / 158 / 88): it comes from the X/Y motors and 2209 drivers in
  spreadCycle, not the mechanics.

## Measured on the K3 (2026-11-20)

| Finding | Numbers |
|---|---|
| Beacon stream | 1000 Hz, no dropouts, every interval exactly 1 ms |
| Beacon sensitivity at ~0.3 mm | ~48–52 Hz per µm |
| Noise, per sample | 2.9 µm RMS, dominated by tones |
| Noise, 0.2 s averages | 0.32 µm |
| Tones | 10 Hz + harmonics (10/20/90/100/110 Hz), up to ~5500× the floor. **Source: the chamber heater** (`heater_generic chamber_heater`, PWM on a 0.1 s cycle). Tested one heater at a time with the Beacon 0.5 mm above the bed: chamber 10 Hz at 5538×; nozzle and bed show no comb. (An earlier "heaters off" test was taken high above the bed, where the Beacon is far less sensitive, so it proved nothing.) Hot nozzle alone adds broad low-frequency noise (3–12 Hz, ~400×). Part-cooling fan shows up at ~240 Hz. |
| Coil temperature at 0.3 mm gap, 250 °C nozzle, hot bed | 82–89 °C, rising ~0.1 °C/s; drift ~0.4 µm/s |
| Squeeze force, static blob at 0.3 mm | **clear** (t = 8.2): ~0.5 µm per mm³/s, pushed apart, ~130 ms lag, grows with blob size |
| Air extrusion (control) | flat (t = 1.0): extrusion, motor and MPC duty changes do not move the Beacon |
| Line at 0.3 mm gap, 0.6 mm wide, speed-coupled flow | not seen |
| Pressure advance | Tested while moving (`pa_gen.py`; Klipper/Kalico only apply PA to moves with XY motion). Settings: 0.25 gap, 0.75 wide, 20→70 mm/s, each PA ×4, ooze-free dry, chamber heater off, bed 110. Indicators: force **A_down** (blip after the slow-down; `pa_analyze.py`) and **photo line width** after the speed-up vs steady (`photo_width.py`). **pa_6** (0.04–0.14): A_down 0.047, photo ≈0.047. **pa_7** (0.03–0.07): A_down 0.053, photo 0.036 / 0.042 (two photos of the same lines). **Best estimate ≈0.045–0.05** (config 0.06). pa_5 (0–0.10) gave A_down 0.011 with no usable photo: outlier, ran right after a disk-full crash. A_up and the combined M are not reliable (A_up stays negative at all PA). The photo method only changes width 2–5% across 0.04–0.07, so it confirms the ballpark; A_down resolves finer. ASA lifts off the bed as it cools: intensity-based photo measurement fails, edge-based width works. |
| Extruder limits | 0.2 mm gap × 1.0 mm line skipped the Sherpa Micro (run_current 0.6 A) even at 16 mm³/s; skipping chewed the filament and caused silent slip later |
| Disk | a recording of all streams through a long heat/cool wait filled the CM4; recorder now skips heavy streams and the prep phase |

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
