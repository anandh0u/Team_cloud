#pragma once

#include <Arduino.h>

// Result of a motion command, mapped to HTTP status + {"ok": false, "error": ...} by the API.
enum class ArmError { None, UnknownPose, EmergencyStop, Busy, ConfigInvalid, OutOfRange };

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

// Calibration (the /calibrate page). jog moves one joint (index JOINT_COUNT = gripper) to an
// angle inside its limits; save stores the arm's current angles in flash as that pose,
// overriding POSE_TABLE, and survives restarts. Reset forgets everything saved.
ArmError armJog(int joint, float angle);
ArmError armSavePose(const char *name);
ArmError gripperSave(bool open);
void armResetCalibration();

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
float armPoseAngle(int pose, int joint);
bool armPoseCalibrated(int pose);
const char *armJointName(int joint);
float armJointMin(int joint);
float armJointMax(int joint);
float gripperOpenAngle();
float gripperClosedAngle();
