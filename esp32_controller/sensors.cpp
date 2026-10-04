#include "sensors.h"

#include <Wire.h>

#include "config.h"

// ------------------------------------------------------------------ MPU6050

// Talks to the registers directly instead of through a library: many boards sold as
// MPU6050 are clones (MPU6500, 6886, ...) that report a different chip ID, and strict
// libraries refuse them. The registers used here are the same on all of them.
static const uint8_t REG_CONFIG = 0x1A, REG_GYRO_CONFIG = 0x1B, REG_ACCEL_CONFIG = 0x1C,
                     REG_ACCEL_XOUT_H = 0x3B, REG_PWR_MGMT_1 = 0x6B, REG_WHO_AM_I = 0x75;
static const float ACCEL_LSB_PER_G = 8192.0f;   // +-4 g range
static const float GYRO_LSB_PER_DPS = 65.5f;    // +-500 deg/s range

static uint8_t imuAddr = 0;      // 0x68 or 0x69 once found
static uint8_t imuWhoAmI = 0;
static bool imuOk = false;
static unsigned long imuLastSampleMs = 0, imuLastRetryMs = 0;
static float ax, ay, az, gx, gy, gz;  // m/s^2 and deg/s
static float movementScore = 0;

static bool imuWrite(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(imuAddr);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

static bool imuRead(uint8_t reg, uint8_t *buf, size_t len) {
  Wire.beginTransmission(imuAddr);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(imuAddr, (uint8_t)len) != len) return false;
  for (size_t i = 0; i < len; i++) buf[i] = Wire.read();
  return true;
}

static void imuTryBegin() {
  imuLastRetryMs = millis();
  imuOk = false;
  for (uint8_t addr : {0x68, 0x69}) {  // 0x69 when the board's AD0 pin is high
    imuAddr = addr;
    uint8_t who = 0;
    if (!imuRead(REG_WHO_AM_I, &who, 1)) continue;
    imuWhoAmI = who;
    imuOk = imuWrite(REG_PWR_MGMT_1, 0x01)       // wake up, gyro clock
            && imuWrite(REG_CONFIG, 0x04)        // ~21 Hz low-pass filter
            && imuWrite(REG_GYRO_CONFIG, 0x08)   // +-500 deg/s
            && imuWrite(REG_ACCEL_CONFIG, 0x08); // +-4 g
    if (imuOk) {
      Serial.printf("[IMU] motion sensor ready at 0x%02X (chip id 0x%02X%s)\n", addr, who,
                    who == 0x68 ? ", MPU6050" : ", compatible clone");
      return;
    }
  }
  Serial.println("[ERROR] MPU6050 not found at 0x68/0x69 (check SDA/SCL, 3.3 V, GND); retrying every 5 s");
}

static void imuUpdate() {
#if IMU_ENABLED
  if (!imuOk) {
    if (millis() - imuLastRetryMs > 5000) imuTryBegin();
    return;
  }
  if (millis() - imuLastSampleMs < 20) return;  // 50 Hz
  imuLastSampleMs = millis();
  uint8_t b[14];  // accel x/y/z, temperature, gyro x/y/z: big-endian 16-bit each
  if (!imuRead(REG_ACCEL_XOUT_H, b, sizeof(b))) {
    imuOk = false;
    Serial.println("[ERROR] MPU6050 read failed; retrying");
    return;
  }
  auto word = [&](int i) { return (int16_t)((b[i] << 8) | b[i + 1]); };
  const float G = 9.80665f;
  ax = word(0) / ACCEL_LSB_PER_G * G; ay = word(2) / ACCEL_LSB_PER_G * G; az = word(4) / ACCEL_LSB_PER_G * G;
  gx = word(8) / GYRO_LSB_PER_DPS; gy = word(10) / GYRO_LSB_PER_DPS; gz = word(12) / GYRO_LSB_PER_DPS;
  // Movement score: how far the total acceleration is from still (1 g) plus how fast it
  // rotates, smoothed over about 2 s. 0 = still; roughly 1+ = clear movement. Unitless.
  float accelDev = fabsf(sqrtf(ax * ax + ay * ay + az * az) - 9.81f);
  float rotation = sqrtf(gx * gx + gy * gy + gz * gz) / 50.0f;
  movementScore += 0.01f * ((accelDev + rotation) - movementScore);
#endif
}

void imuToJson(JsonObject out) {
  out["available"] = (bool)(IMU_ENABLED && imuOk);
  if (!(IMU_ENABLED && imuOk)) return;
  out["ax"] = roundf(ax * 1000) / 1000; out["ay"] = roundf(ay * 1000) / 1000; out["az"] = roundf(az * 1000) / 1000;
  out["gx"] = roundf(gx * 100) / 100; out["gy"] = roundf(gy * 100) / 100; out["gz"] = roundf(gz * 100) / 100;
  out["movement_score"] = roundf(movementScore * 1000) / 1000;
}

// ------------------------------------------------------------------ pulse sensor

static const int IBI_COUNT = 5;
static int hbRaw = 0;
static float hbBaseline = 0, hbMax = 0, hbMin = 4095;
static bool hbAbove = false;
static unsigned long hbLastSampleMs = 0, hbLastBeatMs = 0;
static unsigned long ibis[IBI_COUNT];
static int ibiCount = 0, ibiNext = 0;

static void heartbeatUpdate() {
#if HEARTBEAT_ENABLED
  if (millis() - hbLastSampleMs < 10) return;  // 100 Hz
  hbLastSampleMs = millis();
  hbRaw = analogRead(HEARTBEAT_PIN);
  if (hbBaseline == 0) hbBaseline = hbRaw;
  hbBaseline += 0.02f * (hbRaw - hbBaseline);
  // Envelope that slowly decays back towards the baseline, so the threshold follows the signal.
  hbMax = max(hbMax - 0.5f, (float)hbRaw);
  hbMin = min(hbMin + 0.5f, (float)hbRaw);
  float amplitude = hbMax - hbMin;
  if (amplitude < HEARTBEAT_MIN_AMPLITUDE) {
    hbAbove = false;  // flat or noisy: no finger on the sensor
    return;
  }
  float threshold = hbBaseline + amplitude * 0.25f;
  if (!hbAbove && hbRaw > threshold) {
    hbAbove = true;
    unsigned long now = millis();
    unsigned long ibi = now - hbLastBeatMs;
    if (hbLastBeatMs != 0 && ibi >= 300 && ibi <= 2000) {  // 30-200 bpm
      ibis[ibiNext] = ibi;
      ibiNext = (ibiNext + 1) % IBI_COUNT;
      if (ibiCount < IBI_COUNT) ibiCount++;
    }
    if (hbLastBeatMs == 0 || ibi >= 300) hbLastBeatMs = now;
  } else if (hbAbove && hbRaw < hbBaseline) {
    hbAbove = false;
  }
#endif
}

void heartbeatToJson(JsonObject out) {
  // Only report a pulse while beats keep coming; a stale number is worse than none.
  bool recent = hbLastBeatMs != 0 && millis() - hbLastBeatMs < 3000;
  bool available = HEARTBEAT_ENABLED && recent && ibiCount >= 3;
  out["available"] = available;
  if (!available) return;
  unsigned long sum = 0;
  for (int i = 0; i < ibiCount; i++) sum += ibis[i];
  out["raw"] = hbRaw;
  out["bpm_estimate"] = roundf(600000.0f * ibiCount / sum) / 10;
}

// ------------------------------------------------------------------ diagnostics

void sensorsDebugToJson(JsonObject out) {
  JsonObject pulse = out["pulse"].to<JsonObject>();
  pulse["enabled"] = (bool)HEARTBEAT_ENABLED;
  pulse["pin"] = HEARTBEAT_PIN;
  pulse["raw"] = hbRaw;
  pulse["baseline"] = (int)hbBaseline;
  pulse["swing"] = (int)(hbMax - hbMin);  // must exceed min_swing to count as a signal
  pulse["min_swing"] = HEARTBEAT_MIN_AMPLITUDE;
  pulse["beats_stored"] = ibiCount;
  pulse["ms_since_last_beat"] = hbLastBeatMs ? (long)(millis() - hbLastBeatMs) : -1;
  pulse["hint"] = hbRaw <= 5 ? "reads 0: sensor not powered or wrong pin"
                : hbRaw >= 4090 ? "reads max: signal pin shorted to 3.3 V or wrong pin"
                : (hbMax - hbMin) < HEARTBEAT_MIN_AMPLITUDE ? "flat signal: rest a fingertip lightly on the sensor"
                : "signal present";

  JsonObject imu = out["imu"].to<JsonObject>();
  imu["enabled"] = (bool)IMU_ENABLED;
  imu["sda_pin"] = IMU_SDA_PIN;
  imu["scl_pin"] = IMU_SCL_PIN;
  imu["found"] = imuOk;
  if (imuOk) {
    char hex[5];
    snprintf(hex, sizeof(hex), "0x%02X", imuWhoAmI);
    imu["chip_id"] = hex;  // 0x68 = genuine MPU6050; other values = compatible clone
  }
  JsonArray devices = imu["i2c_devices"].to<JsonArray>();
#if IMU_ENABLED
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      char hex[5];
      snprintf(hex, sizeof(hex), "0x%02X", addr);
      devices.add(hex);
    }
  }
#endif
  imu["hint"] = devices.size() == 0 ? "no I2C device on these pins: check SDA/SCL pins, 3.3 V and GND"
              : imuOk ? "MPU6050 working"
              : "a device answers but not at 0x68/0x69: is this really the motion sensor?";
}

// ------------------------------------------------------------------ public

void sensorsBegin() {
#if IMU_ENABLED
  Wire.begin(IMU_SDA_PIN, IMU_SCL_PIN);
  imuTryBegin();
#endif
#if HEARTBEAT_ENABLED
  analogReadResolution(12);
  pinMode(HEARTBEAT_PIN, INPUT);
  Serial.printf("[PULSE] reading pulse sensor on GPIO %d\n", HEARTBEAT_PIN);
#endif
}

void sensorsUpdate() {
  imuUpdate();
  heartbeatUpdate();
}
