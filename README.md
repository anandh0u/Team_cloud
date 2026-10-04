# Team_cloud

Assistive bedside system for bedridden, partially paralysed and wheelchair-dependent
people: a voice-controlled robotic arm that fetches nearby objects, monitors daily
routine, and keeps caregivers informed.

Hackathon prototype. **Not a medical device**; it does not diagnose anything.

| Folder | What | Status |
|---|---|---|
| [`raspberry_pi/`](raspberry_pi/) | Main brain (Raspberry Pi 5): voice (Sarvam, Indian languages), YOLO vision + pose on the bedside phone camera, caregiver dashboard, activity reports with AI summary, SMS + automatic calls, ESP32 client | Working, 226 tests passing |
| [`esp32_controller/`](esp32_controller/) | ESP32 firmware (Arduino IDE): arm, gripper, heartbeat, MPU6050 over HTTP | Steps 1–11 written and compiling; pose angles need calibration |
| [`gripper_cad/`](gripper_cad/) | 3D-printable two-jaw gripper: Fusion script + ready STL/STEP files | Done |

## Gripper quick start

Print `gripper_cad/export/Base.stl`, `Jaw_Left.stl` and `Jaw_Right.stl` in one job
(PLA/PETG, 0.2 mm layers, 3–4 walls, 30–40% infill, no supports; base plate-side down,
jaws on their side). Assemble with 2 × M3×35 bolts + nyloc nuts.

To change dimensions: edit `gripper_cad/fusion_script/params.json`, copy the
`fusion_script` folder into Fusion's Scripts folder as `GripperCAD`, and run it from
**Utilities → Add-Ins**. Set `output_dir` to a folder on your machine first.

## How the parts talk

```
Raspberry Pi 5  --HTTP/JSON-->  ESP32 controller  -->  servos, heartbeat, MPU6050
      |
      +--HTTP-->  ESP32 camera (snapshots)
```

The Pi only ever requests **named poses** (`MEDICINE`, `USER`, ...). The ESP32
owns the servo angles. The API contract is in [`raspberry_pi/README.md`](raspberry_pi/README.md).
