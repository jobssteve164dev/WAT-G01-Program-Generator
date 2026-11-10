from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProductReference(StrictModel):
    manufacturer: str = Field(max_length=80)
    model: str = Field(max_length=80)
    description: str = Field(max_length=240)
    qualification: str = Field(max_length=80)
    datasheet_url: str = Field(max_length=500)
    voltage_class_v: float


class TestSelection(StrictModel):
    primitive_id: str = Field(max_length=80)
    name: str = Field(max_length=160)
    enabled: bool = True
    parameters: dict[str, float] = Field(default_factory=dict, max_length=12)
    limits: dict[str, float] = Field(default_factory=dict, max_length=6)
    unit: str = Field(max_length=24)
    source_condition: str = Field(max_length=240)
    blocked_reason: str | None = Field(default=None, max_length=500)


class CapabilityProfile(StrictModel):
    profile_id: str = Field(max_length=120)
    max_voltage_v: float
    max_current_a: float
    low_current_floor_a: float
    pin_map: dict[str, str] = Field(max_length=3)


class CompileRequest(StrictModel):
    product: ProductReference
    capability: CapabilityProfile
    tests: list[TestSelection] = Field(min_length=1, max_length=5)
    usage_boundary: str = Field(max_length=120)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=8)


class ValidationFinding(BaseModel):
    severity: str
    code: str
    message: str
    primitive_id: str | None = None


class GeneratedFile(BaseModel):
    path: str
    media_type: str
    content: str
    sha256: str


class SimulationPoint(BaseModel):
    x: float
    y: float


class SimulationCurve(BaseModel):
    primitive_id: str
    x_label: str
    y_label: str
    points: list[SimulationPoint]


class SimulationResult(BaseModel):
    primitive_id: str
    name: str
    value: float
    unit: str
    passed: bool


class SimulationSummary(BaseModel):
    results: list[SimulationResult]
    curves: list[SimulationCurve]


class CompileResult(BaseModel):
    status: str
    deployable_to_tester: bool
    artifact_hash: str
    findings: list[ValidationFinding]
    files: list[GeneratedFile]
    replay: SimulationSummary
    package_base64: str


