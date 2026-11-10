import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .compiler import CompileValidationError, compile_program
from .example import build_example_request
from .models import CompileRequest, CompileResult


def _default_frontend_dist() -> Path:
    configured = os.getenv("WAT_FRONTEND_DIST")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


def create_app(frontend_dist: Path | None = None) -> FastAPI:
    application = FastAPI(title="WAT G-01 Pilot", version="0.1.0")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @application.middleware("http")
    async def limit_compile_request(request: Request, call_next):
        if request.method == "POST" and request.url.path == "/api/compile":
            content_length = request.headers.get("content-length")
            try:
                declared_length = int(content_length) if content_length else None
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"error": "INVALID_CONTENT_LENGTH", "message": "Content-Length 必须是整数。"},
                )
            if declared_length is not None and declared_length > 131_072:
                return JSONResponse(
                    status_code=413,
                    content={"error": "REQUEST_TOO_LARGE", "message": "请求体不能超过 128 KiB。"},
                )
            body = await request.body()
            if len(body) > 131_072:
                return JSONResponse(
                    status_code=413,
                    content={"error": "REQUEST_TOO_LARGE", "message": "请求体不能超过 128 KiB。"},
                )
        return await call_next(request)

    @application.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "replay"}

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
