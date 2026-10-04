# ESP32 controller firmware

The hardware controller of the bedside assistant. It drives the 3-joint arm and the gripper,
reads the pulse sensor and the MPU6050, and takes commands from the Raspberry Pi over Wi-Fi
(HTTP + JSON).

The Pi only ever asks for **named poses** (`MEDICINE`, `USER`, …). This firmware owns the
servo angles, the joint limits and the emergency stop, so a bug or bad request on the Pi
can never drive a servo past its safe range.

Prototype only. The pulse sensor is a hobby sensor, and this firmware never classifies
medical conditions.

## Features

- **Smooth, non-blocking motion.** All joints arrive together with an eased start and stop.
  The web server never waits for a move, so `/stop` is handled immediately, even mid-move.
- **Named poses inside joint limits.** Every pose is checked against `JOINT_MIN_DEG` /
  `JOINT_MAX_DEG` at boot. Out-of-range poses are named in the Serial Monitor and refused.
- **Browser calibration** at `/calibrate`: move each joint with a slider and save the arm's
  position as a pose. Saved in flash: survives restarts, no re-upload needed.
- **Emergency stop** from the API or an optional physical button. After a stop, every motion
  command is refused until `/resume`, and resuming never moves the arm by itself.
- **Sensors:** pulse sensor with beat detection; MPU6050 movement score. The MPU6050 is read
  through its registers directly, so common clone chips work too. A sensor that isn't
  working reports `{"available": false}` instead of made-up values.
- **Wi-Fi that recovers by itself**, with diagnostics in the Serial Monitor, optional static
  IP and mDNS (`http://esp32-controller.local`).

## Hardware and wiring

ESP32 Dev Module, 4 hobby servos (e.g. MG996R for the joints, SG90 for the gripper),
MPU6050, analog pulse sensor, separate 5–6 V servo supply (at least 3 A).

| Part | ESP32 pin (default, change in `config.h`) |
|---|---|
| Base / shoulder / elbow servo signal | GPIO 13 / 14 / 27 |
| Gripper servo signal | GPIO 26 |
| MPU6050 SDA / SCL (VCC 3.3 V, GND) | GPIO 21 / 22 |
| Pulse sensor signal (+ 3.3 V, − GND) | GPIO 34 (must be 32–39: ADC2 pins stop working with Wi-Fi on) |
| Optional STOP button (other leg to GND) | set `STOP_BUTTON_PIN` |

- **Never power the servos from the ESP32's 5 V / 3.3 V pins.** The current spikes reset the ESP32.
- **Join the grounds:** the servo supply's GND must connect to the ESP32's GND.
- A 470–1000 µF capacitor across the servo supply near the servos smooths the spikes.

## Build and upload (Arduino IDE)

1. **Board package:** *File → Preferences → Additional boards manager URLs*:
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`, then in
   *Boards Manager* install **esp32 by Espressif Systems** (3.x).
2. **Board:** *Tools → Board → esp32 → ESP32 Dev Module*. *Upload Speed* 921600 (460800 if it fails).
3. **Libraries:** **ArduinoJson** 7.x and **ESP32Servo**. Nothing else is needed.
4. **Config:** copy `config.example.h` to `config.h` (git-ignored: it holds your Wi-Fi password).
   Set `WIFI_SSID`, `WIFI_PASSWORD` (2.4 GHz only), your pins, and the joint limits.
5. **Upload** `esp32_controller.ino`. If it hangs at `Connecting....`, hold **BOOT** and tap **EN**.
   If uploads fail halfway with "serial noise", use `tools/upload_chunked.ps1` (instructions inside).
6. Open the Serial Monitor at **115200** and press **EN**:

   ```
   [BOOT] esp32-controller firmware 0.3.0
   [ARM] 3 joints + gripper ready at HOME, 7 poses (6 calibrated)
   [IMU] motion sensor ready at 0x68 (chip id 0x68, MPU6050)
   [PULSE] reading pulse sensor on GPIO 34
   [WIFI] connected
   [WIFI] IP address: 192.168.1.50  RSSI: -55 dBm
   [BOOT] ready
   ```

   Put that IP in the Pi's `.env` as `ESP32_CONTROLLER_URL`.

## Calibrate the poses

Open `http://<ESP32 IP>/calibrate`:

1. Move the arm with the sliders (base, shoulder, elbow, gripper). It moves slowly and can't
   go past the joint limits. The red **STOP** button is always on screen.
2. When the arm is in place, tap **Save as …**:

   | Pose | Where the arm should be |
   |---|---|
   | `HOME` | resting, folded out of the way |
   | `SAFE` | raised, clear of everything |
   | `USER` | holding an object out to the patient's hand |
   | `MEDICINE`, `WATER`, `PHONE`, `SPOON` | gripper around each object, at its fixed spot on the table |

3. Gripper: save one angle as **OPEN** and a firm grip as **CLOSED**.
4. Check each pose with **Go**.

Pose names must match `raspberry_pi/config/poses.json`. The Pi refuses to fetch from a pose
the ESP32 reports as not calibrated. The page also prints the matching `POSE_TABLE` to paste
into `config.h`, which makes the calibration part of the code.

## HTTP API

Errors always have the form `{"ok": false, "error": "reason"}`.

| Method | Path | Reply / behaviour |
|---|---|---|
| GET | `/health` | `{"ok": true, "device", "firmware", "uptime_ms", "ip", "wifi_rssi", "free_heap"}` |
| GET | `/status` | arm (`pose`, `moving`, `angles`), `gripper`, `emergency_stop`, `poses`, `pose_angles`, `calibrated`, joint `limits` |
| GET | `/telemetry` | `heartbeat`, `imu`, `arm`, `gripper`, `emergency_stop` in one reply |
| GET | `/heartbeat` | `{"available": true, "raw": 1962, "bpm_estimate": 72.4}` or `{"available": false}` |
| GET | `/imu` | `{"available": true, "ax".."az" (m/s²), "gx".."gz" (deg/s), "movement_score"}` |
| POST | `/arm/pose` | body `{"pose": "WATER"}` → `{"ok": true, "pose": "WATER", "moving": true}`; 400 unknown pose, 409 stopped or busy |
| POST | `/arm/home` | same as a pose move to `HOME` |
| POST | `/gripper/open`, `/gripper/close` | `{"ok": true, "gripper": "OPEN", "moving": true}` |
| POST | `/stop` | freezes the arm; `{"ok": true, "emergency_stop": true}` |
| POST | `/resume` | allows motion again (doesn't move); `{"ok": true, "emergency_stop": false}` |
| GET | `/calibrate` | calibration page |
| POST | `/calibrate/jog` | `{"joint": 0, "angle": 120}` (joint 3 = gripper), within the joint limits |
| POST | `/calibrate/save` | `{"pose": "SPOON"}` or `{"gripper": "OPEN"}`: save the current position |
| POST | `/calibrate/reset` | forget all saved calibration (back to `POSE_TABLE`) |
| GET | `/debug/sensors` | pulse signal level and an I2C bus scan, with plain-language hints |
| GET | `/` | lists every endpoint |

Motion replies come back at once with `"moving": true`; the Pi polls `/status` until `moving`
is false before it sends the next step.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `WIFI_SSID is empty in config.h` | Fill in `config.h` and upload again |
| Dots forever, then `status 1` | Network not found: wrong name, or 5 GHz only |
| `status 4` / `status 6` | Usually a wrong password |
| The ESP32 resets when servos move | Servos powered from the ESP32, or grounds not joined |
| Servos twitch randomly | Grounds not joined |
| `MPU6050 not found` | Open `/debug/sensors`: no I2C devices = wiring/pins; a chip id other than 0x68 is a clone (works if it reads about 9.8 m/s² at rest) |
| Pulse always unavailable | `/debug/sensors` shows the raw level: 0 = not powered, flat = rest a fingertip lightly on it |
| A pose is refused at boot | The Serial Monitor names it: fix it on `/calibrate` or widen the joint limits |
| IP changes after every reboot | Set `WIFI_USE_STATIC_IP 1`, or use `http://esp32-controller.local` |

From the Pi: `python scripts/test_esp32.py` checks every read-only endpoint without moving the
arm; `python scripts/test_arm.py --pose USER --yes` moves the arm and checks that STOP blocks motion.
