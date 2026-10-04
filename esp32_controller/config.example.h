// Copy this file to config.h and fill it in.
// config.h holds your Wi-Fi password: keep it out of version control.
//
// After a firmware update, copy any NEW sections from this file into your config.h
// (the sketch refuses to compile and names what's missing).
#pragma once

// ---------------------------------------------------------------- Device
#define FIRMWARE_VERSION "0.2.1"
// Used as the Wi-Fi hostname and mDNS name (http://<DEVICE_NAME>.local)
#define DEVICE_NAME "esp32-controller"
#define SERIAL_BAUD 115200

// ---------------------------------------------------------------- Wi-Fi
// Required. Firmware refuses to start Wi-Fi while these are empty.
#define WIFI_SSID ""
#define WIFI_PASSWORD ""

// How long setup() waits for the first connection before carrying on.
#define WIFI_CONNECT_TIMEOUT_MS 20000
// After a drop-out, how often to retry (non-blocking; the arm keeps working).
#define WIFI_RETRY_INTERVAL_MS 10000

// Static IP keeps the Raspberry Pi's ESP32_CONTROLLER_URL stable.
// 1 = use the addresses below, 0 = let the router assign one (DHCP).
#define WIFI_USE_STATIC_IP 0
#define WIFI_STATIC_IP 192, 168, 1, 50
#define WIFI_GATEWAY 192, 168, 1, 1
#define WIFI_SUBNET 255, 255, 255, 0

// 1 = advertise http://<DEVICE_NAME>.local on the network
#define MDNS_ENABLED 1

// ---------------------------------------------------------------- HTTP
#define HTTP_PORT 80

// ---------------------------------------------------------------- Arm (steps 3-6)
// Joints in order: base, shoulder, elbow. Calibrate every angle below with the
// servo test sketch before mounting anything that can hit the bed or the patient.
#define JOINT_COUNT 3
#define JOINT_NAMES {"base", "shoulder", "elbow"}
#define JOINT_PINS {13, 14, 27}
// Safe range per joint (degrees). Poses outside it are refused at boot.
#define JOINT_MIN_DEG {0, 20, 0}
#define JOINT_MAX_DEG {180, 160, 180}
#define GRIPPER_PIN 26
#define GRIPPER_OPEN_DEG 30
#define GRIPPER_CLOSED_DEG 100
// Servo pulse range (SG90/MG996R typical).
#define SERVO_MIN_US 500
#define SERVO_MAX_US 2400
// Speeds. Slow is safer near a person; every joint arrives at the same moment.
#define ARM_SPEED_DEG_S 40
#define GRIPPER_SPEED_DEG_S 90

// Named poses: the ONLY positions the Raspberry Pi can request. Names must match
// raspberry_pi/config/poses.json exactly. Angles = {base, shoulder, elbow}.
#define POSE_TABLE {                 \
  {"HOME",     {90, 90, 90}},        \
  {"SAFE",     {90, 120, 60}},       \
  {"USER",     {150, 70, 110}},      \
  {"MEDICINE", {40, 60, 120}},       \
  {"WATER",    {60, 60, 120}},       \
  {"PHONE",    {20, 60, 120}},       \
  {"SPOON",    {80, 60, 120}},       \
}
#define HOME_POSE "HOME"

// Physical emergency stop button (to GND). -1 = no button.
#define STOP_BUTTON_PIN -1

// ---------------------------------------------------------------- MPU6050 (step 8)
// 1 = read the MPU6050 over I2C, 0 = report it as unavailable.
#define IMU_ENABLED 1
#define IMU_SDA_PIN 21
#define IMU_SCL_PIN 22

// ---------------------------------------------------------------- Heartbeat (step 9)
// Analog pulse sensor (PPG). Use an ADC1 pin (32-39): ADC2 doesn't work with Wi-Fi on.
#define HEARTBEAT_ENABLED 1
#define HEARTBEAT_PIN 34
// Smallest pulse swing (ADC counts) treated as a real signal rather than noise.
#define HEARTBEAT_MIN_AMPLITUDE 60
