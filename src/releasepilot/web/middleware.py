"""Pure ASGI middleware for ReleasePilot web layer.

Uses raw ASGI (not BaseHTTPMiddleware) to avoid buffering issues with SSE streams.
"""

from __future__ import annotations

import os
import secrets
import time
from typing import Any

from releasepilot.shared.logging import get_logger

logger = get_logger("web.middleware")

Scope = dict[str, Any]
Receive = Any
Send = Any


class SecurityHeadersMiddleware:
    """Inject security headers on every HTTP response."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self._allow_framing = os.environ.get("RELEASEPILOT_ALLOW_FRAMING", "").lower() in (
            "1",
            "true",
            "yes",
        )
        # Portal origin(s) allowed to embed this app in an iframe.
        self._frame_ancestors = self._build_frame_ancestors()

    def _build_frame_ancestors(self) -> str:
        """Build frame-ancestors value from environment."""
        if not self._allow_framing:
            return "'none'"
        origins_env = os.environ.get("RELEASEPILOT_CORS_ORIGINS", "").strip()
        if origins_env:
            origins = " ".join(o.strip() for o in origins_env.split(",") if o.strip())
            return f"'self' {origins}"
        return "'self'"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Generate a per-request nonce for CSP
        nonce = secrets.token_urlsafe(16)
        # Expose nonce to request handlers via scope state
        scope.setdefault("state", {})["csp_nonce"] = nonce

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-content-type-options", b"nosniff"))
                if not self._allow_framing:
                    headers.append((b"x-frame-options", b"DENY"))
                # nonce-based CSP instead of unsafe-inline
                csp = (
                    f"default-src 'self'; "
                    f"img-src 'self' data:; "
                    f"style-src 'self' 'nonce-{nonce}'; "
                    f"style-src-attr 'unsafe-inline'; "
                    f"script-src 'self' 'nonce-{nonce}'; "
                    f"script-src-attr 'unsafe-inline'; "
                    f"frame-ancestors {self._frame_ancestors}"
                )
                headers.append((b"content-security-policy", csp.encode()))
                headers.append((b"referrer-policy", b"strict-origin-when-cross-origin"))
                # HSTS header
                headers.append(
                    (b"strict-transport-security", b"max-age=63072000; includeSubDomains")
                )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class RequestLoggingMiddleware:
    """Log each HTTP request with method, path, status, and duration."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.monotonic()
        path = scope.get("path", "/")
        method = scope.get("method", "?")
        status_code = 0

        async def send_with_logging(message: dict) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
            await send(message)

        try:
            await self.app(scope, receive, send_with_logging)
        finally:
            duration_ms = round((time.monotonic() - start) * 1000)
            logger.info(
                "%s %s %s %dms",
                method,
                path,
                status_code,
                duration_ms,
                extra={"request_path": path, "duration_ms": duration_ms},
            )
