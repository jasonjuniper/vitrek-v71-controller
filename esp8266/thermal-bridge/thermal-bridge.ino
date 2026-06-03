// =============================================================================
// Juniper Test Station — Thermal Bridge Firmware
// WeMos D1 Mini (ESP8266)
//
// Replaces the Raspberry Pi GPIO layer. Reads three K-type thermocouples via
// SPI (MAX31855 amplifiers), drives the heater SSR via PWM, and controls two
// servo vent valves via PWM. Exposes a simple HTTP API so the NUC running the
// Flask app can read sensors and command actuators over WiFi.
//
// HTTP API
// --------
//   GET  /status          Full JSON snapshot: temps, heater duty, servo positions,
//                         any fault flags.
//   GET  /sensors         Thermocouple readings only (faster poll path).
//   POST /heater          Body: {"duty": 0-100}  — heater SSR PWM duty percent.
//   POST /servo           Body: {"a": 0-100, "b": 0-100}  — vent position percent
//                         (maps to 0°–180° servo travel).
//   POST /estop           Immediately zeros heater duty and centers servos (90°).
//
// Pin assignment (WeMos D1 Mini)
// --------------------------------
//   D5  GPIO14  SPI CLK   → CLK on all three MAX31855 modules
//   D6  GPIO12  SPI MISO  → DO  on all three MAX31855 modules
//   D1  GPIO5   CS TC1    → CS on MAX31855 #1  (TC1_AMBIENT)
//   D2  GPIO4   CS TC2    → CS on MAX31855 #2  (TC2_DUT)
//   D7  GPIO13  CS TC3    → CS on MAX31855 #3  (TC3_HEATER)
//   D8  GPIO15  PWM SSR   → Fotek SSR-40DA control+ (LOW at boot = SSR OFF = safe)
//   D3  GPIO0   PWM Srv A → Servo A signal (intake vent)
//   D4  GPIO2   PWM Srv B → Servo B signal (exhaust vent) — also drives onboard LED
//
// Notes:
//   D8 (GPIO15) is pulled LOW on the D1 Mini at boot, which keeps the heater
//   SSR safely off during startup.
//   GPIO0 and GPIO2 (D3/D4) are HIGH at boot — servos will briefly move to
//   their max position on first power-up before the firmware takes control.
//   This is harmless for vent servos. Disconnect servos when flashing.
//   The SSR-40DA control input needs 3–32V DC. The 3.3V GPIO output is just
//   above the minimum 3V spec. If the SSR is unreliable, add a 2N2222 BJT
//   buffer: GPIO8 → 1kΩ → base, emitter = GND, collector = SSR control+
//   through a 24V supply.
//
// Libraries (install via Arduino Library Manager)
//   ESP8266WiFi        — included with ESP8266 board package
//   ESP8266WebServer   — included with ESP8266 board package
//   ArduinoJson 6.x    — by Benoit Blanchon
//   Servo (ESP8266)    — included with ESP8266 board package
//   max6675            — search "MAX6675 library" by Adafruit OR use the raw SPI
//                        MAX31855 read below (no library needed — raw SPI only)
//
// Setup
//   1. Install ESP8266 board package in Arduino IDE (board manager URL:
//      https://arduino.esp8266.com/stable/package_esp8266com_index.json)
//   2. Select board: "LOLIN(WEMOS) D1 R2 & mini"
//   3. Copy secrets.h.template → secrets.h, fill in your WiFi credentials
//      and desired static IP.
//   4. Flash. Open Serial Monitor at 115200 to see IP address confirmation.
// =============================================================================

#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <ArduinoJson.h>
#include <Servo.h>
#include <SPI.h>
#include "secrets.h"

// ── Pin definitions ───────────────────────────────────────────────────────────
#define PIN_CS_TC1   5    // D1 GPIO5  — MAX31855 #1 chip-select (TC1 Ambient)
#define PIN_CS_TC2   4    // D2 GPIO4  — MAX31855 #2 chip-select (TC2 DUT)
#define PIN_CS_TC3  13    // D7 GPIO13 — MAX31855 #3 chip-select (TC3 Heater)

#define PIN_SSR     15    // D8 GPIO15 — heater SSR PWM control
#define PIN_SERVO_A  0    // D3 GPIO0  — intake vent servo
#define PIN_SERVO_B  2    // D4 GPIO2  — exhaust vent servo

// ── Safety limits ─────────────────────────────────────────────────────────────
#define MAX_HEATER_DUTY   90    // hard cap — matches rig_config.json max_duty_pct
#define WATCHDOG_TIMEOUT  10000 // ms — if no /heater command in this time, zero duty

// ── State ─────────────────────────────────────────────────────────────────────
ESP8266WebServer server(80);
Servo servoA, servoB;

float  tc1_c = 0, tc2_c = 0, tc3_c = 0, tc4_c = 0;
bool   tc1_fault = false, tc2_fault = false, tc3_fault = false;
int    heater_duty = 0;      // 0–100 %
int    servo_a_pct = 50;     // 0–100 % (maps to 0°–180°)
int    servo_b_pct = 50;
bool   estopped = false;
unsigned long last_heater_cmd_ms = 0;
unsigned long last_sensor_read_ms = 0;

// ── MAX31855 raw SPI read ─────────────────────────────────────────────────────
// Returns temperature in °C, or NAN on fault. No library required.
float readMAX31855(int cs_pin) {
  uint32_t data = 0;

  digitalWrite(cs_pin, LOW);
  delayMicroseconds(2);

  for (int i = 31; i >= 0; i--) {
    digitalWrite(14, LOW);       // CLK low (GPIO14 = D5, manual toggle)
    delayMicroseconds(1);
    if (digitalRead(12))         // MISO = GPIO12 = D6
      data |= (1UL << i);
    digitalWrite(14, HIGH);      // CLK high
    delayMicroseconds(1);
  }

  digitalWrite(cs_pin, HIGH);
  delayMicroseconds(2);

  // Bit 16 = fault flag
  if (data & 0x00010000) return NAN;

  // Bits 31:18 = thermocouple temperature (14-bit, 0.25°C/LSB, two's complement)
  int16_t raw = (int16_t)(data >> 18);
  if (raw & 0x2000) raw |= 0xC000;  // sign-extend 14-bit to 16-bit
  return raw * 0.25f;
}

void readAllSensors() {
  // Disable hardware SPI — we manually bit-bang to control CS separately
  // (hardware SPI on ESP8266 only supports one CS line via GPIO15)
  tc1_c = readMAX31855(PIN_CS_TC1);  tc1_fault = isnan(tc1_c);
  tc2_c = readMAX31855(PIN_CS_TC2);  tc2_fault = isnan(tc2_c);
  tc3_c = readMAX31855(PIN_CS_TC3);  tc3_fault = isnan(tc3_c);
  tc4_c = 0;  // TC4 not wired on D1 Mini (only 3 safe CS pins available)
}

// ── Actuator helpers ──────────────────────────────────────────────────────────
void applyHeaterDuty(int duty) {
  duty = constrain(duty, 0, MAX_HEATER_DUTY);
  heater_duty = duty;
  // D8 / GPIO15 — analogWrite range 0–1023
  analogWrite(PIN_SSR, map(duty, 0, 100, 0, 1023));
}

void applyServoPositions(int pct_a, int pct_b) {
  servo_a_pct = constrain(pct_a, 0, 100);
  servo_b_pct = constrain(pct_b, 0, 100);
  servoA.write(map(servo_a_pct, 0, 100, 0, 180));
  servoB.write(map(servo_b_pct, 0, 100, 0, 180));
}

void emergencyStop() {
  applyHeaterDuty(0);
  applyServoPositions(50, 50);  // centre vents
  estopped = true;
}

// ── JSON helpers ──────────────────────────────────────────────────────────────
void sendStatus() {
  StaticJsonDocument<256> doc;
  doc["tc1_c"]       = tc1_fault ? nullptr : (JsonVariant)tc1_c;
  doc["tc2_c"]       = tc2_fault ? nullptr : (JsonVariant)tc2_c;
  doc["tc3_c"]       = tc3_fault ? nullptr : (JsonVariant)tc3_c;
  doc["tc4_c"]       = nullptr;  // not wired
  doc["tc1_fault"]   = tc1_fault;
  doc["tc2_fault"]   = tc2_fault;
  doc["tc3_fault"]   = tc3_fault;
  doc["heater_duty"] = heater_duty;
  doc["servo_a_pct"] = servo_a_pct;
  doc["servo_b_pct"] = servo_b_pct;
  doc["estopped"]    = estopped;
  doc["uptime_s"]    = millis() / 1000;

  String out;
  serializeJson(doc, out);
  server.send(200, "application/json", out);
}

// ── Route handlers ────────────────────────────────────────────────────────────
void handleStatus()  { readAllSensors(); sendStatus(); }
void handleSensors() {
  readAllSensors();
  StaticJsonDocument<128> doc;
  doc["tc1_c"] = tc1_fault ? nullptr : (JsonVariant)tc1_c;
  doc["tc2_c"] = tc2_fault ? nullptr : (JsonVariant)tc2_c;
  doc["tc3_c"] = tc3_fault ? nullptr : (JsonVariant)tc3_c;
  doc["tc4_c"] = nullptr;
  String out; serializeJson(doc, out);
  server.send(200, "application/json", out);
}

void handleHeater() {
  if (estopped) {
    server.send(423, "application/json", "{\"error\":\"estopped\"}");
    return;
  }
  StaticJsonDocument<64> doc;
  if (deserializeJson(doc, server.arg("plain")) != DeserializationError::Ok) {
    server.send(400, "application/json", "{\"error\":\"bad json\"}");
    return;
  }
  int duty = doc["duty"] | 0;
  applyHeaterDuty(duty);
  last_heater_cmd_ms = millis();
  server.send(200, "application/json", "{\"ok\":true}");
}

void handleServo() {
  StaticJsonDocument<64> doc;
  if (deserializeJson(doc, server.arg("plain")) != DeserializationError::Ok) {
    server.send(400, "application/json", "{\"error\":\"bad json\"}");
    return;
  }
  int a = doc["a"] | servo_a_pct;
  int b = doc["b"] | servo_b_pct;
  applyServoPositions(a, b);
  server.send(200, "application/json", "{\"ok\":true}");
}

void handleEstop() {
  emergencyStop();
  server.send(200, "application/json", "{\"ok\":true,\"estopped\":true}");
}

void handleEstopClear() {
  // Allow resuming after an estop — caller's responsibility to confirm safe state first
  estopped = false;
  last_heater_cmd_ms = millis();
  server.send(200, "application/json", "{\"ok\":true,\"estopped\":false}");
}

// ── Setup ─────────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(100);
  Serial.println("\n\nJuniper Thermal Bridge starting...");

  // CS pins — output, deselected (HIGH)
  pinMode(PIN_CS_TC1, OUTPUT); digitalWrite(PIN_CS_TC1, HIGH);
  pinMode(PIN_CS_TC2, OUTPUT); digitalWrite(PIN_CS_TC2, HIGH);
  pinMode(PIN_CS_TC3, OUTPUT); digitalWrite(PIN_CS_TC3, HIGH);

  // SPI CLK and MISO — manual bit-bang
  pinMode(14, OUTPUT); digitalWrite(14, HIGH);  // CLK idle high
  pinMode(12, INPUT);                            // MISO

  // SSR PWM — start at 0 (heater off)
  pinMode(PIN_SSR, OUTPUT);
  analogWriteFreq(10);     // 10 Hz PWM matches rig_config.json heater pwm_hz
  analogWriteRange(1023);
  analogWrite(PIN_SSR, 0);

  // Servos — attach and centre
  servoA.attach(PIN_SERVO_A, 500, 2500);
  servoB.attach(PIN_SERVO_B, 500, 2500);
  applyServoPositions(50, 50);

  // Static IP from secrets.h
  IPAddress ip, gw, sn;
  ip.fromString(BRIDGE_IP);
  gw.fromString(BRIDGE_GATEWAY);
  sn.fromString(BRIDGE_SUBNET);
  WiFi.config(ip, gw, sn);

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to " WIFI_SSID);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500); Serial.print(".");
  }
  Serial.println();
  Serial.print("IP: "); Serial.println(WiFi.localIP());

  // HTTP routes
  server.on("/status",      HTTP_GET,  handleStatus);
  server.on("/sensors",     HTTP_GET,  handleSensors);
  server.on("/heater",      HTTP_POST, handleHeater);
  server.on("/servo",       HTTP_POST, handleServo);
  server.on("/estop",       HTTP_POST, handleEstop);
  server.on("/estop/clear", HTTP_POST, handleEstopClear);
  server.begin();
  Serial.println("HTTP server started on port 80");

  last_heater_cmd_ms = millis();
}

// ── Loop ──────────────────────────────────────────────────────────────────────
void loop() {
  server.handleClient();

  // Watchdog: if no heater command received in WATCHDOG_TIMEOUT ms, zero the duty.
  // This protects against NUC crash or network failure leaving heater running.
  if (!estopped && heater_duty > 0 &&
      (millis() - last_heater_cmd_ms) > WATCHDOG_TIMEOUT) {
    Serial.println("WATCHDOG: no heater command — zeroing duty");
    applyHeaterDuty(0);
  }
}
