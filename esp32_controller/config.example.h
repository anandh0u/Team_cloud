// Copy this file to config.h and fill it in.
// config.h holds your Wi-Fi password: keep it out of version control.
//
// Sections for servos, poses, MPU6050 and heartbeat are added as each build
// step is implemented.
#pragma once

// ---------------------------------------------------------------- Device
#define FIRMWARE_VERSION "0.1.0"
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
