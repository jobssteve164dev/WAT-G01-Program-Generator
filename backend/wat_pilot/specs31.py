import io
import json
import re
import shlex
from pathlib import PurePosixPath
from typing import Annotated
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from fastapi import HTTPException
from pydantic import Field, StringConstraints, model_validator

from .models import StrictModel


RelativeName = Annotated[str, StringConstraints(min_length=1, max_length=160)]
NAME_PART = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


class RecipeBuildRequest(StrictModel):
    test_plan: RelativeName
    recipe_file: RelativeName
    recipe_key: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_]{1,30}$")]
    limit_file: RelativeName | None = None
    prober_file: RelativeName | None = None
    process: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}$")] | None = None
    libraries: list[RelativeName] = Field(default_factory=list, max_length=6)
    constants: list[RelativeName] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def check_native_names(self):
        paths = [self.test_plan, self.recipe_file, self.limit_file, self.prober_file, *self.libraries, *self.constants]
        if any(not all(NAME_PART.fullmatch(part) for part in path.split("/")) for path in paths if path is not None):
            raise ValueError("请使用 SPECS 目录内的相对名称。名称只支持英文字母、数字、点、横线、下划线和目录分隔符。")
        if not self.test_plan.endswith((".tpl", ".tsf")):
            raise ValueError("原厂计划请选择 SPECS 保存的 .tpl 或 transtpl 导出的 .tsf。")
        output_plan = str(PurePosixPath(self.test_plan).with_suffix(".tpl"))
        if self.recipe_file in {self.test_plan, output_plan}:
            raise ValueError("配方与原厂计划必须使用不同的名称。")
        return self


def _build_script(request: RecipeBuildRequest) -> str:
    test_plan = str(PurePosixPath(request.test_plan).with_suffix(".tpl"))
    args = ["create_recipe"]
    for option, value in (
        ("-i", request.process), ("-p", request.prober_file), ("-l", request.limit_file),
        ("-t", ",".join(request.constants)), ("-m", ",".join(request.libraries)),
    ):
        if value:
            args.extend([option, value])
    args.extend([request.recipe_file, request.recipe_key, test_plan])
    lines = [
        "#!/bin/sh",
        "set -eu",
        'specs_root=${WAT_SPECS_ROOT:-/opt/SPECS}',
        'tpl_dir=${WAT_SPECS_TPL_DIR:-"$specs_root/tpl"}',
        'if [ -z "${WAT_SPECS_TPL_DIR:-}" ] && [ ! -d "$tpl_dir" ]; then tpl_dir="$specs_root/usr/tpl"; fi',
        r'fail() { printf "%s\n" "$1" >&2; exit 1; }',
        'command -v create_recipe >/dev/null 2>&1 || fail "SPECS create_recipe was not found in PATH."',
        'cd "$tpl_dir" || fail "Set WAT_SPECS_TPL_DIR to the engineering SPECS test plan directory."',
        'mkdir .wat-specs31-build || fail "Another preparation is active in this SPECS test plan directory."',
        'converted_plan=""',
        'new_recipe=""',
        'cleanup() {',
        '    code=$?',
        '    trap - 0',
        '    if [ "$code" -ne 0 ]; then',
        '        for created in "$converted_plan" "$new_recipe"; do',
        '            if [ -n "$created" ] && [ -f "$created" ] && [ ! -L "$created" ]; then',
        r'                unlink "$created" || printf "Check %s before retrying.\n" "$created" >&2',
        '            fi',
        '        done',
        '    fi',
        '    rmdir .wat-specs31-build || exit 1',
        '    exit "$code"',
        '}',
        'trap cleanup 0',
        'trap "exit 130" INT',
        'trap "exit 143" TERM',
        f'[ -r {shlex.quote(request.test_plan)} ] || fail "SPECS input plan was not found."',
        f'[ ! -e {shlex.quote(request.recipe_file)} ] && [ ! -L {shlex.quote(request.recipe_file)} ] || fail "Choose a new engineering recipe name."',
    ]
    if request.limit_file:
        lines.append(f'[ -r "$specs_root/usr/lim/{request.limit_file}" ] || fail "SPECS limit file was not found."')
    if request.test_plan.endswith(".tsf"):
        lines.extend([
            'command -v transtpl >/dev/null 2>&1 || fail "SPECS transtpl was not found in PATH."',
            f'[ ! -e {shlex.quote(test_plan)} ] && [ ! -L {shlex.quote(test_plan)} ] || fail "Choose a TSF name without an existing TPL."',
            f'converted_plan={shlex.quote(test_plan)}',
            shlex.join(["transtpl", request.test_plan, test_plan]),
            f'[ -s {shlex.quote(test_plan)} ] || fail "transtpl did not produce the expected test plan."',
        ])
    lines.extend([
        f'new_recipe={shlex.quote(request.recipe_file)}',
        shlex.join(args),
        f'[ -s {shlex.quote(request.recipe_file)} ] || fail "Check the recipe name and create_recipe output in SPECS."',
        rf'printf "%s\n" {shlex.quote("Built " + request.recipe_file + ". Open in Recipe Editor and run Data > Check Recipe.")}',
    ])
    return "\n".join(lines) + "\n"


def build_recipe_package(plan: dict, binding: dict, machine: dict, request: RecipeBuildRequest) -> bytes:
    if not re.fullmatch(r"D\.03\.1[0-9]", machine["specs_version"]):
        raise HTTPException(422, "此构建包适用于 4082F 上的 SPECS D.03.1x。请先核对目标机 SPECS 版本。")
    routes = binding["review"]["routes"]
    if any(route["pad_number"] is None or route["pin"] == "CHUCK" for route in routes):
        raise HTTPException(422, "请为参与测量的卡针补全 SPECS Pad Number。Chuck 路径需要按现场 Probe Spec 单独验证。")
    probe_spec = {route["pad_number"]: route["pin"] for route in routes}
    manifest = {
        "tested_on_tester": False, "plan": plan, "binding_id": binding["id"],
        "machine": binding["machine"], "card": binding["card"], "specs_version": machine["specs_version"],
        "request": request.model_dump(), "routes": routes, "measurements": binding["measurements"],
        "probe_spec": [{"pad_number": pad, "pin_number": pin} for pad, pin in sorted(probe_spec.items())],
    }
    guide = (
        "SPECS 3.1 工程配方准备包\n\n"
        "1. 在目标版本的工程 SPECS 环境，用 build-input.json 核对原厂计划。"
        "在 Probe Spec 检查数字 Pad Number 与矩阵针位，在 Test Spec 检查算法端子、输入、限值及实际资源。"
        "输入计划必须是已保存的原厂 TPL 或用 transtpl 导出的 TSF。\n"
        "2. 核对当前 RCPLIB_* 路径指向工程目录，确认所需算法库、限值、常量和探针台配方已就位。"
        "在该环境执行 sh build-recipe.sh，由原厂工具创建配方及其规格。使用单独的新配方名称。\n"
        "   工程环境的计划目录与默认目录不同，可通过 WAT_SPECS_TPL_DIR 指定现场实际目录。\n"
        "3. 在 Recipe Editor 打开配方，选择条目后执行 Data → Check Recipe。"
        "再由目标工程机的现场入口选择 Recipe Key，运行参考测项，核对测量值、状态与限值判定。\n"
    )
    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as package:
        for name, contents in (
            ("README.txt", guide), ("build-input.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"),
            ("build-recipe.sh", _build_script(request)),
        ):
            package.writestr(ZipInfo(name), contents, compress_type=ZIP_DEFLATED)
    return output.getvalue()
