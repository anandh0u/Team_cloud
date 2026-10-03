// ESP32 hardware controller for the assistive bedside system.
// The Raspberry Pi 5 sends commands over HTTP; this board drives the arm,
// gripper and sensors. Prototype only: no medical diagnosis.
//
// Build step 1-2: Wi-Fi + GET /health.

#include <ESPmDNS.h>
#include <WiFi.h>

#include "api_server.h"
#include "config.h"

// Fail at compile time if config.h is missing a required setting.
#if !defined(FIRMWARE_VERSION) || !defined(DEVICE_NAME) || !defined(SERIAL_BAUD) ||       \
    !defined(WIFI_SSID) || !defined(WIFI_PASSWORD) || !defined(WIFI_CONNECT_TIMEOUT_MS) || \
    !defined(WIFI_RETRY_INTERVAL_MS) || !defined(WIFI_USE_STATIC_IP) || !defined(MDNS_ENABLED) || \
    !defined(HTTP_PORT)
#error "config.h is missing required settings. Copy config.example.h to config.h and fill it in."
#endif

static bool wifiConfigured = false;
static bool wifiWasConnected = false;
static unsigned long lastWifiRetryMs = 0;

static void onWifiConnected() {
  Serial.println("[WIFI] connected");
  Serial.printf("[WIFI] IP address: %s  RSSI: %d dBm\n",
                WiFi.localIP().toString().c_str(), WiFi.RSSI());
#if MDNS_ENABLED
  MDNS.end();
  if (MDNS.begin(DEVICE_NAME)) {
    MDNS.addService("http", "tcp", HTTP_PORT);
    Serial.printf("[WIFI] mDNS: http://%s.local\n", DEVICE_NAME);
  } else {
    Serial.println("[ERROR] mDNS failed to start (IP address still works)");
  }
#endif
}

static void startWifi() {
  wifiConfigured = strlen(WIFI_SSID) > 0;
  if (!wifiConfigured) {
    Serial.println("[ERROR] WIFI_SSID is empty in config.h. Wi-Fi disabled until it is set.");
    return;
  }

  WiFi.setHostname(DEVICE_NAME);  // must come before WiFi.mode()
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // lower, steadier latency for Pi commands
  WiFi.setAutoReconnect(true);
#if WIFI_USE_STATIC_IP
  if (!WiFi.config(IPAddress(WIFI_STATIC_IP), IPAddress(WIFI_GATEWAY), IPAddress(WIFI_SUBNET))) {
    Serial.println("[ERROR] static IP configuration rejected; check WIFI_STATIC_IP/GATEWAY/SUBNET");
  }
#endif

  Serial.printf("[WIFI] connecting to \"%s\"", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  const unsigned long started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < WIFI_CONNECT_TIMEOUT_MS) {
    delay(250);
    Serial.print('.');
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    wifiWasConnected = true;
    onWifiConnected();
  } else {
    Serial.printf("[ERROR] Wi-Fi not connected after %d ms (status %d). Retrying every %d ms in the background.\n",
                  WIFI_CONNECT_TIMEOUT_MS, WiFi.status(), WIFI_RETRY_INTERVAL_MS);
  }
  lastWifiRetryMs = millis();
}

// Non-blocking: called every loop so a Wi-Fi drop never stalls the arm or sensors.
static void maintainWifi() {
  if (!wifiConfigured) return;

  const bool connected = WiFi.status() == WL_CONNECTED;
  if (connected && !wifiWasConnected) {
    onWifiConnected();
  } else if (!connected && wifiWasConnected) {
    Serial.println("[ERROR] Wi-Fi connection lost");
    lastWifiRetryMs = millis();
  }
  wifiWasConnected = connected;

  if (!connected && millis() - lastWifiRetryMs >= WIFI_RETRY_INTERVAL_MS) {
    Serial.println("[WIFI] reconnecting...");
    WiFi.reconnect();
    lastWifiRetryMs = millis();
  }
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(200);
  Serial.println();
  Serial.printf("[BOOT] %s firmware %s\n", DEVICE_NAME, FIRMWARE_VERSION);

  startWifi();
  apiBegin();
  Serial.println("[BOOT] ready");
}

void loop() {
  apiHandle();
  maintainWifi();
}
