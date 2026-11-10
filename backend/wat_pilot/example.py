from .models import CapabilityProfile, CompileRequest, ProductReference, TestSelection


def build_example_request() -> CompileRequest:
    return CompileRequest(
        product=ProductReference(
            manufacturer="Nexperia",
            model="BUK7M6R0-40H",
            description="40 V N-channel Trench 9 automotive MOSFET",
            qualification="AEC-Q101",
            datasheet_url=(
                "https://assets.nexperia.com/documents/data-sheet/BUK7M6R0-40H.pdf"
            ),
            voltage_class_v=40.0,
        ),
        capability=CapabilityProfile(
            profile_id="4080_PUBLIC_REFERENCE_DEMO_PROFILE_V1",
            max_voltage_v=200.0,
            max_current_a=1.0,
            low_current_floor_a=1e-9,
            pin_map={
                "drain": "DEMO_DRAIN_RESOURCE",
                "gate": "DEMO_GATE_RESOURCE",
                "source": "DEMO_SOURCE_RESOURCE",
            },
        ),
        tests=[
            TestSelection(
                primitive_id="vgs_threshold",
                name="Gate threshold voltage",
                parameters={
                    "drain_current_a": 0.001,
                    "gate_start_v": 0.0,
                    "gate_stop_v": 5.0,
                    "gate_step_v": 0.05,
                },
                limits={"minimum_v": 2.4, "maximum_v": 3.6},
                unit="V",
                source_condition="ID=1 mA, VDS=VGS, Tj=25 C",
            ),
            TestSelection(
                primitive_id="drain_breakdown",
                name="Drain-source breakdown voltage",
                parameters={
                    "drain_start_v": 0.0,
                    "drain_stop_v": 45.0,
                    "drain_step_v": 0.5,
                    "target_current_a": 0.00025,
                    "compliance_a": 0.00025,
                },
                limits={"minimum_v": 40.0},
                unit="V",
                source_condition="ID=250 uA, VGS=0 V, Tj=25 C",
            ),
            TestSelection(
                primitive_id="drain_leakage",
                name="Zero-gate drain leakage",
                parameters={
                    "drain_voltage_v": 40.0,
                    "gate_voltage_v": 0.0,
                    "compliance_a": 0.00001,
                    "measurement_range_a": 0.000001,
                },
                limits={"maximum_a": 0.000001},
                unit="A",
                source_condition="VDS=40 V, VGS=0 V, Tj=25 C",
            ),
            TestSelection(
                primitive_id="gate_leakage",
                name="Gate-source leakage",
                parameters={
                    "gate_voltage_v": 20.0,
                    "drain_voltage_v": 0.0,
                    "compliance_a": 0.000001,
                    "measurement_range_a": 0.0000001,
                },
                limits={"maximum_a": 0.0000001},
                unit="A",
                source_condition="VGS=20 V, VDS=0 V, Tj=25 C",
            ),
            TestSelection(
                primitive_id="rds_on_package",
                name="Package on-state resistance",
                enabled=False,
                parameters={"gate_voltage_v": 10.0, "drain_current_a": 20.0},
                limits={"maximum_ohm": 0.006},
                unit="ohm",
                source_condition="VGS=10 V, ID=20 A, Tj=25 C",
                blocked_reason=(
                    "数据手册条件要求 20 A，超过公开 4080 参考能力上限 1 A；"
                    "封装 RDS(on) 也不是晶圆 WAT 限值。"
                ),
            ),
        ],
        usage_boundary="PUBLIC_DATASHEET_ENGINEERING_DEMO_NOT_WAT_LIMITS",
        metadata={
            "datasheet_revision": "2025-01-10",
            "reference_temperature_c": 25,
        },
    )
