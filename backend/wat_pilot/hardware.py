import json
import sqlite3
from collections.abc import Callable
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import ConfigDict, Field, FiniteFloat, StringConstraints, model_validator

from .models import StrictModel
from .specs31 import RecipeBuildRequest, build_recipe_package


Name = Annotated[str, StringConstraints(min_length=1, max_length=80)]
Pin = Annotated[int, Field(ge=1, le=48, strict=True)] | Literal["CHUCK"]
Magnitude = Annotated[FiniteFloat, Field(gt=0, strict=True)]
Mode = Literal["voltage_current", "voltage", "current_voltage", "measure_voltage", "measure_current", "reference", "guard"]
Path = Literal["DC", "AUX", "HF", "Extended", "Chuck"]
Conductor = Literal["force", "sense", "guard"]

HARDWARE_SCHEMA = """
CREATE TABLE IF NOT EXISTS hardware_profiles (
    kind TEXT NOT NULL,
    id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (kind, id, revision)
);
CREATE TABLE IF NOT EXISTS plan_hardware (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES plans(id),
    request TEXT NOT NULL,
    review TEXT NOT NULL,
    plan_snapshot TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS plan_hardware_history ON plan_hardware (plan_id);
"""


class HardwareInput(StrictModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class ProfileRef(HardwareInput):
    id: UUID
    revision: int = Field(ge=1)


class Channel(HardwareInput):
    id: UUID
    channel: Name
    port: Name
    path: Path
    pins: list[Pin] = Field(min_length=1, max_length=49)
    modes: list[Mode] = Field(default_factory=list, max_length=7)
    max_voltage_v: Magnitude | None = None
    max_current_a: Magnitude | None = None
    min_current_range_a: Magnitude | None = None
    kelvin: bool = False
    shared_group: str = Field(default="", max_length=80)

    @model_validator(mode="after")
    def check_limits(self):
        if len(set(self.pins)) != len(self.pins) or len(set(self.modes)) != len(self.modes):
            raise ValueError("同一路径的针位和测量能力不能重复。")
        if self.min_current_range_a and self.max_current_a and self.min_current_range_a > self.max_current_a:
            raise ValueError("最小电流量程不能大于最大电流。")
        if (self.path == "Chuck") != (self.pins == ["CHUCK"]) or ("CHUCK" in self.pins and self.path != "Chuck"):
            raise ValueError("Chuck 接点必须独立登记为 Chuck 路径。")
        return self


class Module(HardwareInput):
    slot: Name
    model: Name
    channels: list[Channel] = Field(default_factory=list, max_length=24)


class MachineInput(HardwareInput):
    name: Name
    model: Name
    system_version: Name
    specs_version: str = Field(default="", max_length=80)
    specs_fa_version: str = Field(default="", max_length=80)
    test_head: Name
    pin_board: Name
    prober: str = Field(default="", max_length=80)
    chuck: str = Field(default="", max_length=80)
    source: Name
    modules: list[Module] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def check_channels(self):
        if len({item.slot.casefold() for item in self.modules}) != len(self.modules):
            raise ValueError("同一机台的模块槽位不能重复。")
        channels = [channel for module in self.modules for channel in module.channels]
        route_keys = {(channel.port.casefold(), channel.path) for channel in channels}
        if len({channel.id for channel in channels}) != len(channels) or len(route_keys) != len(channels):
            raise ValueError("同一机台的逻辑端口和路径组合不能重复。")
        return self


class MachineUpdate(MachineInput):
    revision: int = Field(ge=1)


class Needle(HardwareInput):
    id: UUID
    site: Name
    needle: Name
    pin: Pin
    specs_pad: int | None = Field(default=None, ge=1, le=2147483647, strict=True)
    conductors: list[Conductor] = Field(min_length=1, max_length=3)


class CardInput(HardwareInput):
    name: Name
    version: Name
    source: Name
    machines: list[ProfileRef] = Field(min_length=1, max_length=32)
    needles: list[Needle] = Field(default_factory=list, max_length=192)

    @model_validator(mode="after")
    def check_needles(self):
        locations = {(row.site.casefold(), row.needle.casefold()) for row in self.needles}
        if len({row.id for row in self.needles}) != len(self.needles) or len(locations) != len(self.needles):
            raise ValueError("同一 Site 的卡针名称不能重复。")
        if len({item.id for item in self.machines}) != len(self.machines):
            raise ValueError("每台机台只能选择一个安装版本。")
        pad_pins = {}
        for row in self.needles:
            if row.specs_pad is not None and pad_pins.setdefault(row.specs_pad, row.pin) != row.pin:
                raise ValueError("同一 SPECS Pad Number 只能对应一个测试头针位。")
            if len(set(row.conductors)) != len(row.conductors):
                raise ValueError("同一根卡针的有效导体不能重复。")
        return self


class CardUpdate(CardInput):
    revision: int = Field(ge=1)


class Terminal(HardwareInput):
    terminal: Name
    pad: Name
    needle_id: UUID


class Structure(HardwareInput):
    id: UUID
    name: Name
    site: Name
    terminals: list[Terminal] = Field(default_factory=list, max_length=48)

    @model_validator(mode="after")
    def check_terminals(self):
        if len({row.terminal.casefold() for row in self.terminals}) != len(self.terminals):
            raise ValueError("同一结构内的端子名称不能重复。")
        if len({row.needle_id for row in self.terminals}) != len(self.terminals):
            raise ValueError("同一结构内每根卡针只可对应一个端子。")
        return self


class Step(HardwareInput):
    phase: Name
    terminal: Name
    mode: Mode
    voltage_v: Magnitude | None = None
    current_a: Magnitude | None = None
    current_range_a: Magnitude | None = None
    kelvin: bool = False
    resource_id: UUID | None = None


class Measurement(HardwareInput):
    test_index: int = Field(ge=0, lt=64)
    structure_id: UUID
    steps: list[Step] = Field(default_factory=list, max_length=32)


class Target(HardwareInput):
    previous_id: UUID | None = None
    plan_revision: int = Field(ge=1)
    machine: ProfileRef
    card: ProfileRef
    structures: list[Structure] = Field(default_factory=list, max_length=64)
    measurements: list[Measurement] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def check_targets(self):
        if len({row.id for row in self.structures}) != len(self.structures):
            raise ValueError("测试结构不能重复。")
        if len({(row.name.casefold(), row.site.casefold()) for row in self.structures}) != len(self.structures):
            raise ValueError("同一 Site 的结构名称不能重复。")
        if len({(row.test_index, row.structure_id) for row in self.measurements}) != len(self.measurements):
            raise ValueError("同一测项在每个结构上只能配置一次。")
        return self


def _row_profile(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "revision": row["revision"], **json.loads(row["data"])}


def _profile(conn, kind, profile_id, revision=None):
    if revision is None:
        row = conn.execute(
            "SELECT * FROM hardware_profiles WHERE kind = ? AND id = ? ORDER BY revision DESC LIMIT 1",
            (kind, str(profile_id)),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM hardware_profiles WHERE kind = ? AND id = ? AND revision = ?",
            (kind, str(profile_id), revision),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "所选机台或探针卡档案版本不存在。")
    return _row_profile(row)


def _list_profiles(conn, kind):
    return [_row_profile(row) for row in conn.execute("""
        SELECT profiles.* FROM hardware_profiles AS profiles
        JOIN (SELECT id, MAX(revision) AS revision FROM hardware_profiles WHERE kind = ? GROUP BY id) AS latest
        ON latest.id = profiles.id AND latest.revision = profiles.revision
        WHERE profiles.kind = ? ORDER BY json_extract(data, '$.name'), profiles.id
    """, (kind, kind))]


def _save_profile(conn, kind, data, profile_id=None, expected=None):
    conn.execute("BEGIN IMMEDIATE")
    payload = data.model_dump(mode="json", exclude={"revision"})
    if kind == "cards":
        for machine in data.machines:
            _profile(conn, "machines", machine.id, machine.revision)
    for other in _list_profiles(conn, kind):
        if other["id"] == str(profile_id) or other["name"].casefold() != data.name.casefold():
            continue
        if profile_id is None and all(other[key] == value for key, value in payload.items()):
            return other
        raise HTTPException(409, "该设备编号已有档案。请打开已有档案修改。")
    if profile_id is None:
        profile_id = uuid4()
        revision = 1
    else:
        current = _profile(conn, kind, profile_id)
        if current["revision"] in {expected, expected + 1} and all(current[key] == value for key, value in payload.items()):
            return current
        if current["revision"] != expected:
            raise HTTPException(409, "档案已更新。请重新打开最新版本后保存。")
        revision = expected + 1
    conn.execute(
        "INSERT INTO hardware_profiles VALUES (?, ?, ?, ?)", (kind, str(profile_id), revision, json.dumps(payload, ensure_ascii=False)),
    )
    return {"id": str(profile_id), "revision": revision, **payload}


def _capability_problem(step: Step, channel: Channel, needle: Needle) -> str:
    if step.mode not in channel.modes:
        return "所选资源不支持这项测量动作。"
    if step.mode == "guard":
        return "" if "guard" in needle.conductors else "该针位未接 Guard 导体。"
    if "force" not in needle.conductors:
        return "该针位未接 Force 导体。"
    if step.kelvin and (not channel.kelvin or "sense" not in needle.conductors):
        return "四线测量需要资源与探针接线均支持 Sense。"
    if step.mode in {"reference", "guard"}:
        return ""
    if (step.voltage_v is None or channel.max_voltage_v is None or step.voltage_v > channel.max_voltage_v
            or step.current_a is None or channel.max_current_a is None or step.current_a > channel.max_current_a):
        return "请核对测项的电压和电流边界，以及该路径的对应能力。"
    if step.mode in {"voltage_current", "measure_current"}:
        if step.current_range_a is None or channel.min_current_range_a is None:
            return "请填写测流量程，并核对资源登记的最小电流量程。"
        if not channel.min_current_range_a <= step.current_range_a <= channel.max_current_a:
            return "要求的测流量程超出该路径登记的范围。"
    return ""


def _review(target: Target, plan, machine_data: dict, card_data: dict) -> dict:
    machine = MachineInput.model_validate({key: value for key, value in machine_data.items() if key not in {"id", "revision"}})
    card = CardInput.model_validate({key: value for key, value in card_data.items() if key not in {"id", "revision"}})
    resources = {channel.id: (module, channel) for module in machine.modules for channel in module.channels}
    needles = {row.id: row for row in card.needles}
    structures = {row.id: row for row in target.structures}
    issues = []
    routes = []

    def issue(code: str, message: str, test_index: int | None = None):
        issues.append({"code": code, "message": message, "test_index": test_index})

    if target.machine not in card.machines:
        issue("CARD_COMPATIBILITY", "请先在探针卡档案确认这台机台配置版本的安装关系。")
    covered = {row.test_index for row in target.measurements}
    if not plan.tests:
        issue("NO_TESTS", "请先为计划添加测项。")
    for index, test in enumerate(plan.tests):
        if index not in covered:
            issue("NO_MEASUREMENT", f"{test.name} 尚未指定测试结构和测量动作。", index)
    for measurement in target.measurements:
        index = measurement.test_index
        structure = structures.get(measurement.structure_id)
        if index >= len(plan.tests) or structure is None:
            issue("MEASUREMENT", "所选测项或测试结构已不存在。", index)
            continue
        if not measurement.steps:
            issue("NO_STEPS", f"{plan.tests[index].name} 尚未配置测量动作。", index)
        if not any(step.mode in {"voltage_current", "current_voltage", "measure_voltage", "measure_current"} for step in measurement.steps):
            issue("NO_READ", f"{plan.tests[index].name} 尚未配置测量读数。", index)
        required_pins = set(plan.tests[index].pin_ids)
        configured_terminals = {step.terminal.casefold() for step in measurement.steps}
        for pin in plan.pin_setup.mappings:
            if pin.id in required_pins and pin.terminal.casefold() not in configured_terminals:
                issue("PLAN_TERMINAL", f"{plan.tests[index].name} / {structure.name} 缺少计划所需的 {pin.terminal} 端子动作。", index)
        terminals = {row.terminal.casefold(): row for row in structure.terminals}
        phases: dict[str, list[tuple]] = {}
        for step in measurement.steps:
            terminal = terminals.get(step.terminal.casefold())
            needle = needles.get(terminal.needle_id) if terminal else None
            label = f"{plan.tests[index].name} / {structure.name} / {step.phase} / {step.terminal}"
            if needle is None or needle.site.casefold() != structure.site.casefold():
                issue("TERMINAL", f"{label} 缺少对应 Site 的探针卡接线。", index)
                continue
            resource = resources.get(step.resource_id)
            if resource is None:
                issue("RESOURCE", f"{label} 请选择机台的实装资源。", index)
                continue
            module, channel = resource
            for last_module, last_channel, last_needle, last_mode in phases.setdefault(step.phase.casefold(), []):
                same_channel = (module.slot.casefold(), channel.channel.casefold()) == (last_module.slot.casefold(), last_channel.channel.casefold())
                passive_fanout = channel.id == last_channel.id and step.mode == last_mode and step.mode in {"reference", "guard"}
                if (same_channel and not passive_fanout) or (channel.shared_group and channel.shared_group.casefold() == last_channel.shared_group.casefold() and not passive_fanout):
                    issue("RESOURCE_CONFLICT", f"{label} 与同阶段的测量动作共用互斥资源。", index)
                if needle.pin == last_needle.pin and ((step.mode == "guard") == (last_mode == "guard")):
                    issue("PIN_CONFLICT", f"{label} 与同阶段的测量动作竞争同一测试头针位。", index)
            phases[step.phase.casefold()].append((module, channel, needle, step.mode))
            if needle.pin not in channel.pins:
                issue("UNREACHABLE", f"{label}：{channel.port} 的 {channel.path} 路径无法到达针位 {needle.pin}。", index)
                continue
            problem = _capability_problem(step, channel, needle)
            if problem:
                issue("CAPABILITY", f"{label}：{problem}", index)
                continue
            routes.append({
                "test_index": index, "test_name": plan.tests[index].name, "structure": structure.name, "site": structure.site,
                "phase": step.phase, "terminal": terminal.terminal, "pad": terminal.pad, "needle": needle.needle, "pin": needle.pin,
                "pad_number": needle.specs_pad,
                "slot": module.slot, "module": module.model, "channel": channel.channel, "port": channel.port,
                "path": channel.path, "mode": step.mode,
            })
    return {"issues": issues, "routes": routes}


def _current_plan(conn, read_plan, plan_id, revision):
    plan = read_plan(conn, plan_id)
    if plan.archived or plan.revision != revision:
        raise HTTPException(409, "请打开最新的未归档计划，再核对机台接线。")
    return plan


def _review_inputs(conn, target):
    profiles = []
    for kind, reference in (("machines", target.machine), ("cards", target.card)):
        profile = _profile(conn, kind, reference.id)
        if profile["revision"] != reference.revision:
            raise HTTPException(409, "机台或探针卡档案已换版。请选用最新档案并重新核对。")
        profiles.append(profile)
    return profiles


def _binding(conn, row, plan):
    target = json.loads(row["request"])
    machine = _profile(conn, "machines", target["machine"]["id"], target["machine"]["revision"])
    card = _profile(conn, "cards", target["card"]["id"], target["card"]["revision"])
    stale = (plan.archived or target["plan_revision"] != plan.revision
             or _profile(conn, "machines", machine["id"])["revision"] != machine["revision"]
             or _profile(conn, "cards", card["id"])["revision"] != card["revision"])
    return {"id": row["id"], **target, "machine_name": machine["name"], "card_name": card["name"],
            "review": json.loads(row["review"]), "stale": stale}


def _latest_binding(conn, plan_id, machine_id, card_id):
    return conn.execute("""
        SELECT * FROM plan_hardware
        WHERE plan_id = ? AND json_extract(request, '$.machine.id') = ? AND json_extract(request, '$.card.id') = ?
        ORDER BY rowid DESC LIMIT 1
    """, (str(plan_id), str(machine_id), str(card_id))).fetchone()


def _export_inputs(conn, plan, binding_id):
    row = conn.execute("SELECT * FROM plan_hardware WHERE id = ? AND plan_id = ?", (str(binding_id), str(plan.id))).fetchone()
    if row is None:
        raise HTTPException(404, "该测试计划没有这条接线记录。")
    binding = _binding(conn, row, plan)
    latest = _latest_binding(conn, plan.id, binding["machine"]["id"], binding["card"]["id"])
    if latest["id"] != row["id"]:
        raise HTTPException(409, "此接线已换版，请打开最新的机台组合。")
    if binding["stale"] or binding["review"]["issues"]:
        raise HTTPException(409, "请核对最新的计划和机台接线，解决待处理项后再下载。")
    machine = _profile(conn, "machines", binding["machine"]["id"], binding["machine"]["revision"])
    card = _profile(conn, "cards", binding["card"]["id"], binding["card"]["revision"])
    return json.loads(row["plan_snapshot"]), binding, machine, card


def install_hardware_routes(router: APIRouter, connect: Callable, read_plan: Callable):
    @router.get("/hardware/machines")
    def machines():
        with connect() as conn:
            return _list_profiles(conn, "machines")

    @router.post("/hardware/machines", status_code=201)
    def new_machine(machine: MachineInput):
        with connect() as conn, conn:
            return _save_profile(conn, "machines", machine)

    @router.put("/hardware/machines/{profile_id}")
    def edit_machine(profile_id: UUID, machine: MachineUpdate):
        with connect() as conn, conn:
            return _save_profile(conn, "machines", machine, profile_id, machine.revision)

    @router.get("/hardware/cards")
    def cards():
        with connect() as conn:
            return _list_profiles(conn, "cards")

    @router.post("/hardware/cards", status_code=201)
    def new_card(card: CardInput):
        with connect() as conn, conn:
            return _save_profile(conn, "cards", card)

    @router.put("/hardware/cards/{profile_id}")
    def edit_card(profile_id: UUID, card: CardUpdate):
        with connect() as conn, conn:
            return _save_profile(conn, "cards", card, profile_id, card.revision)

    @router.get("/plans/{plan_id}/hardware")
    def list_bindings(plan_id: UUID):
        with connect() as conn, conn:
            conn.execute("BEGIN")
            plan = read_plan(conn, plan_id)
            rows = conn.execute("""
                SELECT * FROM plan_hardware WHERE rowid IN (
                    SELECT MAX(rowid) FROM plan_hardware WHERE plan_id = ?
                    GROUP BY json_extract(request, '$.machine.id'), json_extract(request, '$.card.id')
                ) ORDER BY rowid DESC
            """, (str(plan_id),)).fetchall()
            return [_binding(conn, row, plan) for row in rows]

    @router.post("/plans/{plan_id}/hardware/review")
    def review_binding(plan_id: UUID, target: Target):
        with connect() as conn, conn:
            conn.execute("BEGIN")
            plan = _current_plan(conn, read_plan, plan_id, target.plan_revision)
            return _review(target, plan, *_review_inputs(conn, target))

    @router.post("/plans/{plan_id}/hardware", status_code=201)
    def save_binding(plan_id: UUID, target: Target):
        with connect() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            plan = _current_plan(conn, read_plan, plan_id, target.plan_revision)
            review = _review(target, plan, *_review_inputs(conn, target))
            payload = target.model_dump_json(exclude={"previous_id"})
            previous = _latest_binding(conn, plan_id, target.machine.id, target.card.id)
            if previous is not None and previous["request"] == payload:
                return _binding(conn, previous, plan)
            previous_id = previous["id"] if previous is not None else None
            if (str(target.previous_id) if target.previous_id else None) != previous_id:
                raise HTTPException(409, "这个机台组合已有更新的接线，请打开已保存的组合后再修改。")
            binding_id = str(uuid4())
            conn.execute(
                "INSERT INTO plan_hardware VALUES (?, ?, ?, ?, ?)",
                (binding_id, str(plan_id), payload, json.dumps(review, ensure_ascii=False), plan.model_dump_json()),
            )
            row = conn.execute("SELECT * FROM plan_hardware WHERE id = ?", (binding_id,)).fetchone()
            return _binding(conn, row, plan)

    @router.get("/plans/{plan_id}/hardware/{binding_id}/export")
    def export_binding(plan_id: UUID, binding_id: UUID):
        with connect() as conn, conn:
            conn.execute("BEGIN")
            plan = read_plan(conn, plan_id)
            snapshot, binding, machine, card = _export_inputs(conn, plan, binding_id)
            filename = quote(f"{plan.name}-{machine['name']}-工程接线.json", safe="")
            return JSONResponse(
                {"schema_version": 1, "deployable_to_tester": False, "plan": snapshot,
                 "machine": machine, "card": card, "hardware": binding},
                headers={"Content-Disposition": f"attachment; filename=wat-hardware.json; filename*=UTF-8''{filename}"},
            )

    @router.post("/plans/{plan_id}/hardware/{binding_id}/specs31-build")
    def export_specs31(plan_id: UUID, binding_id: UUID, request: RecipeBuildRequest):
        with connect() as conn, conn:
            conn.execute("BEGIN")
            plan = read_plan(conn, plan_id)
            snapshot, binding, machine, _card = _export_inputs(conn, plan, binding_id)
        package = build_recipe_package(snapshot, binding, machine, request)
        return Response(package, media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="{request.recipe_key}-specs31-build.zip"',
        })
