from test_api import LOGIN, configured  # noqa: F401


def test_specification_survives_reopen_and_copy_without_a_tester(configured, monkeypatch, tmp_path):
    monkeypatch.setenv("WAT_WORKBENCH_DB", str(tmp_path / "db"))
    with configured() as client:
        client.post("/api/login", json=LOGIN)
        product = client.post("/api/products", json={"model": "engineering"}).json()
        item = {"name": "关断漏电流", "unit": "µA", "upper_limit": 12.5,
                "off_leakage": {"structure": "MOS A", "vds_v": 5, "vgs_v": 0, "temperature_c": 25}}
        path = f"/api/products/{product['id']}/plans"
        for field in ("vds_v", "vgs_v", "temperature_c"):
            bad = {**item, "off_leakage": {**item["off_leakage"], field: True}}
            assert client.post(path, json={"name": "错误", "tests": [bad]}).status_code == 422
        assert client.post(path, json={"name": "错误", "tests": [{**item, "upper_limit": True}]}).status_code == 422
        response = client.post(path, json={"name": "工程测量", "tests": [item]})
        assert response.status_code == 201, response.text
        plan = response.json()
        endpoint = f"/api/plans/{plan['id']}"
        assert client.get(endpoint).json()["tests"][0]["off_leakage"] == item["off_leakage"]
        assert client.get(endpoint + "/recipe-targets").json()["targets"] == []
        assert client.get(endpoint + "/export").json()["plan"]["tests"][0]["upper_limit"] == 12.5


def test_download_uses_only_qualified_native_files_and_current_hardware(configured, monkeypatch, tmp_path):
    import hashlib
    import io
    import json
    from zipfile import ZipFile
    from test_hardware import hardware_fixture

    monkeypatch.setenv("WAT_WORKBENCH_DB", str(tmp_path / "db"))
    with configured() as client:
        client.post("/api/login", json=LOGIN)
        reference, target, machine, machine_input = hardware_fixture(client)
        ref_id = reference["id"]
        binding = client.post(f"/api/plans/{ref_id}/hardware", json=target).json()
        product = reference["product_id"]
        method_dir = tmp_path / "recipe-methods" / product / "lab"
        method_dir.mkdir(parents=True)
        original = {"recipe": b"opaque\x00fixture", "bias": b"vd\t5\r\n", "limits": b"id\t1e-6\n"}
        for name, data in original.items():
            (method_dir / name).write_bytes(data)
        def cell(name, literal, scale):
            return {"file": name, "line": 1, "column": 2, "original": literal, "scale": scale}
        method = {"plan": ref_id, "binding": binding["id"], "entry": "recipe", "qualification": "offline parser fixture only",
                  "files": {name: hashlib.sha256(data).hexdigest() for name, data in original.items()},
                  "rows": [{"structure": "MOS A", "parameters": {
                      "vgs_v": {"fixed": 0}, "temperature_c": {"fixed": 25},
                      "vds_v": {"min": 1, "max": 10, "cells": [cell("bias", "5", 1)]},
                      "upper_ua": {"min": 0.5, "max": 15, "cells": [cell("limits", "1e-6", 1e-6)]}}}]}
        (method_dir / "method.json").write_text(json.dumps(method))
        item = {"name": "关断漏电流", "unit": "µA", "upper_limit": 12.5,
                "off_leakage": {"structure": "MOS A", "vds_v": 7, "vgs_v": 0, "temperature_c": 25}}
        saved = client.post(f"/api/products/{product}/plans", json={"name": "验证", "tests": [item]})
        assert saved.status_code == 201, saved.text
        plan = saved.json()
        endpoint = f"/api/plans/{plan['id']}"
        # Same hardware, two qualified profiles; only the one covering this bias is eligible.
        import shutil
        other = method_dir.parent / "colder"
        shutil.copytree(method_dir, other)
        import copy
        cold = copy.deepcopy(method)
        cold["rows"][0]["parameters"]["temperature_c"] = {"fixed": -10}
        (other / "method.json").write_text(json.dumps(cold))
        options = client.get(endpoint + "/recipe-targets").json()
        assert [entry["id"] for entry in options["targets"]] == ["lab"]
        assert options["targets"][0]["machine"] == machine["name"]
        url = endpoint + "/recipe/lab?revision=1"
        response = client.get(url)
        assert response.status_code == 200, response.text if response.status_code != 200 else ""
        with ZipFile(io.BytesIO(response.content)) as archive:
            assert set(archive.namelist()) == set(original)
            assert archive.read("recipe") == original["recipe"]
            assert archive.read("bias") == b"vd\t7\r\n"
            assert float(archive.read("limits").strip().split(b"\t")[1]) == 0.0000125
        item["upper_limit"] = 20
        assert client.put(endpoint, json={"name": "验证", "tests": [item], "revision": 1}).status_code == 200
        no_target = client.get(endpoint + "/recipe-targets").json()
        assert no_target["targets"] == []
        assert "漏电流上限" in no_target["reason"]
        assert client.get(url).status_code == 409
        assert client.get(endpoint + "/recipe/lab?revision=2").status_code == 422
        item["upper_limit"] = 4
        item["off_leakage"]["vds_v"] = 2
        client.put(endpoint, json={"name": "验证", "tests": [item], "revision": 2})
        with ZipFile(io.BytesIO(client.get(endpoint + "/recipe/lab?revision=3").content)) as archive:
            assert float(archive.read("limits").strip().split(b"\t")[1]) == 4e-6
            assert archive.read("bias") == b"vd\t2\r\n"
        (method_dir / "bias").write_bytes(b"tampered")
        assert client.get(endpoint + "/recipe/lab?revision=3").status_code == 409
        client.put(f"/api/hardware/machines/{machine['id']}", json={**machine_input, "revision": 1, "system_version": "changed"})
        assert client.get(endpoint + "/recipe-targets").json()["targets"] == []
