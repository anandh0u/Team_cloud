#pragma once

#include <ArduinoJson.h>

// MPU6050 (movement) and analog pulse sensor. Hobby sensors: readings are rough
// estimates for the caregiver dashboard, never a medical measurement.

void sensorsBegin();

// Call every loop(): samples both sensors on their own schedule. Never blocks.
void sensorsUpdate();

// Fill the JSON shapes the Raspberry Pi expects (see raspberry_pi/README.md):
// heartbeat: {"available", "raw", "bpm_estimate"}; imu: {"available", "ax".."gz", "movement_score"}.
// A sensor that isn't working reports {"available": false} and no values.
void heartbeatToJson(JsonObject out);
void imuToJson(JsonObject out);

// Wiring help for GET /debug/sensors: pulse signal level and an I2C bus scan.
// Diagnostics only; the Pi never reads this.
void sensorsDebugToJson(JsonObject out);
