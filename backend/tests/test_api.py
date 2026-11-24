import hashlib

import pytest
from fastapi.testclient import TestClient

from wat_pilot.api import create_app


ORIGIN = "https://g0.example.test"
PASSWORD = "fixture-password-for-g0"
SALT = bytes.fromhex("a0" * 16)
LOGIN = {"username": "operator", "password": PASSWORD}


@pytest.fixture
def configured(monkeypatch):
    digest = hashlib.pbkdf2_hmac("sha256", PASSWORD.encode(), SALT, 600_000).hex()
    monkeypatch.setenv("WAT_G0_ADMIN_USERNAME", LOGIN["username"])
    monkeypatch.setenv("WAT_G0_ADMIN_HASH", f"{SALT.hex()}:{digest}")
    monkeypatch.setenv("WAT_G0_ORIGIN", ORIGIN)

    def make_client(frontend_dist=None):
        return TestClient(create_app(frontend_dist), base_url=ORIGIN, headers={"Origin": ORIGIN})

    return make_client


@pytest.fixture
def client(configured):
    client = configured()
    assert client.post("/api/login", json=LOGIN).status_code == 204
    return client


def test_workbench_apis_are_locked_before_login(configured):
    visitor = configured()
    assert visitor.get("/api/health").json() == {"status": "ok", "mode": "replay"}
    assert visitor.get("/api/example").status_code == 401
    assert visitor.post("/api/compile", json={}).status_code == 401
    assert visitor.get("/openapi.json").status_code == 401


def test_valid_g0_account_opens_workbench_and_logout_revokes_session(configured):
    visitor = configured()
    signed_in = visitor.post("/api/login", json=LOGIN)
    assert signed_in.status_code == 204
    cookie = signed_in.headers["set-cookie"]
    assert cookie.startswith("__Host-wat_g0_session=")
    assert all(flag in cookie.lower() for flag in ("secure", "httponly", "samesite=strict", "path=/"))
    assert "domain=" not in cookie.lower()
    example = visitor.get("/api/example")
    assert example.status_code == 200
    assert example.headers["cache-control"] == "no-store"
    assert visitor.post("/api/compile", json=example.json()).json()["status"] == "REPLAY_READY"

    session = visitor.cookies["__Host-wat_g0_session"]
    assert visitor.post("/api/logout").status_code == 204
    assert visitor.get("/api/example", headers={"Cookie": f"__Host-wat_g0_session={session}"}).status_code == 401


def test_login_rejects_wrong_password_without_reflecting_it(configured):
    visitor = configured()
    incorrect = "wrong-value-that-should-never-be-reflected"
    response = visitor.post("/api/login", json={**LOGIN, "password": incorrect})
    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert incorrect not in response.text
    assert visitor.get("/api/example").status_code == 401


def test_other_origins_cannot_log_in_compile_or_end_session(configured, client):
    origin = {"Origin": "https://sibling.example.test"}
    visitor = configured()
    assert visitor.post("/api/login", json=LOGIN, headers=origin).status_code == 403
    assert client.post("/api/compile", json={}, headers=origin).status_code == 403
    assert client.post("/api/logout", headers=origin).status_code == 403
    assert client.get("/api/example").status_code == 200


def test_missing_admin_configuration_keeps_api_closed(monkeypatch):
    for key in ("WAT_G0_ADMIN_USERNAME", "WAT_G0_ADMIN_HASH", "WAT_G0_ORIGIN"):
        monkeypatch.delenv(key, raising=False)
    visitor = TestClient(create_app())
    assert visitor.get("/api/health").status_code == 503
    assert visitor.get("/api/example").status_code == 401
    assert visitor.post("/api/login", json=LOGIN).status_code == 503


def test_login_rejects_oversized_json_instead_of_reflecting_input(configured):
    visitor = configured()
    body = b'{"password":"' + b"x" * 5000 + b'"}'
    response = visitor.post("/api/login", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert "set-cookie" not in response.headers
    assert len(response.content) < 300


def test_health_and_example_are_ready_for_local_operator(client):
    health = client.get("/api/health")
    example = client.get("/api/example")

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "mode": "replay"}
    assert example.status_code == 200
    assert example.json()["product"]["model"] == "BUK7M6R0-40H"


def test_compile_endpoint_returns_package_and_explicit_tester_lock(client):
    request = client.get("/api/example").json()

    response = client.post("/api/compile", json=request)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "REPLAY_READY"
    assert body["deployable_to_tester"] is False
    assert body["package_base64"]


def test_compile_endpoint_returns_actionable_findings_for_unsafe_request(client):
    request = client.get("/api/example").json()
    breakdown = next(
        item for item in request["tests"] if item["primitive_id"] == "drain_breakdown"
    )
    breakdown["parameters"]["compliance_a"] = 0

    response = client.post("/api/compile", json=request)

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "PROGRAM_VALIDATION_FAILED"
    assert any(item["code"] == "COMPLIANCE_REQUIRED" for item in body["findings"])
    assert any(
        item["message"] == "击穿扫描必须设置大于 0 的电流限值。"
        for item in body["findings"]
    )


def test_single_service_serves_built_frontend_and_spa_routes(tmp_path, configured):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<h1>WAT UI</h1>", encoding="utf-8")
    (tmp_path / "assets" / "app.js").write_text("console.log('ready')", encoding="utf-8")
    static_client = configured(tmp_path)

    assert static_client.get("/").text == "<h1>WAT UI</h1>"
    assert static_client.get("/assets/app.js").text == "console.log('ready')"
    assert static_client.get("/review/program").text == "<h1>WAT UI</h1>"
    assert static_client.post("/api/login", json=LOGIN).status_code == 204
    assert static_client.get("/api/not-found").status_code == 404


def test_compile_request_body_is_bounded(client):
    response = client.post(
        "/api/compile",
        content=b"{" + (b"x" * 131_073) + b"}",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["error"] == "REQUEST_TOO_LARGE"


def test_invalid_content_length_is_rejected_without_server_error(client):
    response = client.post(
        "/api/compile",
        content=b"{}",
        headers={"Content-Type": "application/json", "Content-Length": "invalid"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "INVALID_CONTENT_LENGTH"
