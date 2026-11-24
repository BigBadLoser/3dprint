# K3 print profile (SuperSlicer), from measurements

Based on, as of 2026-11-24:
- **Vibration profile after the repairs** (Shake&Tune, 3000 mm/s², shaper on).
  - Peaks to avoid: **61 mm/s** (worst; motors), 103, 128, 140 mm/s.
  - Smooth zones: up to 57, 90–100, 107–125, **168–300 mm/s**.
- **Input shaper:** X MZV 131 Hz, Y EI 126 Hz. Y's limit is about 29k mm/s² before smoothing gets large.
- **TEST_SPEED_SAFE:**
  - 300 mm/s passes at every acceleration up to 25k.
  - 400 mm/s is marginal (one pass, one fail).
  - Confirmation run at 350 / 25k × 20 is pending.
- **Flow:** the extruder skipped above ~25 mm³/s at 0.6 A. The 0.8 A flow test is pending.
- **Pressure advance (ASA):** ≈0.045–0.05, measured with the squish test and confirmed by photo.
- **Line geometry:** 0.5 mm nozzle, 0.25 layers, 0.5 wide lines ≈ 0.11 mm². So 20 mm³/s ≈ 180 mm/s.

Constant-speed sections are what the vibration zones apply to. Pick each speed
inside a smooth zone, and keep visible surfaces away from 55–75 mm/s.

## Printer (Klipper/Kalico)

```
[printer]
max_velocity: 350        # 300 if the 350/25k x20 confirmation fails
max_accel: 25000
```
`pressure_advance: 0.05` for ASA, set per filament in the slicer's start
G-code or filament G-code; PPA is probably higher, about 0.08–0.10 (bulging
line ends) and still to be measured.

## Speeds (mm/s)

The repairs made 168–300 mm/s smooth, and that is where the flow limit
(20 mm³/s ≈ 180 mm/s at 0.25/0.5) lands, so everything that isn't a visible
surface runs at the flow limit.

| Feature | Speed | Why |
|---|---|---|
| External perimeters | **120** | 107–125 zone (or 50 for best finish) |
| Top solid infill | **120** | same zone |
| Perimeters | **175** | 168–300 zone, at the flow limit |
| Solid infill | **175** | same |
| Sparse infill | **175** | same |
| SuperSlicer auto-speed | volumetric 20, max auto-speed 180 | auto parts land at ~179 |
| Gap fill / small perimeters | 50 | below the 61 peak |
| Overhangs / bridges | 30–40 | |
| First layer | 30–50 | |
| Travel | 300 (350 after the confirmation run) | under the tested limit |
| Min print speed (cooling slowdown) | 40 | so slowed layers drop past 61 instead of sitting on it |

**Filament → max volumetric speed: 20 mm³/s** until the 0.8 A flow test,
then ~85% of the fastest flow that passed, and raise the 175s to match
(every speed up to 300 is in the smooth zone; avoid 128–140).

## Accelerations (mm/s²)

| Feature | Accel |
|---|---|
| Default | 10000 |
| External perimeters | 6000 |
| Top solid infill | 8000 |
| Perimeters | 10000 |
| Solid infill | 15000 |
| Sparse infill | 25000 |
| Bridges / overhangs | 5000 |
| First layer | 3000 |
| Travel | 25000 |

## To update when pending tests finish

- 0.8 A flow test: set max volumetric speed, and maybe raise sparse infill.
- 350/25k ×20: confirm `max_velocity`.
- PA for PPA (squish test).
- After belt tension matching: re-run the shaper and vibration profile; the
  zones can move.
