import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .auth import install_auth
from .compiler import CompileValidationError, compile_program
from .example import build_example_request
from .models import CompileRequest, CompileResult
from .workbench import WorkbenchStore, workbench_routes


def _default_frontend_dist() -> Path:
    configured = os.getenv("WAT_FRONTEND_DIST")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


def create_app(frontend_dist: Path | None = None) -> FastAPI:
    store = WorkbenchStore(Path(os.getenv("WAT_WORKBENCH_DB") or ".wat-data/workbench.sqlite3"))

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        store.initialize()
        yield

    application = FastAPI(title="WAT G-01 Pilot", version="0.1.0", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Content-Type"],
    )

    @application.middleware("http")
    async def limit_workbench_request(request: Request, call_next):
        path = request.url.path
        limit_kib = 0
        if request.method == "POST" and path == "/api/compile":
            limit_kib = 128
        elif (request.method == "POST" and path.startswith("/api/products/") and path.endswith("/plans")) or (request.method == "PUT" and path.startswith("/api/plans/")):
            limit_kib = 640
        elif request.method in {"POST", "PUT"} and (path == "/api/products" or path.startswith("/api/products/")):
            limit_kib = 256
        if limit_kib:
            byte_limit = limit_kib * 1024
            content_length = request.headers.get("content-length")
            try:
                declared_length = int(content_length) if content_length else None
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"error": "INVALID_CONTENT_LENGTH", "message": "Content-Length 必须是整数。"},
                )
            if (declared_length is not None and declared_length > byte_limit) or len(await request.body()) > byte_limit:
                return JSONResponse(
                    status_code=413,
                    content={"error": "REQUEST_TOO_LARGE", "message": f"请求体不能超过 {limit_kib} KiB。"},
                )
        return await call_next(request)

    auth_ready = install_auth(application)

    @application.get("/api/health")
    def health():
        if not auth_ready:
            return JSONResponse({"status": "unavailable", "mode": "replay"}, status_code=503, headers={"Cache-Control": "no-store"})
        return JSONResponse({"status": "ok", "mode": "replay"}, headers={"Cache-Control": "no-store"})

    @application.get("/api/example", response_model=CompileRequest)
    def example() -> CompileRequest:
        return build_example_request()

    @application.post("/api/compile", response_model=CompileResult)
    def compile_endpoint(request: CompileRequest) -> CompileResult | JSONResponse:
        try:
            return compile_program(request)
        except CompileValidationError as error:
            return JSONResponse(
                status_code=422,
                content={
                    "error": "PROGRAM_VALIDATION_FAILED",
                    "findings": [item.model_dump(mode="json") for item in error.findings],
                },
            )

    application.include_router(workbench_routes(store))
    static_root = (frontend_dist or _default_frontend_dist()).resolve()

    @application.get("/{full_path:path}", include_in_schema=False)
    def frontend(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API endpoint not found")

        candidate = (static_root / full_path).resolve()
        if candidate.is_relative_to(static_root) and candidate.is_file():
            return FileResponse(candidate)

        index = static_root / "index.html"
        if index.is_file():
            return FileResponse(index)
        raise HTTPException(status_code=404, detail="Frontend build not found")

    return application


app = create_app()
