#include "api_server.h"

#include <ArduinoJson.h>
#include <WebServer.h>
#include <WiFi.h>

#include "config.h"

static WebServer server(HTTP_PORT);

// ------------------------------------------------------------------ helpers

static const char *methodName(HTTPMethod m) {
  switch (m) {
    case HTTP_GET: return "GET";
    case HTTP_POST: return "POST";
    default: return "OTHER";
  }
}

static void logRequest() {
  Serial.printf("[HTTP] %s %s\n", methodName(server.method()), server.uri().c_str());
}

static void sendJson(int code, JsonDocument &doc) {
  String body;
  serializeJson(doc, body);
  server.send(code, "application/json", body);
}

// Errors always use the shape the Raspberry Pi expects: {"ok": false, "error": "..."}
static void sendError(int code, const char *message) {
  JsonDocument doc;
  doc["ok"] = false;
  doc["error"] = message;
  sendJson(code, doc);
}

// ------------------------------------------------------------------ handlers

static void handleHealth() {
  logRequest();
  JsonDocument doc;
  doc["ok"] = true;
  doc["status"] = "ok";
  doc["device"] = DEVICE_NAME;
  doc["firmware"] = FIRMWARE_VERSION;
  doc["uptime_ms"] = millis();
  doc["ip"] = WiFi.localIP().toString();
  doc["wifi_rssi"] = WiFi.RSSI();
  doc["free_heap"] = ESP.getFreeHeap();
  sendJson(200, doc);
}

// Endpoints from later build steps answer {"ok": false} so the Pi treats them
// as refused instead of guessing.
static void handleNotImplemented() {
  logRequest();
  sendError(501, "not implemented yet");
}

static void handleNotFound() {
  logRequest();
  sendError(404, "not found");
}

static void handleRoot();

// ------------------------------------------------------------------ route table

struct Route {
  HTTPMethod method;
  const char *path;
  void (*handler)();
};

static const Route ROUTES[] = {
  {HTTP_GET,  "/",              handleRoot},
  {HTTP_GET,  "/health",        handleHealth},
  {HTTP_GET,  "/status",        handleNotImplemented},
  {HTTP_GET,  "/telemetry",     handleNotImplemented},
  {HTTP_GET,  "/heartbeat",     handleNotImplemented},
  {HTTP_GET,  "/imu",           handleNotImplemented},
  {HTTP_POST, "/arm/home",      handleNotImplemented},
  {HTTP_POST, "/arm/pose",      handleNotImplemented},
  {HTTP_POST, "/gripper/open",  handleNotImplemented},
  {HTTP_POST, "/gripper/close", handleNotImplemented},
  {HTTP_POST, "/stop",          handleNotImplemented},
  {HTTP_POST, "/resume",        handleNotImplemented},
};

static void handleRoot() {
  logRequest();
  JsonDocument doc;
  doc["ok"] = true;
  doc["device"] = DEVICE_NAME;
  doc["firmware"] = FIRMWARE_VERSION;
  JsonArray endpoints = doc["endpoints"].to<JsonArray>();
  for (const Route &r : ROUTES) {
    JsonObject e = endpoints.add<JsonObject>();
    e["method"] = methodName(r.method);
    e["path"] = r.path;
    e["implemented"] = r.handler != handleNotImplemented;
  }
  sendJson(200, doc);
}

// ------------------------------------------------------------------ public

void apiBegin() {
  for (const Route &r : ROUTES) {
    server.on(r.path, r.method, r.handler);
  }
  server.onNotFound(handleNotFound);
  server.begin();
  Serial.printf("[HTTP] server started on port %d\n", HTTP_PORT);
}

void apiHandle() {
  server.handleClient();
}
