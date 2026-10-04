#include "api_server.h"

#include <ArduinoJson.h>
#include <WebServer.h>
#include <WiFi.h>

#include "arm.h"
#include "config.h"
#include "sensors.h"

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

static void addArmState(JsonDocument &doc) {
  JsonObject arm = doc["arm"].to<JsonObject>();
  arm["pose"] = armPoseName();
  arm["moving"] = armMoving();
  JsonArray angles = arm["angles"].to<JsonArray>();
  for (int j = 0; j < JOINT_COUNT; j++) angles.add(roundf(armJointAngle(j)));
  arm["gripper_angle"] = roundf(gripperAngle());
  doc["gripper"] = gripperState();
  doc["emergency_stop"] = armEmergencyStop();
}

// Motion replies come back at once with "moving": true; the Pi polls /status until the
// arm is still. The server stays free the whole time, so /stop always gets through.
static void replyMotion(ArmError err, const char *key, const char *value) {
  if (err != ArmError::None) {
    int code = err == ArmError::UnknownPose ? 400 : err == ArmError::ConfigInvalid ? 500 : 409;
    sendError(code, armErrorText(err));
    return;
  }
  JsonDocument doc;
  doc["ok"] = true;
  doc[key] = value;
  doc["moving"] = armMoving();
  sendJson(200, doc);
}

static void handleStatus() {
  logRequest();
  JsonDocument doc;
  doc["ok"] = true;
  doc["firmware"] = FIRMWARE_VERSION;
  doc["config_valid"] = armConfigValid();
  addArmState(doc);
  JsonArray poses = doc["poses"].to<JsonArray>();
  for (int i = 0; i < armPoseCount(); i++) poses.add(armPoseNameAt(i));
  sendJson(200, doc);
}

static void handleTelemetry() {
  logRequest();
  JsonDocument doc;
  heartbeatToJson(doc["heartbeat"].to<JsonObject>());
  imuToJson(doc["imu"].to<JsonObject>());
  addArmState(doc);
  sendJson(200, doc);
}

static void handleHeartbeat() {
  logRequest();
  JsonDocument doc;
  heartbeatToJson(doc.to<JsonObject>());
  sendJson(200, doc);
}

static void handleImu() {
  logRequest();
  JsonDocument doc;
  imuToJson(doc.to<JsonObject>());
  sendJson(200, doc);
}

static void handleArmPose() {
  logRequest();
  JsonDocument body;
  if (deserializeJson(body, server.arg("plain")) || !body["pose"].is<const char *>()) {
    sendError(400, "body must be JSON like {\"pose\": \"MEDICINE\"}");
    return;
  }
  const char *pose = body["pose"];
  ArmError err = armMoveToPose(pose);
  replyMotion(err, "pose", armPoseName());
}

static void handleArmHome() {
  logRequest();
  replyMotion(armHome(), "pose", HOME_POSE);
}

static void handleGripperOpen() {
  logRequest();
  replyMotion(gripperOpen(), "gripper", "OPEN");
}

static void handleGripperClose() {
  logRequest();
  replyMotion(gripperClose(), "gripper", "CLOSED");
}

static void handleStop() {
  logRequest();
  armStop("api");
  JsonDocument doc;
  doc["ok"] = true;
  doc["emergency_stop"] = true;
  sendJson(200, doc);
}

static void handleResume() {
  logRequest();
  armResume();
  JsonDocument doc;
  doc["ok"] = true;
  doc["emergency_stop"] = false;
  sendJson(200, doc);
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
  {HTTP_GET,  "/status",        handleStatus},
  {HTTP_GET,  "/telemetry",     handleTelemetry},
  {HTTP_GET,  "/heartbeat",     handleHeartbeat},
  {HTTP_GET,  "/imu",           handleImu},
  {HTTP_POST, "/arm/home",      handleArmHome},
  {HTTP_POST, "/arm/pose",      handleArmPose},
  {HTTP_POST, "/gripper/open",  handleGripperOpen},
  {HTTP_POST, "/gripper/close", handleGripperClose},
  {HTTP_POST, "/stop",          handleStop},
  {HTTP_POST, "/resume",        handleResume},
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
