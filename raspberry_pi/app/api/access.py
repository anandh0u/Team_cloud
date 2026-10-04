"""Optional password protection for every page and API call (ACCESS_PASSWORD).

Uses HTTP Basic auth, so the browser shows its own login box and then sends the
password with every request from the page. Any user name is accepted. Needed as
soon as the server is reachable from the internet: the pages show the patient's
camera and can move the arm and send messages.
"""
from __future__ import annotations

import base64
import binascii
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

REALM = "Bedside assistant"


def _password_from(header: str | None) -> str | None:
    if not header or not header.lower().startswith("basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    _, sep, password = decoded.partition(":")
    return password if sep else None


class PasswordMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, password: str):
        super().__init__(app)
        self._password = password.encode("utf-8")

    async def dispatch(self, request: Request, call_next) -> Response:
        given = _password_from(request.headers.get("authorization"))
        if given is None or not secrets.compare_digest(given.encode("utf-8"), self._password):
            return PlainTextResponse("Password required", status_code=401,
                                     headers={"WWW-Authenticate": f'Basic realm="{REALM}", charset="UTF-8"'})
        return await call_next(request)
