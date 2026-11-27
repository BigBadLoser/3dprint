# 3dprint

Self-tuning for a Klipper/Kalico printer (test bed: Annex K3, Beacon Rev H).

Current phase: finding out what data the printer can give us before building anything user-facing.

- `docs/data-inventory.md`: signals available today, calibration targets, gaps
- `tools/kdata.py`: raw capture from the Klippy API socket (stdlib only, runs on the Pi)
- `experiments/squish/`: can the Beacon see nozzle squeeze force? (generator, analysis, procedure)
- `tools/belt_tension.py`: belt tension from plucks heard by the Beacon accelerometer (f + 2f pairing, toolhead taps, knock rejection); `tools/belt_synth.py` makes fake recordings, `tests/` checks it on them
