from fastapi.testclient import TestClient

from wat_pilot.api import app, create_app


client = TestClient(app)


def test_health_and_example_are_ready_for_local_operator():
    health = client.get("/api/health")
    example = client.get("/api/example")

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "mode": "replay"}
    assert example.status_code == 200
    assert example.json()["product"]["model"] == "BUK7M6R0-40H"


def test_compile_endpoint_returns_package_and_explicit_tester_lock():
    request = client.get("/api/example").json()

    response = client.post("/api/compile", json=request)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "REPLAY_READY"
    assert body["deployable_to_tester"] is False
    assert body["package_base64"]


def test_compile_endpoint_returns_actionable_findings_for_unsafe_request():
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


def test_single_service_serves_built_frontend_and_spa_routes(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<h1>WAT UI</h1>", encoding="utf-8")
    (tmp_path / "assets" / "app.js").write_text("console.log('ready')", encoding="utf-8")
    static_client = TestClient(create_app(tmp_path))

    assert static_client.get("/").text == "<h1>WAT UI</h1>"
    assert static_client.get("/assets/app.js").text == "console.log('ready')"
    assert static_client.get("/review/program").text == "<h1>WAT UI</h1>"
    assert static_client.get("/api/not-found").status_code == 404


def test_compile_request_body_is_bounded():
    response = client.post(
        "/api/compile",
        content=b"{" + (b"x" * 131_073) + b"}",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["error"] == "REQUEST_TOO_LARGE"


def test_invalid_content_length_is_rejected_without_server_error():
    response = client.post(
        "/api/compile",
        content=b"{}",
        headers={"Content-Type": "application/json", "Content-Length": "invalid"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "INVALID_CONTENT_LENGTH"
