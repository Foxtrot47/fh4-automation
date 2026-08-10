from __future__ import annotations

import pytest

from fh4_agent.contracts import (
    ActionValidationError,
    ArmingState,
    AssistConfiguration,
    BenchmarkConfig,
    BenchmarkIdentity,
    ControllerSample,
    FrameSample,
    RequestedControlAction,
    RuntimeArmingState,
    SessionMetadata,
    TelemetrySample,
    neutral_action,
)


@pytest.fixture
def benchmark() -> BenchmarkIdentity:
    return BenchmarkIdentity.from_mapping(
        {
            "track": "Horizon Festival Circuit",
            "car_make": "Ford",
            "car_model": "Focus RS",
            "car_year": 2017,
            "car_condition": "stock",
            "season": "summer",
            "weather": "clear",
            "time_of_day": "daytime",
            "camera": "hood",
            "assists": {
                "racing_line": "full",
                "abs": True,
                "traction_control": True,
                "transmission": "automatic",
                "steering": "normal",
                "assisted_braking": False,
            },
        }
    )


def test_action_ranges_and_neutral_fail_safe() -> None:
    assert neutral_action().to_mapping() == {
        "steering": 0.0,
        "throttle": 0.0,
        "brake": 0.0,
        "handbrake": 0.0,
    }
    with pytest.raises(ActionValidationError, match="between"):
        RequestedControlAction.from_mapping(
            {"steering": 1.1, "throttle": 0.0, "brake": 0.0}
        )
    with pytest.raises(ActionValidationError, match="missing"):
        RequestedControlAction.from_mapping({"steering": 0.0})


def test_timestamped_samples_round_trip() -> None:
    telemetry = TelemetrySample(1.5, speed_mps=12.0, lap_number=2, is_race_on=True)
    assert TelemetrySample.from_mapping(telemetry.to_mapping()) == telemetry
    frame = FrameSample(1.5, frame_id=3, width=1280, height=720)
    assert FrameSample.from_mapping(frame.to_mapping()) == frame
    controller = ControllerSample(
        1.5,
        RequestedControlAction(0.1, 0.2, 0.0),
    )
    assert ControllerSample.from_mapping(controller.to_mapping()) == controller


def test_session_metadata_round_trip(benchmark: BenchmarkIdentity) -> None:
    metadata = SessionMetadata.create(
        benchmark,
        game_build="test-build",
        started_monotonic_s=12.0,
        session_id="session-1",
    )
    assert SessionMetadata.from_mapping(metadata.to_mapping()) == metadata
    assert metadata.config_digest


def test_arming_requires_explicit_validation(benchmark: BenchmarkIdentity) -> None:
    config = BenchmarkConfig(identity=benchmark)
    assert not RuntimeArmingState.disarmed().can_emit
    armed = RuntimeArmingState.armed(config)
    assert armed.can_emit
    assert armed.benchmark is config
    with pytest.raises(ValueError, match="validated BenchmarkConfig"):
        RuntimeArmingState(state=ArmingState.ARMED)
    with pytest.raises(ValueError, match="validated BenchmarkConfig"):
        RuntimeArmingState.armed(benchmark)  # type: ignore[arg-type]


def test_assist_contract_is_fixed() -> None:
    with pytest.raises(ValueError, match="racing_line"):
        AssistConfiguration.from_mapping(
            {
                "racing_line": "off",
                "abs": True,
                "traction_control": True,
                "transmission": "automatic",
                "steering": "normal",
                "assisted_braking": False,
            }
        )
