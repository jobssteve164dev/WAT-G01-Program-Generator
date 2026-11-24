import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool


SESSION_COOKIE = "__Host-wat_g0_session"
SESSION_SECONDS = 8 * 60 * 60
SESSION_CAPACITY = 64
LOGIN_MAX_BYTES = 4096
PASSWORD_ITERATIONS = 600_000


def valid_origin(origin: str) -> bool:
    try:
        parsed = urlsplit(origin)
        port = parsed.port
        return bool(
            parsed.hostname
            and (port is None or 0 < port < 65536)
            and origin == f"{parsed.scheme}://{parsed.netloc}"
            and not parsed.username
            and not parsed.password
            and not any(character.isspace() for character in origin)
            and (parsed.scheme == "https" or (
                parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            ))
        )
    except ValueError:
        return False


def install_auth(app: FastAPI) -> bool:
    username = os.getenv("WAT_G0_ADMIN_USERNAME", "")
    verifier = os.getenv("WAT_G0_ADMIN_HASH", "")
    origin = os.getenv("WAT_G0_ORIGIN", "")
    configured = bool(
        re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._@-]{0,79}", username)
        and re.fullmatch(r"[a-f0-9]{32}:[a-f0-9]{64}", verifier)
        and valid_origin(origin)
    )
    salt, password_hash = (bytes.fromhex(part) for part in verifier.split(":")) if configured else (b"", b"")
    sessions: dict[bytes, float] = {}
    login_lock = asyncio.Lock()

    def session_key(request: Request) -> bytes:
        token = request.cookies.get(SESSION_COOKIE, "")
        return hashlib.sha256(token.encode()).digest() if re.fullmatch(r"[A-Za-z0-9_-]{43}", token) else b""

    def matches_credentials(name: str, password: str) -> bool:
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
        names_match = hmac.compare_digest(name.encode(), username.encode())
        return hmac.compare_digest(candidate, password_hash) and names_match

    def error(status: int, code: str) -> JSONResponse:
        return JSONResponse({"error": code}, status_code=status, headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def protect_workbench(request: Request, call_next):
        path = request.url.path
        protected = path == "/api" or path.startswith(("/api/", "/docs/", "/redoc/")) or path in {
            "/docs", "/redoc", "/openapi.json",
        }
        if not protected or (request.method == "GET" and path == "/api/health"):
            return await call_next(request)

        login = request.method == "POST" and path == "/api/login"
        if login and not configured:
            return error(503, "LOGIN_UNAVAILABLE")
        if not login and sessions.get(session_key(request), 0) <= time.monotonic():
            return error(401, "LOGIN_REQUIRED")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.getlist("origin") != [origin]:
            return error(403, "ORIGIN_NOT_ALLOWED")
        result = await call_next(request)
        result.headers["Cache-Control"] = "no-store"
        return result

    @app.post("/api/login", status_code=204, include_in_schema=False)
    async def login(request: Request):
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return error(400, "LOGIN_INPUT_INVALID")
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > LOGIN_MAX_BYTES:
                return error(413, "LOGIN_INPUT_TOO_LARGE")
            body.extend(chunk)
        try:
            payload = json.loads(body)
        except (UnicodeError, ValueError, RecursionError):
            return error(400, "LOGIN_INPUT_INVALID")
        if not isinstance(payload, dict):
            return error(400, "LOGIN_INPUT_INVALID")
        name, password = payload.get("username"), payload.get("password")
        if not isinstance(name, str) or not isinstance(password, str) or not 1 <= len(name) <= 80 or not 1 <= len(password) <= 256:
            return error(400, "LOGIN_INPUT_INVALID")
        try:
            name.encode()
            password.encode()
        except UnicodeError:
            return error(400, "LOGIN_INPUT_INVALID")

        if login_lock.locked():
            busy = error(429, "LOGIN_BUSY")
            busy.headers["Retry-After"] = "1"
            return busy
        async with login_lock:
            if not await run_in_threadpool(matches_credentials, name, password):
                return error(401, "INVALID_CREDENTIALS")

        sessions.pop(session_key(request), None)
        now = time.monotonic()
        for key in tuple(sessions):
            if sessions[key] <= now:
                del sessions[key]
        if len(sessions) >= SESSION_CAPACITY:
            del sessions[next(iter(sessions))]
        token = secrets.token_urlsafe(32)
        sessions[hashlib.sha256(token.encode()).digest()] = now + SESSION_SECONDS
        result = Response(status_code=204)
        result.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, secure=True, httponly=True, samesite="strict")
        return result

    @app.post("/api/logout", status_code=204, include_in_schema=False)
    async def logout(request: Request):
        sessions.pop(session_key(request), None)
        result = Response(status_code=204)
        result.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="strict")
        return result

    return configured
