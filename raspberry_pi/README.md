# Assistive bedside system: Raspberry Pi 5 software

Hackathon prototype. **Not a medical device.** It does not diagnose anything, and
heartbeat readings come from a hobby sensor, so they are not clinically accurate.

The Pi is the main brain. The ESP32 controller (arm, gripper, heartbeat, MPU6050)
and the ESP32 camera are separate projects that this code talks to over HTTP.

## Status

| Phase | What | State |
|---|---|---|
| 1 | Structure, config, logging, `GET /health` | done |
| 2 | ESP32 controller client, telemetry parsing, arm actions, mock hardware | done |
| 3–12 | Assistant, camera, YOLO, Sarvam, SQLite, analysis, LLM, caregiver comms | not started |

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

Catalogs in `config/`:

- `poses.json`: the **only** arm positions the Pi may request, plus the object → pose mapping.
- `responses/en.json`: every spoken or returned phrase. Add `ml.json` / `hi.json` later with the same keys.
- `mock_hardware.json`: simulated values for mock mode. **Synthetic, not patient data.**

## Run

```bash
python run.py
curl http://localhost:8000/health
```

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
| (in telemetry) | `heartbeat` | `{"raw": 2048, "bpm": 72.5}`; `bpm` may be `null` when there is no pulse |
| (in telemetry) | `imu` | `{"ax":0,"ay":0,"az":1,"gx":0,"gy":0,"gz":0}`: acceleration in g, rotation in deg/s |
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
