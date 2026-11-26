// Belt tension gauge: ESP32 dev board + 0.91" SSD1306 OLED (128x32, I2C)
// + HX711 + Phidgets micro load cell, in the three-point body
// (two anvils SPAN apart, a plunger in the middle DEPTH past them).
//
// Press the gauge onto the belt until both anvils touch: the belt is then
// deflected exactly DEPTH, and the plunger force F gives the tension
//     T = F * SPAN / (4 * DEPTH)
//
// Board package: "esp32" by Espressif (board: ESP32 Dev Module)
// Libraries: "Adafruit SSD1306" (pulls in "Adafruit GFX Library")
//
// Wiring (ESP32 dev board pins):
//   OLED:  VCC -> 3V3   GND -> GND   SDA -> GPIO21   SCL -> GPIO22
//   HX711: VCC -> 3V3   GND -> GND   DT  -> GPIO16   SCK -> GPIO17
//   load cell: red E+, black E-, white A-, green A+ (swap A+/A- if the
//   reading goes negative when you press the plunger)
//
// Button: the board's BOOT button (GPIO0). Don't hold it while plugging in
// or pressing EN/reset, or the board starts in download mode.
//   short press  store the reading as A, next press as B; shows B vs A
//   hold 2 s     tare (zero) with nothing touching the plunger
//
// USB serial commands (115200, newline):
//   tare               zero
//   cal GRAMS          calibrate with a known weight resting on the plunger
//   geom SPAN DEPTH    anvil spacing and plunger depth in mm (default 40 1.5)
//   scale K            multiply tension by K (from a hanging-weight belt test)
//   raw                toggle printing raw HX711 values
//   show               print the settings
// Settings are kept in flash (Preferences).

#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Preferences.h>

// ---------------------------------------------------------------- pins
const int PIN_DT = 16, PIN_SCK = 17, PIN_BTN = 0;
const int PIN_SDA = 21, PIN_SCL = 22;

Adafruit_SSD1306 oled(128, 32, &Wire, -1);
Preferences prefs;
portMUX_TYPE hxMux = portMUX_INITIALIZER_UNLOCKED;

// ---------------------------------------------------------------- settings
struct Settings {
  uint32_t magic;
  float countsPerGram;
  float span, depth;  // mm
  float k;            // extra scale from a hanging-weight calibration
  int32_t offset;
};
const uint32_t MAGIC = 0xBE170003;
Settings cfg = {MAGIC, 400.0f, 40.0f, 1.5f, 1.0f, 0};

void saveCfg() { prefs.putBytes("cfg", &cfg, sizeof cfg); }

// ---------------------------------------------------------------- HX711
bool hxReady() { return digitalRead(PIN_DT) == LOW; }

int32_t hxRead() {  // channel A, gain 128
  uint32_t v = 0;
  portENTER_CRITICAL(&hxMux);
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
  portEXIT_CRITICAL(&hxMux);
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
bool nextIsA = true;

static bool isNaN(float x) { return x != x; }
float gramsFrom(int32_t raw) { return (raw - cfg.offset) / cfg.countsPerGram; }
float tensionN(float grams) {
  return cfg.k * grams / 1000.0f * 9.80665f * cfg.span / (4.0f * cfg.depth);
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
  Serial.printf("span %.2f mm, depth %.3f mm, k %.4f, %.3f counts/g, offset %ld\n",
                cfg.span, cfg.depth, cfg.k, cfg.countsPerGram, (long)cfg.offset);
  Serial.printf("tension = plunger grams * %.4f N\n", tensionN(1.0f));
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
    } else if (line.startsWith("scale ")) {
      float k = line.substring(6).toFloat();
      if (k > 0.2f && k < 5.0f) {
        cfg.k = k;
        saveCfg();
      }
      showCfg();
    } else if (line == "raw") printRaw = !printRaw;
    else if (line == "show") showCfg();
    else if (line.length())
      Serial.println("commands: tare | cal GRAMS | geom SPAN DEPTH | scale K | raw | show");
    line = "";
  }
}

// ---------------------------------------------------------------- button
void handleButton() {
  static uint32_t down = 0, last = 0;
  static bool wasDown = false, longDone = false;
  if (millis() - last < 30) return;
  last = millis();
  bool isDown = digitalRead(PIN_BTN) == LOW;
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
// 128x32: big tension on top (text size 2 = 12x16 px per char), status below.
void draw(float t, bool stable, bool valid) {
  char buf[32];
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  if (!valid) {
    oled.setTextSize(2);
    oled.setCursor(0, 8);
    oled.print("no HX711");
    oled.display();
    return;
  }
  oled.setTextSize(2);
  oled.setCursor(0, 0);
  snprintf(buf, sizeof buf, "%5.1f N", t);
  oled.print(buf);
  // steady indicator: filled square when the reading has settled
  if (stable) oled.fillRect(120, 4, 8, 8, SSD1306_WHITE);
  else oled.drawRect(120, 4, 8, 8, SSD1306_WHITE);

  oled.setTextSize(1);
  oled.setCursor(0, 22);
  if (!isNaN(storedB)) {
    float d = 100.0f * (storedB - storedA) / storedA;
    snprintf(buf, sizeof buf, "A%.1f B%.1f %+.0f%%%s", storedA, storedB, d,
             fabsf(d) <= 5 ? " OK" : "");
  } else if (!isNaN(storedA)) {
    snprintf(buf, sizeof buf, "A %.1f  press: B", storedA);
  } else {
    snprintf(buf, sizeof buf, "press: store A");
  }
  oled.print(buf);
  oled.display();
}

// ---------------------------------------------------------------- main
void setup() {
  Serial.begin(115200);
  pinMode(PIN_SCK, OUTPUT);
  digitalWrite(PIN_SCK, LOW);
  pinMode(PIN_DT, INPUT);
  pinMode(PIN_BTN, INPUT_PULLUP);

  prefs.begin("beltgauge", false);
  Settings s;
  if (prefs.getBytes("cfg", &s, sizeof s) == sizeof s && s.magic == MAGIC &&
      s.countsPerGram != 0 && s.span > 0 && s.depth > 0 && s.k > 0)
    cfg = s;

  Wire.begin(PIN_SDA, PIN_SCL);
  if (!oled.begin(SSD1306_SWITCHCAPVCC, 0x3C))
    Serial.println("OLED not found at 0x3C (try 0x3D)");
  oled.clearDisplay();
  oled.setTextSize(2);
  oled.setTextColor(SSD1306_WHITE);
  oled.setCursor(0, 8);
  oled.print("taring...");
  oled.display();
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
    float spread = nhist ? fabsf(tensionN((hi - lo) / cfg.countsPerGram)) : 1e9;
    bool stable = nhist == NAVG && spread < max(0.3f, 0.02f * fabsf(t));
    draw(t, stable, valid);
  }
  delay(2);
}
