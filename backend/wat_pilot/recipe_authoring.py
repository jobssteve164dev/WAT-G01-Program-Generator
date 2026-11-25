"""Materialize qualified recipe dependencies without guessing a tester file layout."""
import hashlib
import io
import re
from decimal import Decimal
from typing import Annotated
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import HTTPException, Response
from pydantic import Field, FiniteFloat, ValidationError, model_validator

from .hardware import _export_inputs
from .models import StrictModel


class OffLeakage(StrictModel):
    structure: str = Field(min_length=1, max_length=80, pattern=r"\S")
    vds_v: FiniteFloat = Field(strict=True)
    vgs_v: FiniteFloat = Field(strict=True)
    temperature_c: FiniteFloat = Field(ge=-273.15, strict=True)


class Cell(StrictModel):
    file: str
    line: int = Field(ge=1)
    column: int = Field(ge=1)
    original: str = Field(pattern=r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee][+-]?[0-9]+)?$")
    scale: Decimal = Field(gt=0, allow_inf_nan=False)


class Variable(StrictModel):
    min: Decimal
    max: Decimal
    cells: list[Cell] = Field(min_length=1)

    @model_validator(mode="after")
    def check_range(self):
        if self.min > self.max:
            raise ValueError("invalid qualified range")
        return self


class Fixed(StrictModel):
    fixed: Decimal


class MethodRow(StrictModel):
    structure: str
    parameters: dict[str, Variable | Fixed]

    @model_validator(mode="after")
    def check_fields(self):
        if set(self.parameters) != {"vds_v", "vgs_v", "temperature_c", "upper_ua"}:
            raise ValueError("incomplete test method")
        return self


class Method(StrictModel):
    plan: UUID
    binding: UUID
    entry: str
    qualification: str = Field(min_length=1, max_length=160, pattern=r"\S")
    files: dict[str, Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]]
    rows: list[MethodRow] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def check_files(self):
        if self.entry not in self.files or any(
            not all(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._-]*", part) for part in name.split("/"))
            or name == "method.json" for name in self.files
        ):
            raise ValueError("invalid native filename")
        return self


def method_at(directory):
    try:
        return Method.model_validate_json((directory / "method.json").read_bytes())
    except (OSError, ValidationError) as error:
        raise HTTPException(409, "设备的配方资料需要重新核对。") from error


def validate_layout(plan, method):
    if len(plan.tests) != len(method.rows):
        raise HTTPException(422, "此设备的方法未覆盖当前测项组合。")
    for item, row in zip(plan.tests, method.rows, strict=True):
        if not item.off_leakage or item.off_leakage.structure != row.structure:
            raise HTTPException(422, "此设备的方法未覆盖所选结构和测项顺序。")


LABELS = {"vds_v": "漏源电压 (V)", "vgs_v": "栅源电压 (V)", "temperature_c": "温度 (°C)", "upper_ua": "漏电流上限 (µA)"}


def parameter_changes(plan, method):
    validate_layout(plan, method)
    changes = {}
    for index, (item, row) in enumerate(zip(plan.tests, method.rows, strict=True), 1):
        values = item.off_leakage.model_dump() | {"upper_ua": item.upper_limit}
        for key, param in row.parameters.items():
            value = Decimal(str(values[key]))
            label = f"第 {index} 项的{LABELS[key]}"
            if isinstance(param, Fixed):
                if value != param.fixed:
                    raise HTTPException(422, f"{label}只支持 {param.fixed}。")
                continue
            if not param.min <= value <= param.max:
                raise HTTPException(422, f"{label}须在 {param.min} 至 {param.max} 之间。")
            for cell in param.cells:
                pos = (cell.file, cell.line, cell.column)
                if cell.file not in method.files or pos in changes:
                    raise HTTPException(409, "设备配方参数位置冲突或缺失。")
                changes[pos] = (cell.original.encode("ascii"), format((value * cell.scale).normalize(), "f").encode("ascii"))
    return changes


def build_native(plan, directory, method):
    files = {}
    for name, digest in method.files.items():
        path = directory / name
        if not path.resolve().is_relative_to(directory.resolve()):
            raise HTTPException(409, "设备配方的文件位置已变化。")
        try:
            data = path.read_bytes()
        except OSError as error:
            raise HTTPException(409, "设备配方缺少已核对的文件。") from error
        if hashlib.sha256(data).hexdigest() != digest:
            raise HTTPException(409, "设备配方文件已变化，请联系设备负责人重新核对。")
        files[name] = data
    changes = parameter_changes(plan, method)
    if any(name not in files for name, _, _ in changes):
        raise HTTPException(409, "设备配方缺少参数文件。")
    for name in {pos[0] for pos in changes}:
        raw = files[name]
        if not raw.isascii() or b"\0" in raw:
            raise HTTPException(409, "该设备参数需要原厂工具处理。")
        lines = raw.split(b"\n")
        for (target, line, column), (original, replacement) in changes.items():
            if target != name:
                continue
            try:
                content = lines[line - 1]
                ending = b"\r" if content.endswith(b"\r") else b""
                cells = content.removesuffix(b"\r").split(b"\t")
                if cells[column - 1] != original:
                    raise ValueError
                cells[column - 1] = replacement
                lines[line - 1] = b"\t".join(cells) + ending
            except (IndexError, ValueError) as error:
                raise HTTPException(409, "原厂文件中的参数与已核对的版本不符。") from error
        files[name] = b"\n".join(lines)
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return output.getvalue()


def install_recipe_routes(router, store, read_plan):
    root = store.path.parent / "recipe-methods"

    def target_for(conn, product, directory):
        method = method_at(directory)
        reference = read_plan(conn, method.plan)
        if reference.product_id != product:
            raise HTTPException(409, "设备方法属于其他产品。")
        _, binding, machine, card = _export_inputs(conn, reference, method.binding)
        structures = {row["id"]: row["name"] for row in binding["structures"]}
        covered = {(row["test_index"], structures[row["structure_id"]]) for row in binding["measurements"]}
        if len(binding["measurements"]) != len(method.rows) or len(reference.tests) != len(method.rows) or covered != set(enumerate(row.structure for row in method.rows)):
            raise HTTPException(409, "设备接线与方法中的结构或测项不一致。")
        return method, {"machine_id": machine["id"], "card_id": card["id"], "id": directory.name, "machine": machine["name"], "card": card["name"],
                        "structures": [row.structure for row in method.rows]}

    @router.get("/products/{product_id}/recipe-structures")
    def available_structures(product_id: UUID):
        names = set()
        with store.connect() as conn:
            conn.execute("BEGIN")
            for desc in (root / str(product_id)).glob("*/method.json"):
                try:
                    _, target = target_for(conn, product_id, desc.parent)
                    names.update(target["structures"])
                except HTTPException:
                    continue
        return sorted(names)

    @router.get("/plans/{plan_id}/recipe-targets")
    def targets(plan_id: UUID):
        with store.connect() as conn:
            conn.execute("BEGIN")
            plan = read_plan(conn, plan_id)
            result = {}
            reasons = []
            for desc in sorted((root / str(plan.product_id)).glob("*/method.json")):
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", desc.parent.name):
                    continue
                try:
                    method, target = target_for(conn, plan.product_id, desc.parent)
                    parameter_changes(plan, method)
                    result.setdefault((target["machine_id"], target["card_id"]), target)
                except HTTPException as error:
                    if error.status_code == 422:
                        reasons.append(str(error.detail))
            return {"targets": list(result.values()), "reason": " ".join(dict.fromkeys(reasons)) or
                    "当前没有已接入的设备可执行这组测项，请联系设备负责人。"}

    @router.get("/plans/{plan_id}/recipe/{method_id}")
    def download(plan_id: UUID, method_id: str, revision: int):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", method_id):
            raise HTTPException(404, "未找到适用设备。")
        with store.connect() as conn:
            conn.execute("BEGIN")
            plan = read_plan(conn, plan_id)
            if plan.archived or plan.revision != revision:
                raise HTTPException(409, "请打开最新的未归档计划再生成配方。")
            directory = root / str(plan.product_id) / method_id
            method, _ = target_for(conn, plan.product_id, directory)
            package = build_native(plan, directory, method)
        return Response(package, media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="recipe-{plan.id}-r{revision}-{method_id}.zip"',
            "Cache-Control": "no-store",
        })
