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

// Logs why the access point refused or dropped us, so Wi-Fi problems are diagnosable
// from the Serial Monitor instead of guessed at.
static void onWifiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  if (event != ARDUINO_EVENT_WIFI_STA_DISCONNECTED) return;
  const uint8_t reason = info.wifi_sta_disconnected.reason;
  const char *hint = "";
  switch (reason) {
    case WIFI_REASON_NO_AP_FOUND: hint = " (network not found: check WIFI_SSID and that it is 2.4 GHz)"; break;
    case WIFI_REASON_AUTH_FAIL:
    case WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT:
    case WIFI_REASON_HANDSHAKE_TIMEOUT: hint = " (likely wrong WIFI_PASSWORD)"; break;
    case WIFI_REASON_ASSOC_FAIL:
    case WIFI_REASON_ASSOC_TOOMANY: hint = " (access point refused: device limit or block list?)"; break;
    default: break;
  }
  Serial.printf("[WIFI] disconnected, reason %u%s\n", reason, hint);
}

// Lists the networks the ESP32 can actually hear. Used when connecting fails, so
// "network not found" can be told apart from a name typo or weak reception.
static void logVisibleNetworks() {
  Serial.println("[WIFI] scanning for visible networks...");
  WiFi.disconnect();
  const int found = WiFi.scanNetworks();
  if (found <= 0) {
    Serial.printf("[WIFI] scan found no networks (result %d)\n", found);
    return;
  }
  for (int i = 0; i < found; i++) {
    Serial.printf("[WIFI]   \"%s\"  ch %d  %d dBm  auth %d%s\n", WiFi.SSID(i).c_str(), WiFi.channel(i),
                  WiFi.RSSI(i), WiFi.encryptionType(i),
                  WiFi.SSID(i) == WIFI_SSID ? "  <-- WIFI_SSID matches" : "");
  }
  WiFi.scanDelete();
}

static void startWifi() {
  wifiConfigured = strlen(WIFI_SSID) > 0;
  if (!wifiConfigured) {
    Serial.println("[ERROR] WIFI_SSID is empty in config.h. Wi-Fi disabled until it is set.");
    return;
  }

  WiFi.onEvent(onWifiEvent);
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
    logVisibleNetworks();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
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

  // WiFi.reconnect() errors out ("sta is connecting") while an attempt is still in
  // progress, so restart the attempt cleanly instead.
  if (!connected && millis() - lastWifiRetryMs >= WIFI_RETRY_INTERVAL_MS) {
    Serial.println("[WIFI] reconnecting...");
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
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
