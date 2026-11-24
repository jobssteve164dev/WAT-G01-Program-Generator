from copy import deepcopy
import io
import json
import os
import subprocess
from uuid import uuid4
from zipfile import ZipFile

from test_api import LOGIN
from test_api import configured  # noqa: F401


def hardware_fixture(client):
    resource = str(uuid4())
    source = str(uuid4())
    machine_data = {
        "name": "工程机 A", "model": "4082F", "system_version": "工程版本",
        "specs_version": "D.03.10", "test_head": "Head A", "pin_board": "Pin Board A",
        "source": "工程配置记录",
        "modules": [
            {"slot": "1", "model": "HRSMU", "channels": [
                {"id": resource, "channel": "1", "port": "SMU3", "path": "DC", "pins": [7, 8],
                 "modes": ["voltage_current", "voltage"], "max_voltage_v": 20, "max_current_a": 0.05,
                 "min_current_range_a": 1e-9, "kelvin": True},
            ]},
            {"slot": "2", "model": "GNDU", "channels": [
                {"id": source, "channel": "1", "port": "GNDU", "path": "DC", "pins": [8, 9],
                 "modes": ["reference"]},
            ]},
        ],
    }
    response = client.post("/api/hardware/machines", json=machine_data)
    assert response.status_code == 201, response.text
    machine = response.json()
    needles = [{"id": str(uuid4()), "site": "1", "needle": f"P{pin}", "pin": pin, "specs_pad": 100 + pin,
                "conductors": ["force", "sense"]} for pin in (7, 8, 9)]
    card_data = {"name": "Card A", "version": "B", "source": "接线表",
                 "machines": [{"id": machine["id"], "revision": 1}], "needles": needles}
    response = client.post("/api/hardware/cards", json=card_data)
    assert response.status_code == 201, response.text
    card = response.json()
    product = client.post("/api/products", json={"model": "WAT-fixture"}).json()
    plan = client.post(f"/api/products/{product['id']}/plans", json={
        "name": "低压漏电", "tests": [{"name": "IDSS", "unit": "A", "upper_limit": 5e-6, "conditions": "低压工程条件"}],
    }).json()
    structure = str(uuid4())
    target = {
        "plan_revision": plan["revision"],
        "machine": {"id": machine["id"], "revision": 1}, "card": {"id": card["id"], "revision": 1},
        "structures": [{"id": structure, "name": "MOS A", "site": "1", "terminals": [
            {"terminal": terminal, "pad": f"PAD-{terminal}", "needle_id": needle["id"]}
            for terminal, needle in zip(("D", "S", "B"), needles, strict=True)
        ]}],
        "measurements": [{"test_index": 0, "structure_id": structure, "steps": [
            {"phase": "测量", "terminal": "D", "mode": "voltage_current", "voltage_v": 10,
             "current_a": 0.001, "current_range_a": 1e-6, "resource_id": resource},
            {"phase": "测量", "terminal": "S", "mode": "reference", "resource_id": source},
            {"phase": "测量", "terminal": "B", "mode": "reference", "resource_id": source},
            {"phase": "等待", "terminal": "S", "mode": "voltage", "voltage_v": 2,
             "current_a": 0.001, "resource_id": resource},
        ]}],
    }
    return plan, target, machine, machine_data


def test_plan_resolves_real_pins_and_phase_routes_and_retains_snapshot(configured, monkeypatch, tmp_path):
    monkeypatch.setenv("WAT_WORKBENCH_DB", str(tmp_path / "workbench.sqlite3"))
    with configured() as client:
        assert client.get("/api/hardware/machines").status_code == 401
        assert client.post("/api/login", json=LOGIN).status_code == 204
        plan, target, machine, machine_data = hardware_fixture(client)
        endpoint = f"/api/plans/{plan['id']}/hardware"
        response = client.post(endpoint, json=target)
        assert response.status_code == 201, response.text
        binding = response.json()
        assert binding["review"]["issues"] == []
        routes = binding["review"]["routes"]
        assert [(row["phase"], row["terminal"], row["pin"], row["port"]) for row in routes] == [
            ("测量", "D", 7, "SMU3"), ("测量", "S", 8, "GNDU"), ("测量", "B", 9, "GNDU"), ("等待", "S", 8, "SMU3"),
        ]
        export = client.get(f"{endpoint}/{binding['id']}/export")
        assert export.status_code == 200
        assert export.json()["deployable_to_tester"] is False
        assert export.json()["hardware"]["review"]["routes"] == routes
        assert client.post(endpoint, json=target).json()["id"] == binding["id"]

        changed_machine = {**machine_data, "revision": 1, "system_version": "换版"}
        assert client.put(f"/api/hardware/machines/{machine['id']}", json={**machine_data, "revision": 1}).json()["revision"] == 1
        assert client.get(f"{endpoint}/{binding['id']}/export").status_code == 200
        assert client.put(f"/api/hardware/machines/{machine['id']}", json=changed_machine).json()["revision"] == 2
        assert client.put(f"/api/hardware/machines/{machine['id']}", json=changed_machine).json()["revision"] == 2
        assert client.put(f"/api/hardware/machines/{machine['id']}", json={**changed_machine, "name": "另一改动"}).status_code == 409
        assert client.get(f"{endpoint}/{binding['id']}/export").status_code == 409

    with configured() as client:
        assert client.post("/api/login", json=LOGIN).status_code == 204
        history = client.get(endpoint).json()
        assert history[0]["machine"]["revision"] == 1
        assert history[0]["review"]["routes"] == routes
        assert history[0]["stale"]


def test_binding_refuses_unreachable_and_conflicting_routes_and_stale_plan(configured, monkeypatch, tmp_path):
    monkeypatch.setenv("WAT_WORKBENCH_DB", str(tmp_path / "workbench.sqlite3"))
    with configured() as client:
        assert client.post("/api/login", json=LOGIN).status_code == 204
        plan, target, _, _ = hardware_fixture(client)
        endpoint = f"/api/plans/{plan['id']}/hardware"
        draft = deepcopy(target)
        steps = draft["measurements"][0]["steps"]
        steps[0]["voltage_v"] = 25
        steps[0]["current_range_a"] = 1e-10
        steps[3]["phase"] = "测量"
        result = client.post(f"{endpoint}/review", json=draft).json()
        assert {issue["code"] for issue in result["issues"]} >= {"CAPABILITY", "RESOURCE_CONFLICT", "PIN_CONFLICT"}
        steps[0]["voltage_v"] = 10
        steps[0]["current_range_a"] = 1e-6
        steps[0]["terminal"] = "B"
        result = client.post(f"{endpoint}/review", json=draft).json()
        assert "UNREACHABLE" in {issue["code"] for issue in result["issues"]}
        original = client.post(endpoint, json=target).json()
        assert client.put(f"/api/plans/{plan['id']}", json={
            "revision": plan["revision"], "name": plan["name"], "tests": [],
        }).status_code == 200
        assert client.post(endpoint, json=target).status_code == 409
        assert client.get(f"{endpoint}/{original['id']}/export").status_code == 409


def test_machine_profile_rejects_boolean_as_physical_pin(configured, monkeypatch, tmp_path):
    monkeypatch.setenv("WAT_WORKBENCH_DB", str(tmp_path / "workbench.sqlite3"))
    with configured() as client:
        assert client.post("/api/login", json=LOGIN).status_code == 204
        _, _, machine, original = hardware_fixture(client)
        original["modules"][0]["channels"][0]["pins"] = [True]
        invalid = client.put(f"/api/hardware/machines/{machine['id']}", json={**original, "revision": 1})
        assert invalid.status_code == 422
        assert client.get("/api/hardware/machines").json()[0]["revision"] == 1


def test_replacing_a_binding_keeps_only_latest_usable_and_enforces_plan_terminals(configured, monkeypatch, tmp_path):
    monkeypatch.setenv("WAT_WORKBENCH_DB", str(tmp_path / "workbench.sqlite3"))
    with configured() as client:
        assert client.post("/api/login", json=LOGIN).status_code == 204
        plan, target, _, _ = hardware_fixture(client)
        pin_id = str(uuid4())
        plan_url = f"/api/plans/{plan['id']}"
        plan = client.put(plan_url, json={
            "name": plan["name"], "revision": plan["revision"], "pin_setup": {"mappings": [{"id": pin_id, "terminal": "G"}]},
            "tests": [{**plan["tests"][0], "pin_ids": [pin_id]}],
        }).json()
        target["plan_revision"] = plan["revision"]
        review = client.post(f"{plan_url}/hardware/review", json=target).json()
        assert "PLAN_TERMINAL" in {issue["code"] for issue in review["issues"]}
        plan = client.put(plan_url, json={
            "name": plan["name"], "revision": plan["revision"], "pin_setup": {},
            "tests": [{**plan["tests"][0], "pin_ids": []}],
        }).json()
        target["plan_revision"] = plan["revision"]
        endpoint = f"{plan_url}/hardware"
        old = client.post(endpoint, json=target).json()
        edited = deepcopy(target)
        edited["measurements"][0]["steps"][0]["voltage_v"] = 11
        assert client.post(endpoint, json=edited).status_code == 409
        edited["previous_id"] = old["id"]
        latest = client.post(endpoint, json=edited).json()
        assert latest["id"] != old["id"]
        assert [row["id"] for row in client.get(endpoint).json()] == [latest["id"]]
        assert client.get(f"{endpoint}/{old['id']}/export").status_code == 409
        assert client.post(endpoint, json=edited).json()["id"] == latest["id"]


def test_specs31_build_package_uses_native_tools_and_keeps_pad_and_pin_distinct(configured, monkeypatch, tmp_path):
    monkeypatch.setenv("WAT_WORKBENCH_DB", str(tmp_path / "workbench.sqlite3"))
    with configured() as client:
        assert client.post("/api/login", json=LOGIN).status_code == 204
        plan, target, _, _ = hardware_fixture(client)
        endpoint = f"/api/plans/{plan['id']}/hardware"
        binding = client.post(endpoint, json=target).json()
        target_url = f"{endpoint}/{binding['id']}/specs31-build"
        request = {"test_plan": "cell/leak.tsf", "recipe_file": "cell/engineering",
                   "recipe_key": "LEAK_01", "limit_file": "leak.lim", "libraries": ["cell/DC.lib"]}
        assert client.post(target_url, json={**request, "test_plan": "../leak.tpl"}).status_code == 422
        response = client.post(target_url, json=request)
        assert response.status_code == 200, response.text
        with ZipFile(io.BytesIO(response.content)) as archive:
            details = json.loads(archive.read("build-input.json"))
            assert details["tested_on_tester"] is False
            assert details["probe_spec"][0] == {"pad_number": 107, "pin_number": 7}
            assert details["routes"][0]["terminal"] == "D"
            assert details["routes"][0]["pad_number"] == 107
            build_script = archive.read("build-recipe.sh")

        specs_root = tmp_path / "specs"
        (specs_root / "tpl" / "cell").mkdir(parents=True)
        (specs_root / "tpl" / "cell" / "leak.tsf").write_text("vendor-exported-test-double")
        (specs_root / "usr" / "lim").mkdir(parents=True)
        (specs_root / "usr" / "lim" / "leak.lim").touch()
        bin_dir = tmp_path / "tools"
        bin_dir.mkdir()
        build = tmp_path / "build-recipe.sh"
        build.write_bytes(build_script)
        converter = bin_dir / "transtpl"
        converter.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > tsf-args\ncp "$1" "$2"\n')
        converter.chmod(0o700)
        builder = bin_dir / "create_recipe"
        builder.write_text('#!/bin/sh\nprintf "partial" > "$5"\nexit 42\n')
        builder.chmod(0o700)
        env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "WAT_SPECS_ROOT": str(specs_root)}
        assert subprocess.run(["/bin/sh", str(build)], env=env, check=False).returncode == 42
        assert not (specs_root / "tpl" / "cell" / "leak.tpl").exists()
        assert not (specs_root / "tpl" / "cell" / "engineering").exists()
        builder.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > recipe-args\nprintf "tool output" > "$5"\n')
        assert subprocess.run(["/bin/sh", str(build)], env=env, check=False).returncode == 0
        assert (specs_root / "tpl" / "tsf-args").read_text().splitlines() == ["cell/leak.tsf", "cell/leak.tpl"]
        assert (specs_root / "tpl" / "recipe-args").read_text().splitlines() == [
            "-l", "leak.lim", "-m", "cell/DC.lib", "cell/engineering", "LEAK_01", "cell/leak.tpl",
        ]
        assert (specs_root / "tpl" / "cell" / "engineering").read_text() == "tool output"
        assert subprocess.run(["/bin/sh", str(build)], env=env, check=False).returncode != 0
