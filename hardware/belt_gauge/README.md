# Belt tension gauge (standalone)

A three-point gauge: two anvils 40 mm apart and a plunger between them that
sticks out 1.5 mm further. Pressed onto a belt until both anvils touch, the
belt is deflected exactly 1.5 mm and the plunger force gives the tension:

    T = F · span / (4 · depth)          (40 mm, 1.5 mm: T ≈ 6.7 × F)

Belt bending stiffness makes it read a little high, the same on every belt,
so matching the two belts of an axis is unaffected. Pushing harder than
needed goes into the anvils, not the load cell, so readings don't depend on
how hard you press once the anvils are down.

Status: firmware syntax-checked, not yet compiled or run on hardware; body is
a first draft (render it and check the fit before printing).

## Parts (all on hand)

| Part | Notes |
|---|---|
| ESP32 dev board (ESP32-WROOM DevKit) | USB power; its BOOT button is the gauge's button |
| 0.91" OLED, SSD1306, 128×32, I2C | address 0x3C (some are 0x3D) |
| HX711 mini breakout | load cell amplifier |
| Phidgets 3133_0 micro load cell, 5 kg | 55.25 × 12.7 × 12.7 mm, one M5 threaded hole near each end, 40 mm apart |
| M5 screws | 1 × M5×16 socket head (fixed end, down into the cell), 1 × M5×20 socket head + M5 nut (plunger, down through the cell into the plunger's nut slot). Don't go longer on the plunger screw: an M5×25 pokes out of the nose |
| USB power bank (optional) | for cordless use |

(`firmware/belt_gauge/` is the earlier version for the Waveshare
RP2040-LCD-1.28; its 1.27 mm header turned out to be impractical to wire.)

Expected plunger force at 30–50 N belt tension: 4.5–7.5 N (0.45–0.75 kg),
well inside the 5 kg cell's range.

## Wiring

| ESP32 dev board | OLED | HX711 |
|---|---|---|
| 3V3 | VCC | VCC |
| GND | GND | GND |
| GPIO21 | SDA | |
| GPIO22 | SCL | |
| GPIO16 | | DT (DOUT) |
| GPIO17 | | SCK |

Load cell to HX711: red E+, black E−, white A−, green A+. If the reading goes
negative when you press the plunger, swap white and green.

## Firmware

1. Arduino IDE → Boards Manager: install **esp32** by Espressif.
2. Library Manager: install **Adafruit SSD1306** (accept Adafruit GFX too).
3. Open `firmware/belt_gauge_esp32/belt_gauge_esp32.ino`, board
   **ESP32 Dev Module**, upload.

## Calibrate (once)

USB serial monitor at 115200, line ending *Newline*:

1. Gauge upside down on the table, nothing touching the plunger: `tare`
2. Rest a known weight on the plunger tip, e.g. 500 g (weigh it on a kitchen
   scale): `cal 500`
3. `geom 40 1.5` if you changed the span or depth in the SCAD file.
4. `show` prints the settings. They are kept in flash.

Better, the way BTT calibrates their Belter: clamp one end of a ~40 cm piece
of the same 9 mm belt, hang a known mass from the other (3 kg → 29.4 N,
5 kg → 49 N), measure it with the gauge, and set `scale K` with
K = true tension ÷ displayed tension. That folds the belt's bending
stiffness into the reading, so it shows real newtons.

## Use

- Power on with nothing touching the plunger (it tares at power-up).
- The square at the top right of the display fills in when the reading is steady.
- Printer: motors **off**, gantry pushed square against the stops.
- Press the gauge onto the middle of a free span until both anvils sit on the
  belt.
- **Short press BOOT**: store the reading as A. Measure the other belt, short
  press again: stores B and shows the difference; "OK" when within 5%.
- **Hold BOOT 2 s**: tare again.
- Measure the same span position on both belts of an axis (same gantry
  position), since span length doesn't change the reading here but belt
  routing near pulleys can.
