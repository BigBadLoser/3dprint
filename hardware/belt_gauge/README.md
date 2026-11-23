# Belt tension gauge (standalone)

A three-point gauge: two anvils 40 mm apart and a plunger between them that
sticks out 1.5 mm further. Pressed onto a belt until both anvils touch, the
belt is deflected exactly 1.5 mm and the plunger force gives the tension:

    T = F · span / (4 · depth)          (40 mm, 1.5 mm: T ≈ 6.7 × F)

Belt bending stiffness makes it read a little high, the same on every belt,
so matching the two belts of an axis is unaffected. Pushing harder than
needed goes into the anvils, not the load cell, so readings don't depend on
how hard you press once the anvils are down.

Status: firmware syntax-checked, not yet run on hardware; body is a first
draft (render it and check the fit before printing).

## Parts (all on hand)

| Part | Notes |
|---|---|
| Waveshare RP2040-LCD-1.28 | display, USB-C, LiPo connector + charger |
| HX711 mini breakout | load cell amplifier |
| Phidgets 3133_0 micro load cell, 5 kg | 55.25 × 12.7 × 12.7 mm, one M5 threaded hole near each end, 40 mm apart |
| M5 screws | 1 × M5×16 socket head (fixed end, down into the cell), 1 × M5×20 socket head + M5 nut (plunger, down through the cell into the plunger's nut slot). Don't go longer on the plunger screw: an M5×25 pokes out of the nose |
| small LiPo (optional) | for cordless use; USB power works too |

Expected plunger force at 30–50 N belt tension: 4.5–7.5 N (0.45–0.75 kg),
well inside the 5 kg cell's range.

## Wiring

| RP2040-LCD-1.28 header | HX711 |
|---|---|
| 3V3 | VCC |
| GND | GND |
| GPIO26 | DT (DOUT) |
| GPIO27 | SCK |

Load cell to HX711: red E+, black E−, white A−, green A+. If the reading goes
negative when you press the plunger, swap white and green.

## Firmware

1. Arduino IDE → Boards Manager: add
   `https://github.com/earlephilhower/arduino-pico/releases/download/global/package_rp2040_index.json`
   to *Additional boards manager URLs*, install **Raspberry Pi Pico/RP2040**.
2. Library Manager: install **GFX Library for Arduino** (Arduino_GFX, 1.6.x).
3. Open `firmware/belt_gauge/belt_gauge.ino`, board **Waveshare RP2040 LCD 1.28**
   (or *Raspberry Pi Pico*), upload (hold BOOT while plugging in the first time).

## Calibrate (once)

USB serial monitor at 115200, line ending *Newline*:

1. Gauge upside down on the table, nothing touching the plunger: `tare`
2. Rest a known weight on the plunger tip, e.g. 500 g (weigh it on a kitchen
   scale): `cal 500`
3. `geom 40 1.5` if you changed the span or depth in the SCAD file.
4. `show` prints the settings. They are kept in flash.

Optional true check: hang a known mass from a piece of the same belt
(e.g. 3 kg → 29.4 N), measure it with the gauge; the reading should be close.
If it is consistently off, that's the bending-stiffness offset.

## Use

- Power on with nothing touching the plunger (it tares at power-up).
- Printer: motors **off**, gantry pushed square against the stops.
- Press the gauge onto the middle of a free span until both anvils sit on the
  belt. The number turns **green** when the reading is steady.
- **Short press BOOT**: store the reading as A. Measure the other belt, short
  press again: stores B and shows the difference; green when within 5%.
- **Hold BOOT 2 s**: tare again.
- Measure the same span position on both belts of an axis (same gantry
  position), since span length doesn't change the reading here but belt
  routing near pulleys can.
