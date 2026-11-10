from wat_pilot.example import build_example_request


def test_public_example_maps_datasheet_conditions_without_claiming_wat_equivalence():
    request = build_example_request()

    assert request.product.model == "BUK7M6R0-40H"
    assert request.product.qualification == "AEC-Q101"
    assert request.product.datasheet_url == (
        "https://assets.nexperia.com/documents/data-sheet/BUK7M6R0-40H.pdf"
    )

    by_id = {item.primitive_id: item for item in request.tests}
    assert by_id["vgs_threshold"].parameters == {
        "drain_current_a": 0.001,
        "gate_start_v": 0.0,
        "gate_stop_v": 5.0,
        "gate_step_v": 0.05,
    }
    assert by_id["vgs_threshold"].limits == {"minimum_v": 2.4, "maximum_v": 3.6}
    assert by_id["rds_on_package"].enabled is False
    assert "20 A" in by_id["rds_on_package"].blocked_reason
    assert request.usage_boundary == "PUBLIC_DATASHEET_ENGINEERING_DEMO_NOT_WAT_LIMITS"
    assert request.capability.pin_map == {
        "drain": "DEMO_DRAIN_RESOURCE",
        "gate": "DEMO_GATE_RESOURCE",
        "source": "DEMO_SOURCE_RESOURCE",
    }
