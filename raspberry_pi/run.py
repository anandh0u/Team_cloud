"""Start the Raspberry Pi backend: python run.py"""
import sys

import uvicorn

from app.config import ConfigError, load_settings


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    uvicorn.run("app.main:create_app", factory=True, host=settings.api_host, port=settings.api_port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
