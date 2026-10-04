#include "sensors.h"

#include <Adafruit_MPU6050.h>
#include <Wire.h>

#include "config.h"

// ------------------------------------------------------------------ MPU6050

static Adafruit_MPU6050 mpu;
static bool imuOk = false;
static unsigned long imuLastSampleMs = 0, imuLastRetryMs = 0;
static float ax, ay, az, gx, gy, gz;  // m/s^2 and deg/s
static float movementScore = 0;

static void imuTryBegin() {
  imuLastRetryMs = millis();
  imuOk = mpu.begin(MPU6050_I2CADDR_DEFAULT, &Wire);
  if (imuOk) {
    mpu.setAccelerometerRange(MPU6050_RANGE_4_G);
    mpu.setGyroRange(MPU6050_RANGE_500_DEG);
    mpu.setFilterBandwidth(MPU6050_BAND_21_HZ);
    Serial.println("[IMU] MPU6050 ready");
  } else {
    Serial.println("[ERROR] MPU6050 not found on I2C (check SDA/SCL wiring); retrying every 5 s");
  }
}

static void imuUpdate() {
#if IMU_ENABLED
  if (!imuOk) {
    if (millis() - imuLastRetryMs > 5000) imuTryBegin();
    return;
  }
  if (millis() - imuLastSampleMs < 20) return;  // 50 Hz
  imuLastSampleMs = millis();
  sensors_event_t a, g, temp;
  if (!mpu.getEvent(&a, &g, &temp)) {
    imuOk = false;
    Serial.println("[ERROR] MPU6050 read failed; retrying");
    return;
  }
  ax = a.acceleration.x; ay = a.acceleration.y; az = a.acceleration.z;
  const float RAD_TO_DPS = 57.2958f;
  gx = g.gyro.x * RAD_TO_DPS; gy = g.gyro.y * RAD_TO_DPS; gz = g.gyro.z * RAD_TO_DPS;
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
