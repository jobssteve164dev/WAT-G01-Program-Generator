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
    assert visitor.get("/api/products").status_code == 401
    assert visitor.post("/api/products", json={"model": "SECRET"}).status_code == 401
    assert visitor.post("/api/compile", json={}).status_code == 401
    assert visitor.get("/openapi.json").status_code == 401


def test_products_keep_independent_plans_across_process_restarts(configured, monkeypatch, tmp_path):
    monkeypatch.setenv("WAT_WORKBENCH_DB", str(tmp_path / "workbench.sqlite3"))
    with configured() as client:
        assert client.post("/api/login", json=LOGIN).status_code == 204
        assert client.get("/api/products").json() == []
        product_input = {"id": "94cea44c-dd12-4cf2-a670-a0add26b03e2", "model": "WAT-650", "description": "SiC MOSFET"}
        first = client.post("/api/products", json=product_input)
        second = client.post("/api/products", json={"model": "BCD-80"})
        assert first.status_code == second.status_code == 201
        a, b = first.json()["id"], second.json()["id"]
        assert client.post("/api/products", json=product_input).json()["id"] == a
        assert client.post("/api/products", json={"model": "wat-650"}).status_code == 409
        assert client.put(f"/api/products/{b}", json={"model": "BCD-80", "manufacturer": "研发"}).json()["manufacturer"] == "研发"
        excessive = client.post(f"/api/products/{a}/plans", content=b'{"name":"overflow","tests":[{"name":"leakage","upper_limit":1e999}]}',
                                headers={"Content-Type": "application/json"})
        assert excessive.status_code == 422
        assert client.post(f"/api/products/{a}/plans", json={"name": "来料验证"}).status_code == 201
        draft_input = {"id": "8dadc1ba-c80d-488a-a6f0-97461397aff2", "name": "高温漏电"}
        created = client.post(f"/api/products/{a}/plans", json=draft_input)
        assert created.status_code == 201
        plan = created.json()
        assert client.post(f"/api/products/{a}/plans", json=draft_input).json()["id"] == plan["id"]
        assert client.post(f"/api/products/{a}/plans", json={**draft_input, "name": "其他计划"}).status_code == 409
        assert client.get(f"/api/products/{b}/plans").json() == []
        client.post(f"/api/products/{b}/plans", json={"name": "高温漏电"})
        assert len(client.get(f"/api/products/{a}/plans").json()) == 2
        endpoint = f"/api/plans/{plan['id']}"
        item = {"name": "反向漏电", "conditions": "VDS = 12 V，VGS = 0 V", "unit": "µA", "lower_limit": None, "upper_limit": 2.5}
        draft = {"name": "高温漏电 Rev A", "description": "工程片", "tests": [item], "revision": plan["revision"]}
        saved = client.put(endpoint, json=draft)
        assert saved.status_code == 200
        revision = saved.json()["revision"]
        assert client.put(endpoint, json=draft).json()["revision"] == revision
        assert client.put(endpoint, json={**draft, "description": "旧版本的其他修改"}).status_code == 409
        assert client.put(endpoint, json={**draft, "revision": revision, "tests": [{**item, "lower_limit": 5}]}).status_code == 422
        assert client.put(endpoint, json={**draft, "revision": revision, "archived": True}).status_code == 200
        assert [plan["name"] for plan in client.get(f"/api/products/{a}/plans").json()] == ["来料验证"]
        assert client.get(f"/api/products/{a}/plans?archived=true").json()[0]["id"] == plan["id"]

    with configured() as reopened:
        assert reopened.post("/api/login", json=LOGIN).status_code == 204
        assert {product["model"] for product in reopened.get("/api/products").json()} == {"WAT-650", "BCD-80"}
        persisted = reopened.get(endpoint).json()
        assert persisted["archived"] and persisted["tests"] == [item]
        restored = reopened.put(endpoint, json={**draft, "revision": persisted["revision"]})
        assert restored.status_code == 200 and not restored.json()["archived"]
        exported = reopened.get(f"{endpoint}/export")
        assert "attachment" in exported.headers["content-disposition"]
        assert exported.json()["product"]["model"] == "WAT-650"
        assert exported.json()["plan"]["tests"] == [item]
        assert exported.json()["deployable_to_tester"] is False


def test_plan_pin_mappings_follow_measurements_into_copies_and_exports(configured, monkeypatch, tmp_path):
    monkeypatch.setenv("WAT_WORKBENCH_DB", str(tmp_path / "workbench.sqlite3"))
    drain = "d2af94cf-207f-4bb2-b02e-93cf52c19ed9"
    source = "054eaca0-9406-435e-bd81-ff9138589588"
    pin_setup = {"probe_card": "PC-A", "tester": "4082F 工程机", "mappings": [
        {"id": drain, "terminal": "D", "wafer_pad": "PAD-D", "probe_pin": "A1", "tester_pin": "12", "tester_port": "SMU2"},
        {"id": source, "terminal": "S", "wafer_pad": "PAD-S", "probe_pin": "A2", "tester_pin": "13", "tester_port": "GNDU"},
    ]}
    item = {"name": "漏电", "pin_ids": [drain, source]}
    with configured() as client:
        assert client.post("/api/login", json=LOGIN).status_code == 204
        product = client.post("/api/products", json={"model": "P-400"}).json()
        plans = f"/api/products/{product['id']}/plans"
        old = client.post(plans, json={"name": "原计划", "tests": [{"name": "漏电"}]}).json()
        endpoint = f"/api/plans/{old['id']}"
        payload = {"name": "带针脚的漏电", "tests": [item], "pin_setup": pin_setup, "revision": old["revision"]}
        response = client.put(endpoint, json=payload)
        assert response.status_code == 200
        current = response.json()
        assert current["pin_setup"] == pin_setup and current["tests"][0]["pin_ids"] == item["pin_ids"]
        assert client.put(endpoint, json=payload).json()["revision"] == current["revision"]
        copied = {**payload, "name": "副本"}
        copied.pop("revision")
        duplicate = client.post(plans, json=copied).json()
        assert duplicate["id"] != current["id"] and duplicate["pin_setup"] == pin_setup
        assert client.post(plans, json={**copied, "pin_setup": {**pin_setup, "mappings": pin_setup["mappings"][:1]}}).status_code == 422
        assert client.post(plans, json={**copied, "pin_setup": {**pin_setup, "mappings": [pin_setup["mappings"][0], {**pin_setup["mappings"][1], "terminal": "d"}]}}).status_code == 422
        assert client.put(endpoint, json={"name": "旧客户端", "revision": current["revision"], "tests": [{"name": "漏电"}]}).status_code == 409

    with configured() as client:
        assert client.post("/api/login", json=LOGIN).status_code == 204
        exported = client.get(f"{endpoint}/export").json()
        assert exported["plan"]["pin_setup"] == pin_setup
        assert exported["plan"]["tests"][0]["pin_ids"] == [drain, source]
        assert exported["deployable_to_tester"] is False


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
