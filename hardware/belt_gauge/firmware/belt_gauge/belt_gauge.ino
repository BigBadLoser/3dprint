// Belt tension gauge: Waveshare RP2040-LCD-1.28 + HX711 + Phidgets micro
// load cell, in a three-point body (two anvils SPAN apart, a plunger in the
// middle that sticks out DEPTH past them).
//
// Press the gauge onto the belt until both anvils touch: the belt is then
// deflected exactly DEPTH, and the plunger force F gives the tension
//     T = F * SPAN / (4 * DEPTH)
// (belt bending stiffness makes this read slightly high; it is the same on
// both belts, so matching two belts is unaffected).
//
// Board package: "Raspberry Pi Pico/RP2040" by Earle Philhower
//   (board: Waveshare RP2040 LCD 1.28, or generic Raspberry Pi Pico)
// Library: "GFX Library for Arduino" (Arduino_GFX) 1.6.x
//
// Wiring (board header -> HX711 mini breakout):
//   3V3 -> VCC     GND -> GND     GPIO26 -> DT (DOUT)     GPIO27 -> SCK
//   load cell: red E+, black E-, white A-, green A+ (swap A+/A- if the
//   reading goes negative when you press the plunger)
//
// Button: the BOOT button (works while the sketch runs)
//   short press  store the reading as A, next press as B; shows B vs A
//   hold 2 s     tare (zero) with nothing touching the plunger
//
// USB serial commands (115200, newline):
//   tare               zero
//   cal GRAMS          calibrate with a known weight resting on the plunger
//   geom SPAN DEPTH    anvil spacing and plunger depth in mm (default 40 1.5)
//   raw                toggle printing raw HX711 values
//   show               print the settings
// Settings are kept in flash.

#include <Arduino_GFX_Library.h>
#include <EEPROM.h>

// ---------------------------------------------------------------- pins
const int PIN_DT = 26, PIN_SCK = 27;
const int LCD_DC = 8, LCD_CS = 9, LCD_CLK = 10, LCD_MOSI = 11, LCD_RST = 12,
          LCD_BL = 25;
const int PIN_BAT = 29;  // BAT_ADC, battery voltage / 3

Arduino_DataBus *bus = new Arduino_RPiPicoSPI(LCD_DC, LCD_CS, LCD_CLK, LCD_MOSI,
                                              GFX_NOT_DEFINED, spi1);
Arduino_GFX *panel = new Arduino_GC9A01(bus, LCD_RST, 0, true);
Arduino_Canvas *gfx = new Arduino_Canvas(240, 240, panel);

// RGB565 colours (own names, so this doesn't depend on the library's)
const uint16_t C_BLACK = 0x0000;
const uint16_t C_WHITE = 0xFFFF;
const uint16_t C_RED = 0xF800;
const uint16_t C_GREEN = 0x07E0;
const uint16_t C_YELLOW = 0xFFE0;
const uint16_t C_ORANGE = 0xFD20;
const uint16_t C_LIGHTGREY = 0xC618;
const uint16_t C_DARKGREY = 0x7BEF;

// ---------------------------------------------------------------- settings
struct Settings {
  uint32_t magic;
  float countsPerGram;
  float span, depth;  // mm
  int32_t offset;
};
const uint32_t MAGIC = 0xBE17'0002;
Settings cfg = {MAGIC, 400.0f, 40.0f, 1.5f, 0};

void saveCfg() {
  EEPROM.put(0, cfg);
  EEPROM.commit();
}

// ---------------------------------------------------------------- HX711
bool hxReady() { return digitalRead(PIN_DT) == LOW; }

int32_t hxRead() {  // channel A, gain 128
  uint32_t v = 0;
  noInterrupts();
  for (int i = 0; i < 24; i++) {
    digitalWrite(PIN_SCK, HIGH);
    delayMicroseconds(1);
    v = (v << 1) | (digitalRead(PIN_DT) ? 1 : 0);
    digitalWrite(PIN_SCK, LOW);
    delayMicroseconds(1);
  }
  digitalWrite(PIN_SCK, HIGH);  // 25th pulse: gain 128 next time
  delayMicroseconds(1);
  digitalWrite(PIN_SCK, LOW);
  interrupts();
  if (v & 0x800000) v |= 0xFF000000;  // sign-extend
  return (int32_t)v;
}

int32_t hxAverage(int n) {
  int64_t s = 0;
  for (int i = 0; i < n; i++) {
    uint32_t t0 = millis();
    while (!hxReady()) {
      if (millis() - t0 > 500) return 0;
      delay(1);
    }
    s += hxRead();
  }
  return (int32_t)(s / n);
}

// ---------------------------------------------------------------- state
const int NAVG = 8;  // ~0.8 s at the HX711's 10 samples/s
int32_t hist[NAVG];
int nhist = 0, ihist = 0;
bool printRaw = false;
float storedA = NAN, storedB = NAN;
static bool isNaN(float x) { return x != x; }
bool nextIsA = true;

float gramsFrom(int32_t raw) { return (raw - cfg.offset) / cfg.countsPerGram; }
float tensionN(float grams) {
  return grams / 1000.0f * 9.80665f * cfg.span / (4.0f * cfg.depth);
}

void tare() {
  cfg.offset = hxAverage(16);
  saveCfg();
  nhist = 0;
  storedA = storedB = NAN;
  nextIsA = true;
  Serial.printf("tared, offset %ld\n", (long)cfg.offset);
}

void calibrate(float grams) {
  int32_t raw = hxAverage(20);
  if (grams <= 0 || raw == cfg.offset) {
    Serial.println("cal: bad weight or no change from tare");
    return;
  }
  cfg.countsPerGram = (raw - cfg.offset) / grams;
  saveCfg();
  Serial.printf("calibrated: %.3f counts per gram\n", cfg.countsPerGram);
}

void showCfg() {
  Serial.printf("span %.2f mm, depth %.3f mm, %.3f counts/g, offset %ld\n",
                cfg.span, cfg.depth, cfg.countsPerGram, (long)cfg.offset);
  Serial.printf("tension = grams * %.4f N per gram\n", tensionN(1.0f));
}

void handleSerial() {
  static String line;
  while (Serial.available()) {
    char c = Serial.read();
    if (c != '\n' && c != '\r') {
      line += c;
      continue;
    }
    line.trim();
    if (line == "tare") tare();
    else if (line.startsWith("cal ")) calibrate(line.substring(4).toFloat());
    else if (line.startsWith("geom ")) {
      String rest = line.substring(5);
      int sp = rest.indexOf(' ');
      float s = rest.substring(0, sp).toFloat(), d = rest.substring(sp + 1).toFloat();
      if (s > 5 && d > 0.1) {
        cfg.span = s;
        cfg.depth = d;
        saveCfg();
      }
      showCfg();
    } else if (line == "raw") printRaw = !printRaw;
    else if (line == "show") showCfg();
    else if (line.length()) Serial.println("commands: tare | cal GRAMS | geom SPAN DEPTH | raw | show");
    line = "";
  }
}

// ---------------------------------------------------------------- button
// BOOTSEL is read through the flash chip-select, so poll it sparingly.
void handleButton() {
  static uint32_t down = 0, last = 0;
  static bool wasDown = false, longDone = false;
  if (millis() - last < 40) return;
  last = millis();
  bool isDown = BOOTSEL;
  if (isDown && !wasDown) {
    down = millis();
    longDone = false;
  }
  if (isDown && !longDone && millis() - down > 2000) {
    longDone = true;
    tare();
  }
  if (!isDown && wasDown && !longDone && nhist) {
    int64_t s = 0;
    for (int i = 0; i < nhist; i++) s += hist[i];
    float t = tensionN(gramsFrom((int32_t)(s / nhist)));
    if (nextIsA) {
      storedA = t;
      storedB = NAN;
    } else {
      storedB = t;
    }
    nextIsA = !nextIsA;
  }
  wasDown = isDown;
}

// ---------------------------------------------------------------- display
void centered(const char *s, int y, int size, uint16_t col) {
  gfx->setTextSize(size);
  gfx->setTextColor(col);
  int w = strlen(s) * 6 * size;
  gfx->setCursor(120 - w / 2, y);
  gfx->print(s);
}

void draw(float t, bool stable, bool valid) {
  char buf[32];
  gfx->fillScreen(C_BLACK);
  if (!valid) {
    centered("no HX711", 105, 3, C_RED);
    gfx->flush();
    return;
  }
  snprintf(buf, sizeof buf, "%.1f", t);
  centered(buf, 70, 6, stable ? C_GREEN : C_YELLOW);
  centered("N belt tension", 130, 2, C_LIGHTGREY);
  if (!isNaN(storedA)) {
    snprintf(buf, sizeof buf, "A %.1f", storedA);
    centered(buf, 160, 2, C_WHITE);
  }
  if (!isNaN(storedB)) {
    float d = 100.0f * (storedB - storedA) / storedA;
    snprintf(buf, sizeof buf, "B %.1f  %+.0f%%", storedB, d);
    centered(buf, 182, 2, fabsf(d) <= 5 ? C_GREEN : C_ORANGE);
  } else if (!isNaN(storedA)) {
    centered("press: store B", 182, 2, C_DARKGREY);
  } else {
    centered("press: store A", 160, 2, C_DARKGREY);
  }
  float vbat = analogRead(PIN_BAT) * 3.3f / 4095.0f * 3.0f;
  if (vbat > 2.5f) {
    snprintf(buf, sizeof buf, "%.2fV", vbat);
    centered(buf, 30, 2, vbat < 3.5f ? C_RED : C_DARKGREY);
  }
  gfx->flush();
}

// ---------------------------------------------------------------- main
void setup() {
  Serial.begin(115200);
  pinMode(PIN_SCK, OUTPUT);
  digitalWrite(PIN_SCK, LOW);
  pinMode(PIN_DT, INPUT);
  pinMode(LCD_BL, OUTPUT);
  digitalWrite(LCD_BL, HIGH);
  analogReadResolution(12);

  EEPROM.begin(256);
  Settings s;
  EEPROM.get(0, s);
  if (s.magic == MAGIC && s.countsPerGram != 0 && s.span > 0 && s.depth > 0) cfg = s;

  gfx->begin();
  gfx->fillScreen(C_BLACK);
  centered("taring...", 105, 3, C_WHITE);
  gfx->flush();
  delay(500);
  if (hxAverage(4) != 0) tare();  // nothing touching the plunger at power-up
  showCfg();
}

void loop() {
  handleSerial();
  handleButton();

  static uint32_t lastSample = 0;
  if (hxReady()) {
    int32_t r = hxRead();
    lastSample = millis();
    hist[ihist] = r;
    ihist = (ihist + 1) % NAVG;
    if (nhist < NAVG) nhist++;
    if (printRaw) Serial.println(r);
  }
  bool valid = millis() - lastSample < 1000;

  static uint32_t lastDraw = 0;
  if (millis() - lastDraw > 150) {
    lastDraw = millis();
    int64_t s = 0;
    int32_t lo = INT32_MAX, hi = INT32_MIN;
    for (int i = 0; i < nhist; i++) {
      s += hist[i];
      lo = min(lo, hist[i]);
      hi = max(hi, hist[i]);
    }
    float t = nhist ? tensionN(gramsFrom((int32_t)(s / nhist))) : 0;
    // stable when the last ~0.8 s varies by less than 2% (or 0.3 N)
    float span = nhist ? fabsf(tensionN((hi - lo) / cfg.countsPerGram)) : 1e9;
    bool stable = nhist == NAVG && span < max(0.3f, 0.02f * fabsf(t));
    draw(t, stable, valid);
  }
}
