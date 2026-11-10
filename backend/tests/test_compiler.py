import base64
import hashlib
import io
import json
import zipfile

import pytest

from wat_pilot.compiler import CompileValidationError, compile_program
from wat_pilot.example import build_example_request


def test_valid_public_example_builds_deterministic_reference_package():
    request = build_example_request()

    first = compile_program(request)
    second = compile_program(request)

    assert first.status == "REPLAY_READY"
    assert first.deployable_to_tester is False
    assert first.artifact_hash == second.artifact_hash
    assert first.package_base64 == second.package_base64
    assert len(first.artifact_hash) == 64

    files = {item.path: item.content for item in first.files}
    assert set(files) == {
        "README.txt",
        "limits.csv",
        "manifest.json",
        "program/reference_algorithm.tis.c",
        "replay/results.json",
        "test-plan.json",
        "validation-report.json",
    }
    assert "REFERENCE DRAFT - NOT QUALIFIED FOR TESTER EXECUTION" in files[
        "program/reference_algorithm.tis.c"
    ]
    assert "disconnect_all();" in files["program/reference_algorithm.tis.c"]
    assert "disable_port(SMU1);" in files["program/reference_algorithm.tis.c"]

    payload = base64.b64decode(first.package_base64)
    assert len(payload) < 250_000
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert sorted(archive.namelist()) == sorted(files)
        for generated in first.files:
            archived = archive.read(generated.path)
            assert archived.decode("utf-8") == generated.content
            assert hashlib.sha256(archived).hexdigest() == generated.sha256
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["artifact_hash"] == first.artifact_hash
        assert manifest["deployable_to_tester"] is False


def test_package_simulation_reproduces_public_typical_values():
    result = compile_program(build_example_request())
    by_id = {item.primitive_id: item for item in result.replay.results}

    assert by_id["vgs_threshold"].value == 3.0
    assert by_id["drain_breakdown"].value == 43.0
    assert by_id["drain_leakage"].value == 4e-8
    assert by_id["gate_leakage"].value == 2e-9
    assert all(item.passed for item in by_id.values())
    assert len(result.replay.curves[0].points) == 101


def test_enabling_package_rds_on_is_blocked_by_capability_gate():
    request = build_example_request()
    rds_on = next(item for item in request.tests if item.primitive_id == "rds_on_package")
    rds_on.enabled = True

    with pytest.raises(CompileValidationError) as error:
        compile_program(request)

    assert any(
        finding.code == "BLOCKED_PRIMITIVE"
        and finding.primitive_id == "rds_on_package"
        for finding in error.value.findings
    )


def test_blocked_primitive_cannot_be_enabled_by_lowering_its_current():
    request = build_example_request()
    rds_on = next(item for item in request.tests if item.primitive_id == "rds_on_package")
    rds_on.enabled = True
    rds_on.parameters["drain_current_a"] = 0.5

    with pytest.raises(CompileValidationError) as error:
        compile_program(request)

    assert any(item.code == "BLOCKED_PRIMITIVE" for item in error.value.findings)


def test_breakdown_without_positive_compliance_is_blocked():
    request = build_example_request()
    breakdown = next(item for item in request.tests if item.primitive_id == "drain_breakdown")
    breakdown.parameters["compliance_a"] = 0.0

    with pytest.raises(CompileValidationError) as error:
        compile_program(request)

    assert any(
        finding.code == "COMPLIANCE_REQUIRED"
        and finding.primitive_id == "drain_breakdown"
        for finding in error.value.findings
    )


def test_voltage_above_product_rating_is_blocked_even_when_tester_can_source_it():
    request = build_example_request()
    breakdown = next(item for item in request.tests if item.primitive_id == "drain_breakdown")
    breakdown.parameters["drain_stop_v"] = 60.0

    with pytest.raises(CompileValidationError) as error:
        compile_program(request)

    assert any(
        finding.code == "PRODUCT_VOLTAGE_BOUNDARY_EXCEEDED"
        and finding.primitive_id == "drain_breakdown"
        for finding in error.value.findings
    )


@pytest.mark.parametrize(
    ("primitive_id", "parameter", "value", "expected_code"),
    [
        ("vgs_threshold", "gate_step_v", 0.0, "INVALID_SWEEP"),
        ("vgs_threshold", "gate_stop_v", 2.0, "TYPICAL_VALUE_OUTSIDE_SWEEP"),
        ("gate_leakage", "gate_voltage_v", 25.0, "DEVICE_GATE_VOLTAGE_EXCEEDED"),
        ("drain_leakage", "compliance_a", 2.0, "CURRENT_CAPABILITY_EXCEEDED"),
        ("drain_leakage", "measurement_range_a", 0.0, "INVALID_MEASUREMENT_RANGE"),
        ("drain_breakdown", "target_current_a", 0.001, "TARGET_EXCEEDS_COMPLIANCE"),
        ("vgs_threshold", "gate_step_v", 1e-300, "SWEEP_POINT_LIMIT_EXCEEDED"),
        ("drain_breakdown", "drain_step_v", 1e-300, "SWEEP_POINT_LIMIT_EXCEEDED"),
        ("drain_breakdown", "compliance_a", 1.0, "PRIMITIVE_ENVELOPE_EXCEEDED"),
        ("drain_leakage", "compliance_a", 1.0, "PRIMITIVE_ENVELOPE_EXCEEDED"),
        ("gate_leakage", "compliance_a", 1.0, "PRIMITIVE_ENVELOPE_EXCEEDED"),
        ("drain_leakage", "measurement_range_a", 1e-12, "PRIMITIVE_ENVELOPE_EXCEEDED"),
    ],
)
def test_editable_parameters_are_rejected_when_they_break_the_primitive_contract(
    primitive_id, parameter, value, expected_code
):
    request = build_example_request()
    test = next(item for item in request.tests if item.primitive_id == primitive_id)
    test.parameters[parameter] = value

    with pytest.raises(CompileValidationError) as error:
        compile_program(request)

    assert any(
        item.code == expected_code and item.primitive_id == primitive_id
        for item in error.value.findings
    )


def test_client_cannot_raise_server_owned_capability_or_replace_pin_map():
    request = build_example_request()
    request.capability.max_voltage_v = 1000
    request.capability.pin_map["drain"] = "REAL_DRAIN_1"

    with pytest.raises(CompileValidationError) as error:
        compile_program(request)

    assert any(item.code == "REFERENCE_CONTEXT_MISMATCH" for item in error.value.findings)


def test_invalid_limits_are_blocked_and_limit_failures_are_not_called_passed():
    invalid = build_example_request()
    threshold = next(item for item in invalid.tests if item.primitive_id == "vgs_threshold")
    threshold.limits = {"minimum_v": 4.0, "maximum_v": 3.0}

    with pytest.raises(CompileValidationError) as error:
        compile_program(invalid)

    assert any(item.code == "INVALID_LIMITS" for item in error.value.findings)

    failing = build_example_request()
    threshold = next(item for item in failing.tests if item.primitive_id == "vgs_threshold")
    threshold.limits["minimum_v"] = 3.2
    result = compile_program(failing)

    assert result.status == "REPLAY_LIMIT_FAILED"
    assert any(not item.passed for item in result.replay.results)
