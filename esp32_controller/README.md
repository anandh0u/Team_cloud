# ESP32 controller firmware

Low-level hardware controller for the assistive bedside system. It drives the
3-axis arm and gripper, reads the heartbeat sensor and MPU6050, and takes
commands from the Raspberry Pi 5 over HTTP.

Prototype only. Heartbeat data is wellness data from a hobby sensor, and this
firmware never classifies medical conditions.

## Build progress

| Step | What | State |
|---|---|---|
| 1 | Wi-Fi (connect, auto-reconnect, optional static IP, mDNS) | done, running |
| 2 | `GET /health`, `GET /`, JSON errors | done, running |
| 3–6 | Arm (3 joints) + gripper, smooth motion, named poses, joint limits, browser calibration (`/calibrate`) | done, running |
| 7 | STOP / RESUME (API + optional button), motion refused while stopped | done, compiles |
| 8 | MPU6050 movement (`/imu`), direct register access so clone chips work too | done, compiles |
| 9 | Pulse sensor with beat detection (`/heartbeat`) | done, compiles |
| 10–11 | `/status`, `/telemetry`, faults reported as `{"available": false}` | done, compiles |

## Arduino IDE setup

1. **Board package.** In **File → Preferences → Additional boards manager URLs**, add
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`.
   Then in **Tools → Board → Boards Manager**, install **esp32 by Espressif Systems** (3.x).
2. **Board.** Choose **Tools → Board → esp32 → ESP32 Dev Module**.
3. **Libraries.** In **Tools → Manage Libraries**, install:
   - **ArduinoJson** by Benoit Blanchon, **version 7.x** (needed now)
   - **ESP32Servo** by Kevin Harrington (from step 3)
   - (MPU6050: no library needed; the firmware reads it directly, which also works with clone chips)
   - **Adafruit ADS1X15** (only if the heartbeat goes through an ADS1115; already installed)

   `WiFi.h`, `WebServer.h`, `ESPmDNS.h` and `Wire.h` come with the board package.
4. **Config.** Open `config.h` and set `WIFI_SSID` and `WIFI_PASSWORD`.
   The ESP32 only supports **2.4 GHz** Wi-Fi.
5. **Upload.** Open `esp32_controller.ino` (the IDE opens every file in the folder),
   select the COM port and click **Upload**. If it hangs at `Connecting....`,
   hold the board's **BOOT** button until the upload starts.

## Steps 3–11: arm, gripper, sensors

**Updating from 0.1.0:** copy the new sections at the end of `config.example.h` (Arm, MPU6050,
Heartbeat) into your `config.h`. The sketch won't compile until they're there, and says so.

**Wiring (defaults, change in `config.h`):**

| Part | ESP32 pin |
|---|---|
| Base / shoulder / elbow servo signal | GPIO 13 / 14 / 27 |
| Gripper servo signal | GPIO 26 |
| MPU6050 SDA / SCL (3.3 V, GND) | GPIO 21 / 22 |
| Pulse sensor signal (3.3 V, GND) | GPIO 34 (must be 32–39) |
| Optional STOP button (to GND) | set `STOP_BUTTON_PIN` |

Power the servos from a separate 5–6 V supply with its GND joined to the ESP32's GND.

**Calibrate the poses** (no re-upload needed): open `http://<ESP32 IP>/calibrate` in a browser.
Move each joint with its slider, and when the arm is where it should be, tap **Save as MEDICINE**
(and so on for HOME, SAFE, USER, WATER, PHONE, SPOON). Do the same for the gripper's OPEN and
CLOSED angles. Saved angles live in the ESP32's flash, survive restarts and override
`POSE_TABLE`. The page also shows the matching `POSE_TABLE` text to paste into `config.h`.

Set each joint's safe range first in `JOINT_MIN_DEG` / `JOINT_MAX_DEG`: sliders can't go past it,
and at boot every pose is checked against it (an out-of-range pose is named in the Serial
Monitor and named-pose moves are refused until it's fixed on the calibration page).

**How a move works:** `POST /arm/pose` answers at once with `"moving": true`, the arm moves
smoothly (all joints arrive together, eased start and stop), and the Pi polls `/status` until
`moving` is false. The web server never waits for a move, so `/stop` is handled immediately,
even mid-move. After a stop every motion command answers 409 until `/resume`; resuming never
moves the arm by itself.

**Wiring problems?** Open `http://<ESP32 IP>/debug/sensors` in a browser. It shows the pulse
sensor's raw signal with a hint, and scans the I2C bus to list every device found on the
MPU6050 pins.

**Test from the Pi** (after updating `ESP32_CONTROLLER_URL`):

```bash
python scripts/test_esp32.py               # health, status, telemetry, heartbeat, IMU
python scripts/test_arm.py --pose USER --yes   # moves the arm, checks STOP blocks motion
```

## Steps 1–2: Wi-Fi + /health

**Wiring:** none. Only USB to the PC.

**Test**

1. Upload, then open **Tools → Serial Monitor** at **115200** baud and press the board's **EN/RST** button.
2. Expected Serial output:

   ```
   [BOOT] esp32-controller firmware 0.1.0
   [WIFI] connecting to "YourNetwork"........
   [WIFI] connected
   [WIFI] IP address: 192.168.1.50  RSSI: -55 dBm
   [WIFI] mDNS: http://esp32-controller.local
   [HTTP] server started on port 80
   [BOOT] ready
   ```

3. From the Raspberry Pi (or any PC on the same Wi-Fi), using the IP from step 2:

   ```bash
   curl http://192.168.1.50/health
   ```

   Expected:

   ```json
   {"ok":true,"status":"ok","device":"esp32-controller","firmware":"0.1.0","uptime_ms":15342,"ip":"192.168.1.50","wifi_rssi":-55,"free_heap":234567}
   ```

   The Serial Monitor shows `[HTTP] GET /health`.

4. `curl http://192.168.1.50/` lists every endpoint with `"implemented": true/false`.
5. **Reconnect test:** switch the router (or phone hotspot) off and on again. Expected Serial output:
   `[ERROR] Wi-Fi connection lost`, then `[WIFI] reconnecting...`, then `[WIFI] connected` with the IP.
6. **Connect it to the Pi software.** In `raspberry_pi/.env`, set
   `MOCK_HARDWARE=false` and `ESP32_CONTROLLER_URL=http://192.168.1.50`. Then run:

   ```bash
   python scripts/test_esp32.py
   ```

   `GET /health` should show `[OK  ]`. The other endpoints show `[FAIL]` with
   `controller refused: not implemented yet` until their steps are built.

**Common problems**

| Symptom | Cause / fix |
|---|---|
| `[ERROR] WIFI_SSID is empty in config.h` | Fill in `config.h` and upload again |
| Dots forever, then `Wi-Fi not connected ... (status 1)` | Wrong network name, or a 5 GHz-only network. Status 1 = network not found |
| `status 4` or `status 6` | Usually a wrong password |
| `fatal error: ArduinoJson.h: No such file` | Install ArduinoJson 7.x |
| `JsonDocument was not declared` | ArduinoJson 6 is installed; update to 7.x |
| curl times out | The PC/Pi isn't on the same network, or the router isolates clients (common on guest Wi-Fi) |
| IP changes after every reboot | Set `WIFI_USE_STATIC_IP 1` with a free address, or use `http://esp32-controller.local` |
