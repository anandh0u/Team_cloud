#include "arm.h"

#include <ESP32Servo.h>

#include "config.h"

struct Pose {
  const char *name;
  float angles[JOINT_COUNT];
};

static const Pose POSES[] = POSE_TABLE;
static const int POSE_COUNT = sizeof(POSES) / sizeof(POSES[0]);
static const int JOINT_PIN[JOINT_COUNT] = JOINT_PINS;
static const char *const JOINT_NAME[JOINT_COUNT] = JOINT_NAMES;
static const float JOINT_MIN[JOINT_COUNT] = JOINT_MIN_DEG;
static const float JOINT_MAX[JOINT_COUNT] = JOINT_MAX_DEG;

static Servo joints[JOINT_COUNT];
static Servo gripper;

// Motion: every joint moves from `from` to `to` over `durationMs`, so all arrive together.
static float current[JOINT_COUNT], from[JOINT_COUNT], to[JOINT_COUNT];
static unsigned long moveStartMs = 0, moveDurationMs = 0;
static bool jointsMoving = false;

static float gripCurrent = GRIPPER_OPEN_DEG, gripFrom = GRIPPER_OPEN_DEG, gripTo = GRIPPER_OPEN_DEG;
static unsigned long gripStartMs = 0, gripDurationMs = 0;
static bool gripMoving = false;

static bool configValid = false;
static bool emergencyStop = false;
static const char *poseName = "UNKNOWN";
static const char *gripName = "OPEN";

const char *armErrorText(ArmError e) {
  switch (e) {
    case ArmError::UnknownPose: return "unknown pose";
    case ArmError::EmergencyStop: return "emergency stop active: motion refused until /resume";
    case ArmError::Busy: return "arm is still moving";
    case ArmError::ConfigInvalid: return "pose table in config.h is invalid (see Serial Monitor): motion refused";
    default: return "";
  }
}

static int toMicros(float deg) {
  return SERVO_MIN_US + (int)((SERVO_MAX_US - SERVO_MIN_US) * constrain(deg, 0.0f, 180.0f) / 180.0f);
}

static const Pose *findPose(const char *name) {
  for (const Pose &p : POSES) {
    if (strcmp(p.name, name) == 0) return &p;
  }
  return nullptr;
}

static bool validatePoses() {
  bool ok = findPose(HOME_POSE) != nullptr;
  if (!ok) Serial.printf("[ERROR] HOME_POSE \"%s\" is not in POSE_TABLE\n", HOME_POSE);
  for (const Pose &p : POSES) {
    for (int j = 0; j < JOINT_COUNT; j++) {
      if (p.angles[j] < JOINT_MIN[j] || p.angles[j] > JOINT_MAX[j]) {
        Serial.printf("[ERROR] pose %s: %s angle %.0f is outside %.0f-%.0f\n", p.name, JOINT_NAME[j],
                      p.angles[j], JOINT_MIN[j], JOINT_MAX[j]);
        ok = false;
      }
    }
  }
  return ok;
}

bool armBegin() {
  configValid = validatePoses();
  const Pose *home = findPose(HOME_POSE);
  for (int j = 0; j < JOINT_COUNT; j++) {
    current[j] = from[j] = to[j] = home ? home->angles[j] : (JOINT_MIN[j] + JOINT_MAX[j]) / 2;
    joints[j].setPeriodHertz(50);
    joints[j].attach(JOINT_PIN[j], SERVO_MIN_US, SERVO_MAX_US);
    joints[j].writeMicroseconds(toMicros(current[j]));
    delay(250);  // stagger the start-up current spikes
  }
  gripper.setPeriodHertz(50);
  gripper.attach(GRIPPER_PIN, SERVO_MIN_US, SERVO_MAX_US);
  gripper.writeMicroseconds(toMicros(gripCurrent));
  poseName = home ? home->name : "UNKNOWN";
#if STOP_BUTTON_PIN >= 0
  pinMode(STOP_BUTTON_PIN, INPUT_PULLUP);
#endif
  Serial.printf("[ARM] %d joints + gripper ready at %s, %d poses%s\n", JOINT_COUNT, poseName, POSE_COUNT,
                configValid ? "" : " (POSE TABLE INVALID: motion refused)");
  return configValid;
}

static ArmError checkCanMove() {
  if (emergencyStop) return ArmError::EmergencyStop;
  if (!configValid) return ArmError::ConfigInvalid;
  if (jointsMoving || gripMoving) return ArmError::Busy;
  return ArmError::None;
}

ArmError armMoveToPose(const char *name) {
  const Pose *pose = findPose(name);
  if (pose == nullptr) return ArmError::UnknownPose;
  ArmError err = checkCanMove();
  if (err != ArmError::None) return err;

  float longest = 0;
  for (int j = 0; j < JOINT_COUNT; j++) {
    from[j] = current[j];
    to[j] = constrain(pose->angles[j], JOINT_MIN[j], JOINT_MAX[j]);
    longest = max(longest, fabsf(to[j] - from[j]));
  }
  moveStartMs = millis();
  moveDurationMs = (unsigned long)(1000.0f * longest / ARM_SPEED_DEG_S);
  jointsMoving = true;
  poseName = pose->name;
  Serial.printf("[ARM] moving to %s (%lu ms)\n", pose->name, moveDurationMs);
  return ArmError::None;
}

ArmError armHome() { return armMoveToPose(HOME_POSE); }

static ArmError moveGripper(float target, const char *name) {
  ArmError err = checkCanMove();
  if (err != ArmError::None) return err;
  gripFrom = gripCurrent;
  gripTo = target;
  gripStartMs = millis();
  gripDurationMs = (unsigned long)(1000.0f * fabsf(gripTo - gripFrom) / GRIPPER_SPEED_DEG_S);
  gripMoving = true;
  gripName = name;
  Serial.printf("[ARM] gripper %s\n", name);
  return ArmError::None;
}

ArmError gripperOpen() { return moveGripper(GRIPPER_OPEN_DEG, "OPEN"); }
ArmError gripperClose() { return moveGripper(GRIPPER_CLOSED_DEG, "CLOSED"); }

void armStop(const char *reason) {
  if (jointsMoving) poseName = "UNKNOWN";  // frozen somewhere between poses
  if (gripMoving) gripName = "UNKNOWN";
  jointsMoving = gripMoving = false;
  for (int j = 0; j < JOINT_COUNT; j++) from[j] = to[j] = current[j];
  gripFrom = gripTo = gripCurrent;
  if (!emergencyStop) Serial.printf("[STOP] emergency stop (%s): arm frozen\n", reason);
  emergencyStop = true;
}

void armResume() {
  if (emergencyStop) Serial.println("[STOP] resumed: motion allowed again (the arm does not move by itself)");
  emergencyStop = false;
}

static float progress(unsigned long startMs, unsigned long durationMs) {
  if (durationMs == 0) return 1.0f;
  float t = (float)(millis() - startMs) / durationMs;
  t = constrain(t, 0.0f, 1.0f);
  return t * t * (3 - 2 * t);  // ease in and out: no jerk at the start or end
}

static void readStopButton() {
#if STOP_BUTTON_PIN >= 0
  static unsigned long lowSinceMs = 0;
  if (digitalRead(STOP_BUTTON_PIN) == LOW) {
    if (lowSinceMs == 0) lowSinceMs = millis();
    if (millis() - lowSinceMs > 30) armStop("button");  // 30 ms debounce
  } else {
    lowSinceMs = 0;
  }
#endif
}

void armUpdate() {
  readStopButton();
  if (jointsMoving) {
    float t = progress(moveStartMs, moveDurationMs);
    for (int j = 0; j < JOINT_COUNT; j++) {
      current[j] = from[j] + (to[j] - from[j]) * t;
      joints[j].writeMicroseconds(toMicros(current[j]));
    }
    if (t >= 1.0f) {
      jointsMoving = false;
      Serial.printf("[ARM] at %s\n", poseName);
    }
  }
  if (gripMoving) {
    float t = progress(gripStartMs, gripDurationMs);
    gripCurrent = gripFrom + (gripTo - gripFrom) * t;
    gripper.writeMicroseconds(toMicros(gripCurrent));
    if (t >= 1.0f) gripMoving = false;
  }
}

bool armMoving() { return jointsMoving || gripMoving; }
bool armEmergencyStop() { return emergencyStop; }
bool armConfigValid() { return configValid; }
const char *armPoseName() { return poseName; }
const char *gripperState() { return gripName; }
float armJointAngle(int joint) { return current[joint]; }
float gripperAngle() { return gripCurrent; }
int armPoseCount() { return POSE_COUNT; }
const char *armPoseNameAt(int index) { return POSES[index].name; }
