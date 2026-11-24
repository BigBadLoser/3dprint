# K3 print profile (SuperSlicer), from measurements

0.5 mm nozzle, 0.25 mm layers, 0.5 mm lines (≈0.112 mm² per line, so
speed = flow ÷ 0.112). ASA unless noted. As of 2026-11-24.

## What it's based on

| Measurement | Result | What it sets |
|---|---|---|
| Vibration profile after repairs (S&T, 3000 mm/s²) | Peaks 61 (worst), 103, 128, 140 mm/s. Smooth: ≤57, 90–100, 107–125, **168–300** | every constant speed sits in a smooth band |
| Input shaper | X MZV 131 Hz (~50k max), Y EI 126 Hz (~29k max) | accel ceiling 25k |
| TEST_SPEED_SAFE | 300 mm/s clean up to 25k; 400 marginal | max_velocity 350 (pending ×20 confirmation; else 300) |
| Extruder, free air at 0.8 A, 250 °C | > 60 mm³/s without skipping | flow is no longer the limit |
| Rotation distance | 96 mm out per 100 commanded | `rotation_distance × 0.96` |
| Pressure advance (squish + photo) | ≈0.045–0.05 | PA 0.05 |
| Motion limit at this line size | 300 mm/s × 0.112 mm² ≈ 33.5 mm³/s | the usable flow ceiling |

## Printer config

```
[extruder]
rotation_distance: <old value × 0.96>   # re-check: 100 commanded = 100 out
max_extrude_only_distance: 150

[printer]
max_velocity: 350        # 300 if the 350/25k ×20 confirmation fails
max_accel: 25000
```

## SuperSlicer: Print settings → Speed

### Speed for print moves (mm/s)

| Field | Value | Note |
|---|---|---|
| Default (for %-based) | 100 | unchanged |
| Perimeter internal | **0** (auto, ≈268) | |
| Perimeter external | **120** | 107–125 band, visible surface |
| Infill solid | **0** (auto, ≈268) | |
| Infill sparse | **0** (auto, ≈268) | |
| Top solid | **120** | visible surface |
| Support default | 120 | |
| Support interface | 80% (≈96) | 90–100 band |
| Brim & skirt | 50% | |
| Bridge external | **50** | below the 61 peak |
| Bridge internal | 95 | 90–100 band |
| Overhangs max | 100% | |
| Gap fill max | 95 | |
| Thin walls | 95 | |
| Ironing | 15 | |

### Non-print moves

| Field | Value |
|---|---|
| Travel xy | **300** (350 after the confirmation run) |
| Travel z | 0 (default) |

### Modifiers

| Field | Value | Note |
|---|---|---|
| First layer min / max / max infill | 0 / **50** / **50** | 75 sat in the 61–75 rough patch |
| Small perimeter speed | **45** (absolute) | 50% of 120 = 60, right on the worst peak |
| Small perimeter min / max length | 6 / 20 | unchanged |

### Auto speed

| Field | Value |
|---|---|
| Volumetric auto-speed | **30** mm³/s |
| Max auto-speed | **270** |

30 mm³/s gives ≈268 mm/s, inside the 168–300 band. Confirm with a test part
(clean walls, layer strength in the vise); then 33 is the most this line size
can use. If 30 looks worse than the slow profile: 25 mm³/s / max 225.

### Pressure equalizer

Leave at 0 (pressure advance handles transitions).

### Acceleration (mm/s²)

| Field | Value |
|---|---|
| Default | 10000 |
| Perimeter internal / external | 10000 / **6000** |
| Infill solid / sparse / top solid | 15000 / **25000** / **8000** |
| Support default / interface | 10000 / 0 |
| Bridges / internal bridges / overhangs | 5000 / 0 / 0 |
| Gap fill / thin walls | 0 / 0 (= default) |
| Travel | **25000** |
| Decelerate with target acceleration | on |
| First layer max | **3000** |

## SuperSlicer: Filament settings (ASA)

| Field | Value |
|---|---|
| Max volumetric speed | **30** mm³/s |
| Extrusion multiplier | 1.00 after the rotation-distance fix (undo any old compensation) |
| Start G-code | `SET_PRESSURE_ADVANCE ADVANCE=0.05` |
| Cooling: min print speed | 40 (slowed layers drop below the 61 peak) |

PPA: PA probably 0.08–0.10 (not measured), and its flow limit is likely
lower than ASA's; start at 20 mm³/s.

## SuperSlicer: Printer settings → Machine limits

Set to "use for time estimate only", with max feedrate 350 and max
acceleration 25000 per axis, so time estimates match. Klipper ignores M201
anyway; M204 per feature still applies.

## To update later

- 350/25k ×20 confirmation → max_velocity and travel 350.
- Test part at 30 mm³/s → keep, raise to 33, or drop to 25.
- PA for PPA (squish test).
- After belt matching with the gauge: re-run the shaper and vibration
  profile; the bands can move.
