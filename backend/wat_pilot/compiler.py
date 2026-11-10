import base64
import csv
import hashlib
import io
import json
import math
import zipfile

from .models import (
    CompileRequest,
    CompileResult,
    GeneratedFile,
    SimulationCurve,
    SimulationPoint,
    SimulationResult,
    SimulationSummary,
    TestSelection,
    ValidationFinding,
)


SUPPORTED_PRIMITIVES = {
    "vgs_threshold",
    "drain_breakdown",
    "drain_leakage",
    "gate_leakage",
}
PARAMETER_KEYS = {
    "vgs_threshold": {"drain_current_a", "gate_start_v", "gate_stop_v", "gate_step_v"},
    "drain_breakdown": {
        "drain_start_v", "drain_stop_v", "drain_step_v", "target_current_a", "compliance_a"
    },
    "drain_leakage": {
        "drain_voltage_v", "gate_voltage_v", "compliance_a", "measurement_range_a"
    },
    "gate_leakage": {
        "gate_voltage_v", "drain_voltage_v", "compliance_a", "measurement_range_a"
    },
}
LIMIT_KEYS = {
    "vgs_threshold": {"minimum_v", "maximum_v"},
    "drain_breakdown": {"minimum_v"},
    "drain_leakage": {"maximum_a"},
    "gate_leakage": {"maximum_a"},
}
TYPICAL_VALUES = {
    "vgs_threshold": 3.0,
    "drain_breakdown": 43.0,
    "drain_leakage": 4e-8,
    "gate_leakage": 2e-9,
}


class CompileValidationError(ValueError):
    def __init__(self, findings: list[ValidationFinding]):
        super().__init__("compile validation failed")
        self.findings = findings


def compile_program(request: CompileRequest) -> CompileResult:
    findings = _validate(request)
    blocking = [item for item in findings if item.severity == "error"]
    if blocking:
        raise CompileValidationError(findings)

    simulation = _simulate(request)
    status = (
        "REPLAY_READY"
        if all(item.passed for item in simulation.results)
        else "REPLAY_LIMIT_FAILED"
    )
    if status == "REPLAY_LIMIT_FAILED":
        findings.append(
            ValidationFinding(
                severity="warning",
                code="REPLAY_LIMIT_FAILED",
                message="公开典型值回放存在未通过限值的项目，请复核限值后再评审。",
            )
        )
    payloads = _render_payloads(request, findings, simulation, status)
    payload_hashes = {
        path: hashlib.sha256(content.encode("utf-8")).hexdigest()
        for path, content in sorted(payloads.items())
    }
    identity = {
        "schema_version": 1,
        "product_model": request.product.model,
        "capability_profile": request.capability.profile_id,
        "payload_sha256": payload_hashes,
    }
    artifact_hash = hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()
    manifest = {
        **identity,
        "artifact_hash": artifact_hash,
        "status": status,
        "deployable_to_tester": False,
        "deployment_blocker": (
            "当前公开资料不足以确认目标 SPECS 编译器、加载器、资源映射和受支持的部署接口。"
        ),
    }
    payloads["manifest.json"] = _pretty_json(manifest)

    files = [
        GeneratedFile(
            path=path,
            media_type=_media_type(path),
            content=content,
            sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )
        for path, content in sorted(payloads.items())
    ]
    package_bytes = _deterministic_zip({item.path: item.content for item in files})

    return CompileResult(
        status=status,
        deployable_to_tester=False,
        artifact_hash=artifact_hash,
        findings=findings,
        files=files,
        replay=simulation,
        package_base64=base64.b64encode(package_bytes).decode("ascii"),
    )


def _validate(request: CompileRequest) -> list[ValidationFinding]:
    findings = [
        ValidationFinding(
            severity="warning",
            code="REFERENCE_ONLY",
            message=(
                "生成的 TIS/C 源码是可评审参考稿，尚不是通过 SPECS 认证的机台制品。"
            ),
        ),
        ValidationFinding(
            severity="warning",
            code="PACKAGE_DATASHEET_NOT_WAT_LIMITS",
            message=(
                "公开封装器件限值仅用于跑通流程，不是晶圆验收限值。"
            ),
        ),
    ]
    findings.extend(_validate_reference_context(request))
    enabled = [item for item in request.tests if item.enabled]
    if not enabled:
        findings.append(
            ValidationFinding(
                severity="error",
                code="NO_TEST_SELECTED",
                message="请至少选择一个测试项目。",
            )
        )
        return findings

    for test in enabled:
        if test.primitive_id == "rds_on_package":
            findings.append(
                _error(
                    test,
                    "BLOCKED_PRIMITIVE",
                    "封装导通电阻不属于当前 G‑01 可生成原语，不能通过修改电流绕过。",
                )
            )
            continue
        if test.primitive_id not in SUPPORTED_PRIMITIVES:
            findings.append(
                _error(test, "UNKNOWN_PRIMITIVE", "当前不支持这个测试项目。")
            )
            continue
        if set(test.parameters) != PARAMETER_KEYS[test.primitive_id]:
            findings.append(
                _error(test, "INVALID_PARAMETER_SET", "测试参数集合与当前原语契约不一致。")
            )
            continue
        if set(test.limits) != LIMIT_KEYS[test.primitive_id]:
            findings.append(
                _error(test, "INVALID_LIMIT_SET", "判定限值集合与当前原语契约不一致。")
            )
            continue
        if not all(
            math.isfinite(value)
            for value in [*test.parameters.values(), *test.limits.values()]
        ):
            findings.append(
                _error(test, "NON_FINITE_PARAMETER", "所有参数都必须是有限数值。")
            )
            continue
        if not _limits_are_ordered(test.limits):
            findings.append(
                _error(test, "INVALID_LIMITS", "判定下限不能大于判定上限。")
            )
            continue

        if test.primitive_id == "vgs_threshold":
            _validate_threshold(request, test, findings)
        elif test.primitive_id == "drain_breakdown":
            _validate_breakdown(request, test, findings)
        else:
            _validate_leakage(request, test, findings)

    if any(not item.enabled and item.blocked_reason for item in request.tests):
        findings.append(
            ValidationFinding(
                severity="info",
                code="UNSUPPORTED_TEST_EXCLUDED",
                message="封装级大电流测试已按能力边界排除。",
                primitive_id="rds_on_package",
            )
        )
    return findings


def _validate_reference_context(request: CompileRequest) -> list[ValidationFinding]:
    from .example import build_example_request

    canonical = build_example_request()
    mismatch = (
        request.product != canonical.product
        or request.capability != canonical.capability
        or request.usage_boundary != canonical.usage_boundary
        or request.metadata != canonical.metadata
    )
    canonical_tests = {item.primitive_id: item for item in canonical.tests}
    request_ids = [item.primitive_id for item in request.tests]
    if set(request_ids) != set(canonical_tests) or len(request_ids) != len(set(request_ids)):
        mismatch = True
    else:
        for test in request.tests:
            expected = canonical_tests[test.primitive_id]
            if (
                test.name != expected.name
                or test.unit != expected.unit
                or test.source_condition != expected.source_condition
                or test.blocked_reason != expected.blocked_reason
            ):
                mismatch = True
                break
    if not mismatch:
        return []
    return [
        ValidationFinding(
            severity="error",
            code="REFERENCE_CONTEXT_MISMATCH",
            message="器件、参考能力、测试身份和示意资源由服务端固定，客户端不能修改。",
        )
    ]


def _limits_are_ordered(limits: dict[str, float]) -> bool:
    minimum = next((value for key, value in limits.items() if key.startswith("minimum")), None)
    maximum = next((value for key, value in limits.items() if key.startswith("maximum")), None)
    return minimum is None or maximum is None or minimum <= maximum


def _sweep_exceeds_point_limit(start: float, stop: float, step: float) -> bool:
    if step <= 0 or stop <= start:
        return False
    point_count = (stop - start) / step + 1
    return not math.isfinite(point_count) or point_count > 10_000 or start + step == start


def _validate_threshold(
    request: CompileRequest,
    test: TestSelection,
    findings: list[ValidationFinding],
) -> None:
    p = test.parameters
    if p["gate_step_v"] <= 0 or p["gate_stop_v"] <= p["gate_start_v"]:
        findings.append(_error(test, "INVALID_SWEEP", "栅压扫描必须正向且步进大于 0。"))
    elif _sweep_exceeds_point_limit(p["gate_start_v"], p["gate_stop_v"], p["gate_step_v"]):
        findings.append(_error(test, "SWEEP_POINT_LIMIT_EXCEEDED", "栅压扫描不能超过 10000 个点，且每一步必须能推进电压。"))
    if max(abs(p["gate_start_v"]), abs(p["gate_stop_v"])) > 20:
        findings.append(_error(test, "DEVICE_GATE_VOLTAGE_EXCEEDED", "栅极电压超过器件 ±20 V 绝对额定值。"))
    if not (0 < p["drain_current_a"] <= request.capability.max_current_a):
        findings.append(_error(test, "CURRENT_CAPABILITY_EXCEEDED", "漏极电流必须大于 0 且不超过参考能力上限。"))
    if not math.isclose(p["drain_current_a"], 0.001, rel_tol=0, abs_tol=1e-12):
        findings.append(_error(test, "SOURCE_CONDITION_UNSUPPORTED", "公开典型值回放仅支持 ID=1 mA 的数据手册条件。"))
    if not p["gate_start_v"] <= TYPICAL_VALUES["vgs_threshold"] <= p["gate_stop_v"]:
        findings.append(_error(test, "TYPICAL_VALUE_OUTSIDE_SWEEP", "栅压扫描窗口未覆盖公开典型值 3.0 V。"))


def _validate_breakdown(
    request: CompileRequest,
    test: TestSelection,
    findings: list[ValidationFinding],
) -> None:
    p = test.parameters
    if p["drain_start_v"] < 0 or p["drain_step_v"] <= 0 or p["drain_stop_v"] <= p["drain_start_v"]:
        findings.append(_error(test, "INVALID_SWEEP", "击穿扫描必须从非负电压正向扫描，且步进大于 0。"))
    elif _sweep_exceeds_point_limit(p["drain_start_v"], p["drain_stop_v"], p["drain_step_v"]):
        findings.append(_error(test, "SWEEP_POINT_LIMIT_EXCEEDED", "击穿扫描不能超过 10000 个点，且每一步必须能推进电压。"))
    if not p["drain_start_v"] <= TYPICAL_VALUES["drain_breakdown"] <= p["drain_stop_v"]:
        findings.append(_error(test, "TYPICAL_VALUE_OUTSIDE_SWEEP", "漏压扫描窗口未覆盖公开典型值 43 V。"))
    if p["compliance_a"] <= 0:
        findings.append(_error(test, "COMPLIANCE_REQUIRED", "击穿扫描必须设置大于 0 的电流限值。"))
    if p["compliance_a"] > request.capability.max_current_a:
        findings.append(_error(test, "CURRENT_CAPABILITY_EXCEEDED", "电流限值超过参考能力上限。"))
    if not math.isclose(p["compliance_a"], 0.00025, rel_tol=0, abs_tol=1e-12):
        findings.append(_error(test, "PRIMITIVE_ENVELOPE_EXCEEDED", "公开击穿原语的电流限值固定为 250 µA。"))
    if p["target_current_a"] <= 0 or p["target_current_a"] > p["compliance_a"]:
        findings.append(_error(test, "TARGET_EXCEEDS_COMPLIANCE", "判定电流必须大于 0 且不能超过电流限值。"))
    if not math.isclose(p["target_current_a"], 0.00025, rel_tol=0, abs_tol=1e-12):
        findings.append(_error(test, "SOURCE_CONDITION_UNSUPPORTED", "公开典型值回放仅支持 ID=250 µA 的数据手册条件。"))
    if p["drain_stop_v"] > request.product.voltage_class_v * 1.2:
        findings.append(
            _error(
                test,
                "PRODUCT_VOLTAGE_BOUNDARY_EXCEEDED",
                f"扫描终点 {p['drain_stop_v']:g} V 超过工程演示边界：1.2 × {request.product.voltage_class_v:g} V。",
            )
        )
    if p["drain_stop_v"] > request.capability.max_voltage_v:
        findings.append(_error(test, "VOLTAGE_CAPABILITY_EXCEEDED", "扫描终点超过当前能力配置。"))


def _validate_leakage(
    request: CompileRequest,
    test: TestSelection,
    findings: list[ValidationFinding],
) -> None:
    p = test.parameters
    if p["compliance_a"] <= 0:
        findings.append(_error(test, "COMPLIANCE_REQUIRED", "漏电测量必须设置大于 0 的电流限值。"))
    if p["compliance_a"] > request.capability.max_current_a:
        findings.append(_error(test, "CURRENT_CAPABILITY_EXCEEDED", "电流限值超过参考能力上限。"))
    if p["measurement_range_a"] <= 0 or p["measurement_range_a"] > p["compliance_a"]:
        findings.append(_error(test, "INVALID_MEASUREMENT_RANGE", "测量量程必须大于 0 且不能超过电流限值。"))
    expected_compliance = 1e-5 if test.primitive_id == "drain_leakage" else 1e-6
    expected_range = 1e-6 if test.primitive_id == "drain_leakage" else 1e-7
    if not (
        math.isclose(p["compliance_a"], expected_compliance, rel_tol=0, abs_tol=1e-15)
        and math.isclose(p["measurement_range_a"], expected_range, rel_tol=0, abs_tol=1e-15)
        and p["measurement_range_a"] >= request.capability.low_current_floor_a
        and p["measurement_range_a"] >= TYPICAL_VALUES[test.primitive_id]
        and p["measurement_range_a"] >= test.limits["maximum_a"]
    ):
        findings.append(
            _error(
                test,
                "PRIMITIVE_ENVELOPE_EXCEEDED",
                "漏电原语的限流与量程必须使用公开参考包络，并覆盖典型值和判定上限。",
            )
        )
    if abs(p["drain_voltage_v"]) > request.product.voltage_class_v:
        findings.append(_error(test, "DEVICE_DRAIN_VOLTAGE_EXCEEDED", "漏极电压超过器件 40 V 额定值。"))
    if abs(p["gate_voltage_v"]) > 20:
        findings.append(_error(test, "DEVICE_GATE_VOLTAGE_EXCEEDED", "栅极电压超过器件 ±20 V 绝对额定值。"))
    expected = (40.0, 0.0) if test.primitive_id == "drain_leakage" else (0.0, 20.0)
    if not (
        math.isclose(p["drain_voltage_v"], expected[0], rel_tol=0, abs_tol=1e-12)
        and math.isclose(p["gate_voltage_v"], expected[1], rel_tol=0, abs_tol=1e-12)
    ):
        findings.append(_error(test, "SOURCE_CONDITION_UNSUPPORTED", "当前公开典型值回放只支持数据手册给出的电压条件。"))


def _error(test: TestSelection, code: str, message: str) -> ValidationFinding:
    return ValidationFinding(
        severity="error",
        code=code,
        message=message,
        primitive_id=test.primitive_id,
    )


def _simulate(request: CompileRequest) -> SimulationSummary:
    values = {
        "vgs_threshold": (3.0, "V"),
        "drain_breakdown": (43.0, "V"),
        "drain_leakage": (4e-8, "A"),
        "gate_leakage": (2e-9, "A"),
    }
    results: list[SimulationResult] = []
    curves: list[SimulationCurve] = []
    for test in request.tests:
        if not test.enabled or test.primitive_id not in values:
            continue
        value, unit = values[test.primitive_id]
        results.append(
            SimulationResult(
                primitive_id=test.primitive_id,
                name=test.name,
                value=value,
                unit=unit,
                passed=_passes(value, test.limits),
            )
        )
        if test.primitive_id == "vgs_threshold":
            points = []
            for index in range(101):
                voltage = round(index * 0.05, 10)
                current = min(1.0, 10 ** (-9 + (2 * voltage)))
                points.append(SimulationPoint(x=voltage, y=current))
            curves.append(
                SimulationCurve(
                    primitive_id="vgs_threshold",
                    x_label="VGS (V)",
                    y_label="ID (A)",
                    points=points,
                )
            )
    return SimulationSummary(results=results, curves=curves)


def _passes(value: float, limits: dict[str, float]) -> bool:
    minimum = next((v for k, v in limits.items() if k.startswith("minimum")), None)
    maximum = next((v for k, v in limits.items() if k.startswith("maximum")), None)
    return (minimum is None or value >= minimum) and (maximum is None or value <= maximum)


def _render_payloads(
    request: CompileRequest,
    findings: list[ValidationFinding],
    simulation: SimulationSummary,
    status: str,
) -> dict[str, str]:
    request_payload = request.model_dump(mode="json")
    return {
        "README.txt": _readme(request, status),
        "limits.csv": _limits_csv(request),
        "program/reference_algorithm.tis.c": _reference_tis_source(request),
        "replay/results.json": _pretty_json(simulation.model_dump(mode="json")),
        "test-plan.json": _pretty_json(request_payload),
        "validation-report.json": _pretty_json(
            {
                "status": status,
                "findings": [item.model_dump(mode="json") for item in findings],
            }
        ),
    }


def _readme(request: CompileRequest, status: str) -> str:
    return (
        "WAT G-01 参考程序包\n"
        "===================\n\n"
        f"公开器件：{request.product.manufacturer} {request.product.model}\n"
        f"状态：{status} / 公开典型值回放 / 机台部署锁定\n\n"
        "本程序包使用公开封装器件数据手册演示从规格到制品的生成流程。"
        "它不是晶圆验收规格，不得直接加载到 4080/4082 机台。"
        "解锁机台部署需要目标版本的 SPECS 测量算法开发手册、受支持的编译器和加载器、"
        "准确的资源映射、离线编译结果、硬件相关性验证及正式审批。\n"
    )


def _limits_csv(request: CompileRequest) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["primitive_id", "limit_name", "value", "unit"])
    for test in request.tests:
        if not test.enabled:
            continue
        for name, value in sorted(test.limits.items()):
            writer.writerow([test.primitive_id, name, f"{value:.12g}", test.unit])
    return output.getvalue()


def _reference_tis_source(request: CompileRequest) -> str:
    pin_map = request.capability.pin_map
    lines = [
        "/*",
        " * REFERENCE DRAFT - NOT QUALIFIED FOR TESTER EXECUTION",
        f" * Product: {_safe_comment(request.product.model)}",
        " * Public TIS-style source for review and typical-value replay only.",
        " * Resource symbols below are placeholders, not confirmed tester mappings.",
        " * Return-code handling and executable limit judgement are not yet integrated.",
        " */",
        "#include <tis.h>",
        "#include <math.h>",
        "",
        f"#define PIN_DRAIN {pin_map['drain']}",
        f"#define PIN_GATE {pin_map['gate']}",
        f"#define PIN_SOURCE {pin_map['source']}",
        "",
        "static void safe_cleanup(void) {",
        "    force_v(SMU1, 0.0, 0.0, 1.0e-6);",
        "    force_v(SMU2, 0.0, 0.0, 1.0e-6);",
        "    disable_port(SMU1);",
        "    disable_port(SMU2);",
        "    disconnect_all();",
        "}",
        "",
        "int main(void) {",
        "    double measured = 0.0;",
        "    double voltage = 0.0;",
        "    init_system();",
        "    connect_pin(SMU1, PIN_DRAIN);",
        "    connect_pin(SMU2, PIN_GATE);",
        "    connect_pin(GNDU, PIN_SOURCE);",
        "",
    ]
    for test in request.tests:
        if not test.enabled:
            continue
        p = test.parameters
        lines.append(f"    /* {_safe_comment(test.name)}: {_safe_comment(test.source_condition)} */")
        if test.primitive_id == "vgs_threshold":
            lines.extend(
                [
                    f"    for (voltage = {_number(p['gate_start_v'])}; voltage <= {_number(p['gate_stop_v'])}; voltage += {_number(p['gate_step_v'])}) {{",
                    f"        force_v(SMU1, voltage, 0.0, {_number(p['drain_current_a'])});",
                    "        force_v(SMU2, voltage, 0.0, 1.0e-3);",
                    f"        measure_i(SMU1, &measured, {_number(p['drain_current_a'])});",
                    f"        if (fabs(measured) >= {_number(p['drain_current_a'])}) break;",
                    "    }",
                ]
            )
        elif test.primitive_id == "drain_breakdown":
            lines.extend(
                [
                    "    force_v(SMU2, 0.0, 0.0, 1.0e-6);",
                    f"    for (voltage = {_number(p['drain_start_v'])}; voltage <= {_number(p['drain_stop_v'])}; voltage += {_number(p['drain_step_v'])}) {{",
                    f"        force_v(SMU1, voltage, 0.0, {_number(p['compliance_a'])});",
                    f"        measure_i(SMU1, &measured, {_number(p['target_current_a'])});",
                    f"        if (fabs(measured) >= {_number(p['target_current_a'])}) break;",
                    "    }",
                ]
            )
        elif test.primitive_id == "drain_leakage":
            lines.extend(
                [
                    f"    force_v(SMU2, {_number(p['gate_voltage_v'])}, 0.0, 1.0e-6);",
                    f"    force_v(SMU1, {_number(p['drain_voltage_v'])}, 0.0, {_number(p['compliance_a'])});",
                    f"    measure_i(SMU1, &measured, {_number(p['measurement_range_a'])});",
                ]
            )
        elif test.primitive_id == "gate_leakage":
            lines.extend(
                [
                    f"    force_v(SMU1, {_number(p['drain_voltage_v'])}, 0.0, 1.0e-6);",
                    f"    force_v(SMU2, {_number(p['gate_voltage_v'])}, 0.0, {_number(p['compliance_a'])});",
                    f"    measure_i(SMU2, &measured, {_number(p['measurement_range_a'])});",
                ]
            )
        lines.append("")
    lines.extend(["    safe_cleanup();", "    return 0;", "}", ""])
    return "\n".join(lines)


def _safe_comment(value: str) -> str:
    return value.replace("*/", "* /").replace("\n", " ").replace("\r", " ")


def _number(value: float) -> str:
    return f"{value:.12g}"


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _pretty_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _media_type(path: str) -> str:
    if path.endswith(".json"):
        return "application/json"
    if path.endswith(".csv"):
        return "text/csv"
    if path.endswith(".c"):
        return "text/x-c"
    return "text/plain"


def _deterministic_zip(files: dict[str, str]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, content in sorted(files.items()):
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, content.encode("utf-8"))
    return output.getvalue()
