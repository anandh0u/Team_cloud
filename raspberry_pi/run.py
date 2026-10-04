"""Start the Raspberry Pi backend: python run.py"""
import asyncio
import sys

import uvicorn

from app.config import ConfigError, Settings, load_settings


async def serve(settings: Settings) -> None:
    from app.main import create_app

    app = create_app(settings)
    http = uvicorn.Server(uvicorn.Config(app, host=settings.api_host, port=settings.api_port))
    if settings.https_port is None:
        await http.serve()
        return

    # Same app object, so YOLO and the ESP32 client are loaded once. Only the HTTP
    # server runs startup/shutdown; HTTPS opens after startup finished and shares its state.
    https = uvicorn.Server(uvicorn.Config(
        app, host=settings.api_host, port=settings.https_port, lifespan="off",
        ssl_certfile=str(settings.tls_cert_file), ssl_keyfile=str(settings.tls_key_file)))
    http_task = asyncio.create_task(http.serve())
    while not http.started and not http_task.done():
        await asyncio.sleep(0.1)
    if http_task.done():  # startup failed: nothing to serve over HTTPS
        await http_task
        return
    https_task = asyncio.create_task(https.serve())
    done, _ = await asyncio.wait({http_task, https_task}, return_when=asyncio.FIRST_COMPLETED)
    # One stopped (Ctrl+C, or port in use): stop the other too.
    http.should_exit = https.should_exit = True
    await asyncio.gather(http_task, https_task)


def main() -> int:
    try:
        settings = load_settings()
        asyncio.run(serve(settings))
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
