# 3dprint

Self-tuning for a Klipper/Kalico printer (test bed: Annex K3, Beacon Rev H).

Current phase: finding out what data the printer can give us before building anything user-facing.

- `docs/data-inventory.md`: signals available today, calibration targets, gaps
- `tools/kdata.py`: raw capture from the Klippy API socket (stdlib only, runs on the Pi)
- `experiments/squish/`: can the Beacon see nozzle squeeze force? (generator, analysis, procedure)
