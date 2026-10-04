#pragma once

#include <Arduino.h>

// Result of a motion command, mapped to HTTP status + {"ok": false, "error": ...} by the API.
enum class ArmError { None, UnknownPose, EmergencyStop, Busy, ConfigInvalid };

const char *armErrorText(ArmError e);

// Attaches the servos at the HOME pose. Returns false if the pose table is invalid
// (unknown HOME pose or an angle outside the joint limits): motion then stays refused.
bool armBegin();

// Call every loop(): advances smooth motion and reads the stop button. Never blocks.
void armUpdate();

// Motion commands return at once; the move then runs in armUpdate(). Poll armMoving().
ArmError armMoveToPose(const char *name);
ArmError armHome();
ArmError gripperOpen();
ArmError gripperClose();

// Freezes every joint where it is and refuses motion until armResume(). Always succeeds.
void armStop(const char *reason);
void armResume();

bool armMoving();
bool armEmergencyStop();
bool armConfigValid();
const char *armPoseName();      // target pose while moving; "UNKNOWN" after a stop mid-move
const char *gripperState();     // "OPEN", "CLOSED" or "UNKNOWN"
float armJointAngle(int joint);
float gripperAngle();
int armPoseCount();
const char *armPoseNameAt(int index);
