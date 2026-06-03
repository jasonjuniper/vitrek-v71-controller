// =============================================================================
// Juniper Test Station — Status Display
// WeMos D1 Mini (ESP8266) + SSD1306 128×64 OLED
//
// Polls the Flask API at http://{STATION_IP}:5000/api/rig/status every 2 s
// and displays live rig state: TC temps, heater duty, test name, alarm banner.
//
// Hardware:
//   WeMos D1 Mini — ESP8266EX
//   SSD1306 128×64 OLED — I²C on D1 (SCL / GPIO5) and D2 (SDA / GPIO4)
//   Built-in LED on GPIO2 (active LOW on D1 Mini)
//
// Libraries required (install via Arduino Library Manager):
//   ESP8266WiFi        — included in ESP8266 board package
//   ESP8266HTTPClient  — included in ESP8266 board package
//   ArduinoJson 6.x    — search "ArduinoJson" by Benoit Blanchon
//   Wire               — built-in
//   Adafruit GFX       — search "Adafruit GFX Library"
//   Adafruit SSD1306   — search "Adafruit SSD1306"
//
// Secrets: copy secrets.h.template to secrets.h and fill in your values.
//          secrets.h is listed in .gitignore and must NOT be committed.
// =============================================================================

#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <WiFiClient.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#include "secrets.h"   // WIFI_SSID, WIFI_PASSWORD, STATION_IP, STATION_PORT

// ---------------------------------------------------------------------------
// Display configuration
// ---------------------------------------------------------------------------

// SSD1306 128×64 connected via I²C.
// D1 Mini default I²C pins: D1=SCL (GPIO5), D2=SDA (GPIO4).
// Reset pin: -1 means share Arduino reset line (standard for most SSD1306 breakouts).
#define SCREEN_WIDTH   128
#define SCREEN_HEIGHT   64
#define OLED_RESET      -1
#define OLED_I2C_ADDR 0x3C   // Most SSD1306 breakouts; try 0x3D if display stays blank

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// ---------------------------------------------------------------------------
// Poll configuration
// ---------------------------------------------------------------------------

// How often to query the Flask API (milliseconds)
#define POLL_INTERVAL_MS  2000

// HTTP request timeout (milliseconds) — keeps UI responsive if station is down
#define HTTP_TIMEOUT_MS   1500

// ---------------------------------------------------------------------------
// GPIO
// ---------------------------------------------------------------------------

// Built-in LED on D1 Mini is GPIO2, active LOW (LOW = on, HIGH = off)
#define LED_PIN  2

// ---------------------------------------------------------------------------
// State variables — populated by the last successful JSON parse
// ---------------------------------------------------------------------------

struct RigState {
  float   tc1_ambient;       // TC1 ambient air temperature (°C)
  float   tc2_dut;           // TC2 DUT surface temperature (°C)
  float   tc3_heater;        // TC3 heater element temperature (°C)
  int     heater_duty;       // Heater PWM duty cycle (0–100 %)
  bool    plc_estop;         // true = E-stop clear (normal), false = E-stop triggered
  bool    plc_door;          // true = door closed (normal), false = door open
  bool    plc_overtemp;      // true = HW overtemp tripped, false = normal
  char    test_name[32];     // Active test name, or "IDLE"
  char    test_result[12];   // "PASS", "FAIL", "RUNNING", "IDLE", etc.
  bool    valid;             // true once we've received at least one good response
};

RigState rig = {0.0, 0.0, 0.0, 0, true, true, false, "IDLE", "IDLE", false};

// ---------------------------------------------------------------------------
// Timing
// ---------------------------------------------------------------------------

unsigned long lastPollMs = 0;   // millis() at last poll attempt
bool          lastPollOk = true; // was the last HTTP request successful?
int           failCount  = 0;    // consecutive failed polls

// ---------------------------------------------------------------------------
// Function prototypes
// ---------------------------------------------------------------------------

void connectWiFi();
bool pollStation();
void updateDisplay();
void showConnecting();
void showNoData();

// =============================================================================
// setup()
// =============================================================================

void setup() {
  // Serial for debug output — open at 115200 baud in Arduino Serial Monitor
  Serial.begin(115200);
  delay(100);
  Serial.println();
  Serial.println(F("=== Juniper Test Station — Status Display ==="));

  // Built-in LED: output, start OFF (HIGH = off on D1 Mini)
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH);

  // Initialise I²C — D1 Mini defaults are D1/D2 (GPIO5/4), which Wire uses automatically.
  // Explicit call not required but harmless: Wire.begin(SDA, SCL) = Wire.begin(4, 5)
  Wire.begin(4, 5);

  // Initialise SSD1306 display
  if (!display.begin(SSD1306_SWITCHCAPVCC, OLED_I2C_ADDR)) {
    Serial.println(F("ERROR: SSD1306 not found — check wiring and I2C address"));
    // Blink LED rapidly to signal hardware fault — never exits this loop
    while (true) {
      digitalWrite(LED_PIN, LOW);   delay(100);
      digitalWrite(LED_PIN, HIGH);  delay(100);
    }
  }

  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);

  // Show splash screen while connecting
  showConnecting();

  // Connect to WiFi — blocks until connected or forever retries
  connectWiFi();

  Serial.println(F("WiFi connected. Starting poll loop."));
}

// =============================================================================
// loop()
// =============================================================================

void loop() {
  unsigned long now = millis();

  // Poll the station every POLL_INTERVAL_MS milliseconds
  if (now - lastPollMs >= POLL_INTERVAL_MS) {
    lastPollMs = now;

    // Blink LED briefly before request (signals activity even if display frozen)
    digitalWrite(LED_PIN, LOW);

    bool ok = pollStation();

    // LED: keep ON (LOW) briefly to show poll happened, then off
    delay(40);
    digitalWrite(LED_PIN, HIGH);

    if (ok) {
      failCount  = 0;
      lastPollOk = true;
    } else {
      failCount++;
      lastPollOk = false;
      Serial.printf("Poll failed (%d consecutive)\n", failCount);
    }

    // Update display after each poll attempt (ok or not)
    if (lastPollOk || rig.valid) {
      updateDisplay();
    } else {
      showNoData();
    }
  }

  // Reconnect WiFi if dropped
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println(F("WiFi lost — reconnecting…"));
    showConnecting();
    connectWiFi();
  }
}

// =============================================================================
// connectWiFi()
// Blocks until WiFi is connected. Shows "CONNECTING..." on the display.
// =============================================================================

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  Serial.print(F("Connecting to "));
  Serial.println(WIFI_SSID);

  int dots = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print('.');
    dots++;

    // Refresh "CONNECTING..." screen with animated dots
    display.clearDisplay();
    display.setTextSize(1);
    display.setCursor(0, 0);
    display.println(F("JUNIPER STATION"));
    display.drawLine(0, 10, 127, 10, SSD1306_WHITE);
    display.setCursor(20, 28);
    display.setTextSize(1);
    display.print(F("CONNECTING"));
    for (int i = 0; i < (dots % 4); i++) display.print('.');
    display.setCursor(0, 48);
    display.setTextSize(1);
    display.print(F("SSID: "));
    display.println(WIFI_SSID);
    display.display();
  }

  Serial.println();
  Serial.print(F("Connected. IP: "));
  Serial.println(WiFi.localIP());
}

// =============================================================================
// pollStation()
// Sends GET to http://{STATION_IP}:{STATION_PORT}/api/rig/status
// Parses JSON into the global rig struct.
// Returns true on success, false on any error.
//
// Expected JSON shape (subset — extra keys are ignored):
// {
//   "tc1_ambient": 23.1,
//   "tc2_dut": 45.2,
//   "tc3_heater": 67.3,
//   "heater_duty": 45,
//   "plc_estop": 1,    // 1=clear, 0=tripped
//   "plc_door": 1,     // 1=closed, 0=open
//   "plc_overtemp": 0, // 0=normal, 1=tripped
//   "test_name": "HiPot_24V_Run1",
//   "test_result": "RUNNING"
// }
// =============================================================================

bool pollStation() {
  // Build URL string
  char url[80];
  snprintf(url, sizeof(url), "http://%s:%d/api/rig/status", STATION_IP, STATION_PORT);

  WiFiClient   wifiClient;
  HTTPClient   http;

  // Begin HTTP connection
  if (!http.begin(wifiClient, url)) {
    Serial.println(F("HTTP begin failed"));
    return false;
  }
  http.setTimeout(HTTP_TIMEOUT_MS);

  int httpCode = http.GET();

  if (httpCode != HTTP_CODE_200) {
    Serial.printf("HTTP GET returned %d\n", httpCode);
    http.end();
    return false;
  }

  // Read response body
  String payload = http.getString();
  http.end();

  // Parse JSON — ArduinoJson v6 style
  // StaticJsonDocument: size 384 bytes is sufficient for this payload.
  // Increase if the Flask API grows more fields.
  StaticJsonDocument<384> doc;
  DeserializationError err = deserializeJson(doc, payload);

  if (err) {
    Serial.print(F("JSON parse error: "));
    Serial.println(err.c_str());
    return false;
  }

  // Extract fields — use default values if key is missing so we never crash
  rig.tc1_ambient  = doc["tc1_ambient"]  | 0.0f;
  rig.tc2_dut      = doc["tc2_dut"]      | 0.0f;
  rig.tc3_heater   = doc["tc3_heater"]   | 0.0f;
  rig.heater_duty  = doc["heater_duty"]  | 0;
  rig.plc_estop    = (doc["plc_estop"]   | 1) == 1;   // 1 = clear = normal = true
  rig.plc_door     = (doc["plc_door"]    | 1) == 1;   // 1 = closed = normal = true
  rig.plc_overtemp = (doc["plc_overtemp"]| 0) == 1;   // 1 = tripped = fault = true

  // Copy strings safely — strlcpy truncates if too long
  const char* tn = doc["test_name"]   | "IDLE";
  const char* tr = doc["test_result"] | "IDLE";
  strlcpy(rig.test_name,   tn, sizeof(rig.test_name));
  strlcpy(rig.test_result, tr, sizeof(rig.test_result));

  rig.valid = true;

  Serial.printf("Poll OK: AMB=%.1f DUT=%.1f HTR=%.1f PWM=%d%% E-STOP=%d DOOR=%d OT=%d TEST=%s/%s\n",
    rig.tc1_ambient, rig.tc2_dut, rig.tc3_heater, rig.heater_duty,
    rig.plc_estop, rig.plc_door, rig.plc_overtemp,
    rig.test_name, rig.test_result);

  return true;
}

// =============================================================================
// updateDisplay()
// Renders the five-line status layout using the current rig struct.
//
// Layout (128×64 SSD1306, text size 1 = 6×8 px per char, 21 chars per line):
//   Line 1 (y=0):  "JUNIPER STATION"  header
//   ─ separator ─
//   Line 2 (y=12): "AMB:xx.x  DUT:xx.x"   TC1 and TC2 temps
//   Line 3 (y=22): "HTR:xxx.x PWM:xxx%"   TC3 and heater duty
//   Line 4 (y=34): "TEST: <name>" truncated to fit
//   Line 5 (y=44): "PASS" / "FAIL" / "RUNNING" / alarm banner
//
// If any PLC fault is active (E-stop open, door open, or HW overtemp),
// the bottom 20 px are replaced with a large "!! ALARM !!" banner.
// =============================================================================

void updateDisplay() {
  display.clearDisplay();

  // --- Line 1: header ---
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(14, 0);
  display.println(F("JUNIPER STATION"));

  // Separator line
  display.drawLine(0, 9, 127, 9, SSD1306_WHITE);

  // Determine alarm condition: any PLC safety fault active
  bool alarm = (!rig.plc_estop) || (!rig.plc_door) || (rig.plc_overtemp);

  // --- Line 2: TC1 ambient + TC2 DUT ---
  // Format: "AMB:23.1 DUT:45.2" — 19 chars comfortably fits at size 1
  display.setCursor(0, 12);
  display.setTextSize(1);
  char linebuf[24];
  snprintf(linebuf, sizeof(linebuf), "AMB:%5.1f DUT:%5.1f",
           rig.tc1_ambient, rig.tc2_dut);
  display.println(linebuf);

  // --- Line 3: TC3 heater + duty cycle ---
  // Format: "HTR:67.3  PWM: 45%"
  snprintf(linebuf, sizeof(linebuf), "HTR:%5.1f  PWM:%3d%%",
           rig.tc3_heater, rig.heater_duty);
  display.println(linebuf);

  // Thin separator before test status
  display.drawLine(0, 32, 127, 32, SSD1306_WHITE);

  // --- Line 4: active test name ---
  display.setCursor(0, 34);
  display.print(F("TEST:"));
  // Truncate test_name to 14 chars to fit line (5 for "TEST:" + 1 space + 14 = 20)
  char truncName[15];
  strlcpy(truncName, rig.test_name, sizeof(truncName));
  display.println(truncName);

  // --- Line 5: result or ALARM ---
  if (alarm) {
    // Draw solid inverse ALARM banner across bottom 20 px
    display.fillRect(0, 44, 128, 20, SSD1306_WHITE);
    display.setTextColor(SSD1306_BLACK);
    display.setTextSize(2);
    display.setCursor(8, 46);
    display.print(F("!! ALARM !!"));
    display.setTextColor(SSD1306_WHITE);   // Reset for next frame
  } else {
    // Show test result — highlight PASS/FAIL with inverse if done
    display.setCursor(0, 46);
    display.setTextSize(1);

    if (strcmp(rig.test_result, "PASS") == 0) {
      display.fillRect(0, 44, 40, 12, SSD1306_WHITE);
      display.setTextColor(SSD1306_BLACK);
      display.setCursor(2, 46);
      display.print(F("PASS"));
      display.setTextColor(SSD1306_WHITE);
    } else if (strcmp(rig.test_result, "FAIL") == 0) {
      display.fillRect(0, 44, 36, 12, SSD1306_WHITE);
      display.setTextColor(SSD1306_BLACK);
      display.setCursor(2, 46);
      display.print(F("FAIL"));
      display.setTextColor(SSD1306_WHITE);
    } else {
      // RUNNING, IDLE, or anything else — plain text
      display.print(rig.test_result);
    }

    // Also show alarm cause text if individual flags are set
    // (Shouldn't reach here if alarm=true, but defensive)
    if (!lastPollOk) {
      display.setCursor(50, 46);
      display.setTextSize(1);
      display.print(F("?comms"));
    }
  }

  display.display();
}

// =============================================================================
// showConnecting()
// Displays a "CONNECTING..." placeholder while WiFi is not yet up.
// Called during setup and on WiFi reconnect.
// =============================================================================

void showConnecting() {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(14, 0);
  display.println(F("JUNIPER STATION"));
  display.drawLine(0, 9, 127, 9, SSD1306_WHITE);
  display.setCursor(20, 28);
  display.println(F("CONNECTING..."));
  display.setCursor(0, 48);
  display.print(F("SSID: "));
  display.println(WIFI_SSID);
  display.display();
}

// =============================================================================
// showNoData()
// Displayed when consecutive poll failures mean rig state is stale/unknown.
// Shown only if we have never successfully polled (rig.valid == false).
// If rig.valid is true, updateDisplay() still runs with last known values.
// =============================================================================

void showNoData() {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(14, 0);
  display.println(F("JUNIPER STATION"));
  display.drawLine(0, 9, 127, 9, SSD1306_WHITE);

  // Large "NO DATA" message
  display.setTextSize(2);
  display.setCursor(16, 22);
  display.println(F("NO DATA"));

  display.setTextSize(1);
  display.setCursor(0, 48);
  display.print(F("Fails: "));
  display.print(failCount);
  display.print(F("  IP: "));
  display.println(STATION_IP);
  display.display();
}
