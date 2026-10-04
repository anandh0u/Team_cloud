# Assistive bedside system: Raspberry Pi 5 software

Hackathon prototype. **Not a medical device.** It does not diagnose anything, and
heartbeat readings come from a hobby sensor, so they are not clinically accurate.

The Pi is the main brain. The ESP32 controller (arm, gripper, heartbeat, MPU6050)
is a separate project that this code talks to over HTTP. A **phone on a bedside
stand** replaces a separate camera, microphone and speaker: it opens a web page
served by the Pi, sends voice + a photo with each command, and plays the reply.

## Status

| Phase | What | State |
|---|---|---|
| 1 | Structure, config, logging, `GET /health` | done |
| 2 | ESP32 controller client, telemetry, arm actions, mock hardware, all hardware routes | done |
| 3 | Intent parser, emergency handling, assistant text API, caregiver comms (MOCK backend) | done |
| 4a | YOLO object detection on phone photos (`POST /vision/detect`) | done |
| 4b | Phone page `/phone`: voice (Sarvam) + live camera in, spoken reply out; "where is my X" answered from the photo | done |
| 6 | Activity history (SQLite) and `/report`: trend vs the previous period, 7-day chart, AI summary (OpenAI, numbers only) | done |
| 5 | Live detection boxes, caregiver dashboard `/dashboard` (camera pose + ESP32 sensors), real SMS via Android gateway, password + online access | done |
| later | Custom YOLO model for medicine, SQLite, routine analysis, LLM summaries, real messaging | not started |

## API (current)

| Method | Path | What |
|---|---|---|
| GET | `/health` | API up + ESP32 reachability |
| GET | `/status` | ESP32 status, allowed poses |
| GET | `/telemetry` | heartbeat, IMU, arm, gripper, emergency_stop (unavailable sections are `null` + listed in `errors`) |
| POST | `/assistant/text` | `{"text": "Where is my phone?"}` returns action + spoken `response` |
| POST | `/find-object` | `{"object": "phone"}` |
| POST | `/arm/pose` | `{"pose": "MEDICINE"}`: 200 ok, 422 unknown pose, 409 refused by ESP32, 503 unreachable |
| POST | `/arm/home`, `/arm/stop`, `/arm/resume` | resume is never automatic |
| POST | `/caregiver/message` | `{"contact": "son", "message": "..."}` |
| POST | `/caregiver/call` | `{"contact": "son"}` |
| POST | `/emergency` | stops the arm and alerts the caregiver in parallel |
| POST | `/vision/detect` | multipart `image`: YOLO detections (label, confidence, box in pixels). 503 if `YOLO_MODEL` is unset |
| POST | `/assistant/phone` | multipart: `text` or `audio`, plus optional `image`. Returns transcript, assistant reply, detections and `reply_audio` (base64 WAV) |
| GET | `/phone` | the bedside phone page |
| GET | `/dashboard`, `/dashboard/state`, `/dashboard/frame.jpg` | caregiver dashboard, its live data, latest camera frame |
| GET | `/report`, `/report/data?hours=24&ai=true` | activity report page and its data (1–168 hours) |

## How commands are understood

`config/intents/en.json` holds every phrase and pattern. Matching is fully local and
deterministic, checked in this order:

**EMERGENCY > STOP > HOME > STATUS > MESSAGE > CALL > FIND > GET > UNKNOWN**

- Emergency phrases always win. "Help me find my phone" is **not** an emergency
  (bare "help" must be the whole sentence), but "I need help" anywhere is.
- "Tell my daughter I need help" is an emergency **and** the message still goes to her.
- Calling the caregiver is treated as an emergency.
- Unknown objects and contacts stay `null`; they are never guessed.
- An LLM (later phase) will only ever see sentences that end up `UNKNOWN`.

On EMERGENCY the arm stop and the caregiver alert run **at the same time**, so an
unreachable ESP32 can't delay the alert. The reply only says "I am contacting your
caregiver" if the alert actually went out.

`COMMUNICATION_BACKEND=MOCK` only **logs** messages, calls and alerts; nothing is sent yet.

## Setup

On the Raspberry Pi (Raspberry Pi OS 64-bit):

```bash
cd raspberry_pi
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env        # then edit .env
```

On Windows, use `.venv\Scripts\activate` and `copy .env.example .env`.

## Configuration

All settings come from `.env` (see `.env.example`). Nothing has a hidden default:
if a required value is missing or malformed, the app refuses to start and names
the variable.

| Variable | Required | Meaning |
|---|---|---|
| `MOCK_HARDWARE` | yes | `true` simulates all hardware; `false` uses the real ESP32 |
| `API_HOST`, `API_PORT` | yes | where FastAPI listens |
| `LOG_LEVEL` | yes | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `ASSISTANT_LANGUAGE` | yes | selects `config/responses/<lang>.json` |
| `CATALOG_DIR` | yes | folder with `poses.json`, `mock_hardware.json`, `responses/` |
| `ESP32_CONTROLLER_URL` | when not mock | e.g. `http://192.168.1.50` |
| `ESP32_HTTP_TIMEOUT_S` | yes | per-request timeout |
| `ESP32_HTTP_RETRIES` | yes | extra attempts for safe-to-repeat calls only |
| `ESP32_RETRY_BACKOFF_S` | yes | wait between retries (grows linearly) |
| `YOLO_MODEL` | no | e.g. `data/models/yolo11n.pt` (downloaded if missing). Empty = vision off |
| `YOLO_CONFIDENCE` | when `YOLO_MODEL` is set | minimum detection score, 0.01–1 |
| `SCENE_MAX_AGE_S` | when `YOLO_MODEL` is set | "where is my X" only trusts a photo this recent |
| `SARVAM_API_KEY` | no | empty = voice off (typing still works, replies aren't spoken by Sarvam) |
| `SARVAM_STT_MODEL`, `SARVAM_STT_MODE`, `SARVAM_TTS_MODEL`, `SARVAM_TTS_SPEAKER`, `SARVAM_TTS_LANGUAGE`, `SARVAM_TIMEOUT_S` | when the key is set | see `.env.example`. `translate` mode turns Malayalam, Hindi, etc. into English for the intent parser |
| `HTTPS_PORT`, `TLS_CERT_FILE`, `TLS_KEY_FILE` | no | extra HTTPS port for the phone page (mic + live camera need HTTPS) |

Catalogs in `config/`:

- `poses.json`: the **only** arm positions the Pi may request, plus the object → pose mapping.
- `responses/en.json`: every spoken or returned phrase. Add `ml.json` / `hi.json` later with the same keys.
- `mock_hardware.json`: simulated values for mock mode. **Synthetic, not patient data.**

## Run

```bash
python run.py
curl http://localhost:8000/health
```

## Phone page

1. Create the HTTPS certificate once (again if the Pi's IP changes): `scripts/make_cert.sh`
2. `python run.py` serves `http://<Pi IP>:8000` and `https://<Pi IP>:8443`.
3. On the phone (same Wi-Fi) open `https://<Pi IP>:8443/phone`. The browser warns that the
   certificate is not trusted: tap **Advanced → Proceed**. Allow camera and microphone.
4. Point the phone at the bedside table. Tap the green button, speak, tap again.
   The page sends the recording and a snapshot; the reply is shown and spoken.

On plain `http://…:8000/phone` voice is unavailable (browser rule), but typing and the photo button work.

Object positions (left/middle/right) are from the camera's point of view. Which YOLO
labels count as which object is set in `config/vision_labels.json`. The standard model
can't recognise medicine, so the assistant says so instead of guessing.

## Caregiver dashboard

`/dashboard` refreshes every 2 seconds:

- **Needs attention:** no movement seen for `STILL_ATTENTION_MIN`, out of camera view for
  `AWAY_ATTENTION_MIN`, camera or sensors not reporting, arm stopped.
- **Position and last movement:** from the YOLO pose model on live camera frames (every `POSE_INTERVAL_S`).
  Lying / reclined / upright comes from the torso angle; movement from how far body points shift.
- **Pulse and motion:** ESP32 heartbeat + MPU6050, polled every `SENSOR_POLL_S`.
- **Camera:** the latest frame from the bedside phone, with detection boxes.
- **Recent activity:** every command and reply, message, call request and emergency alert, with failures in red.

All of it is in memory and is lost on restart. These are observations, not diagnoses.

## Activity report

Every pose reading (in view, position, movement: numbers only, never pictures) is stored in
`DATABASE_PATH` for `HISTORY_DAYS`. `/report` compares the chosen period with the one before:

- **Trend:** "more active" / "less active" / "about the same" when the share of checks with movement
  changed by at least 10 points; "not enough data" until both periods have 30 minutes of camera time.
- Positions, longest time without movement, time out of view, and a 7-day chart (with a table view).
- **AI summary:** `LLM_MODEL` (OpenAI) rewrites the numbers in plain language. Only the numbers are sent.
  It is told never to say the patient is improving or getting worse: more movement can also mean
  restlessness or discomfort, so changes are flagged for the doctor or nurse to interpret. Summaries are
  reused for 15 minutes to limit API cost.

## Texts and calls (Android SMS gateway)

1. On an Android phone with a SIM, install **SMS Gateway for Android** (sms-gate.app).
2. Connect it to the same Wi-Fi as the Pi, switch on **Local Server**, tap **Start**. Note the address, username and password.
3. In `.env`: `COMMUNICATION_BACKEND=ANDROID_GATEWAY`, `ANDROID_GATEWAY_URL`, `ANDROID_GATEWAY_USERNAME`,
   `ANDROID_GATEWAY_PASSWORD`, `CAREGIVER_PHONE` (emergencies) and optionally `CONTACT_PHONES`.
4. Restart. "Tell my son I'm hungry" sends an SMS. "Call my son" texts him a call-back request and the bedside
   phone opens its dialler. "Help" alerts the caregiver by SMS and offers to dial them.

Set the gateway app's battery usage to **Unrestricted**, or Android may stop its server in the background.

## Automatic calls (MacroDroid on the bedside phone)

Android won't let a web page place a call, so MacroDroid (free) does it:

1. Install **MacroDroid** on the bedside phone and allow it **Phone** permission.
2. New macro. **Trigger:** *Connectivity → HTTP Server Request*. Path: the last part of `CALL_AUTOMATION_URL`
   (e.g. `bedside-call-6e5a…`). IP whitelist: the Pi's IP. Variable whitelist: `number`
   (create a global string variable `number` first).
3. **Actions:** *Phone → Make Call* → *[Select Number]* → `{v=number}`; then *Wait 2 seconds*;
   then *Phone → Speakerphone On/Off → On*.
4. MacroDroid settings → *HTTP Server Settings* → port **8090**. Set MacroDroid's battery usage to **Unrestricted**.
5. In `.env`: `CALL_AUTOMATION_URL=http://<phone IP>:8090/<path>` and restart.

"Call my son" then rings him on speaker with no tap. "Help" texts **and** calls the caregiver. If the phone
can't be reached, the page opens the dialler instead, so a call is never silently lost.

## Online access

`scripts/start.sh --online` restarts the server, opens a free Cloudflare quick tunnel and prints a public
`https://….trycloudflare.com` address (it changes on every start). It refuses to run without `ACCESS_PASSWORD`.
Over the tunnel the phone page has a valid certificate, so the microphone works without warnings, but live
video then uses mobile data (roughly 200 MB per hour). On the same Wi-Fi, prefer the local HTTPS address.

## Tests

| Command | Checks |
|---|---|
| `python -m pytest -q` | Unit tests: config fail-closed, HTTP client errors/timeouts/retries, mock safety behaviour, `/health` |
| `python scripts/test_esp32.py` | Read-only: health, status, telemetry, heartbeat, IMU. Does not move the arm. |
| `python scripts/test_arm.py --pose USER` | Moves the arm through a sequence and checks STOP blocks motion. Real hardware also needs `--yes`. |

## ESP32 controller API contract

Agreed with the firmware team. Responses are JSON. A command counts as successful
only when the HTTP status is 2xx **and** the body doesn't contain `"ok": false`.

| Method | Path | Body | Reply |
|---|---|---|---|
| POST | `/arm/pose` | `{"pose": "MEDICINE"}` | `{"ok": true, "pose": "MEDICINE"}` |
| POST | `/gripper/close` | | `{"ok": true, "gripper": "CLOSED"}` |
| POST | `/stop` | | `{"ok": true, "emergency_stop": true}` |
| GET | `/telemetry` | | `{"heartbeat": {...}, "imu": {...}, "arm": {...}, "gripper": "OPEN", "emergency_stop": false}` |

Not yet confirmed by the firmware team. The Pi currently assumes:

| Method | Path | Assumed reply |
|---|---|---|
| POST | `/arm/home` | `{"ok": true, "pose": "HOME"}` |
| POST | `/gripper/open` | `{"ok": true, "gripper": "OPEN"}` |
| POST | `/resume` | `{"ok": true, "emergency_stop": false}` |
| GET | `/health`, `/status` | any 2xx JSON |
| (any) | refusal | `{"ok": false, "error": "reason"}` (any HTTP status) |
| (in telemetry) | `heartbeat` | `{"available": true, "raw": 520, "bpm_estimate": 76}`; `{"available": false}` if the sensor failed |
| (in telemetry) | `imu` | `{"available": true, "ax":0.04, "ay":0.18, "az":9.74, "gx":1.1, "gy":0.2, "gz":-0.4, "movement_score":0.27}`: acceleration in **m/s²**, rotation in deg/s |
| (in telemetry) | `arm` | any object; stored as received |

Safety requirements on the firmware:

- Refuse unknown pose names with `{"ok": false}`.
- After `/stop`, refuse **all** motion (pose, home, gripper) until `/resume`.

The firmware owns the servo angles for each pose name. The Pi never sends angles or PWM values.

## Safety behaviour (phase 2)

- Pose names are checked against `config/poses.json` before any request is sent. Unknown names never reach the ESP32.
- Motion commands (`pose`, `home`, `gripper`) are sent **at most once**. A timed-out move is reported as failed and never re-sent automatically.
- `/stop`, `/resume` and all GETs are retried, since repeating them is safe.
- No HTTP response gives *"The robotic arm is currently unavailable."* An HTTP error gives *"The robotic arm could not do that right now."*
- When the controller is unreachable or replies in an unexpected format, telemetry is marked `available: false` with no values. It never guesses.
- Every hardware result carries `source: DEVICE` or `source: MOCK`.
