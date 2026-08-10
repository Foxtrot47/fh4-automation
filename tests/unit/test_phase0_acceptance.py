from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fh4_agent.contracts import (
    ActionValidationError,
    BenchmarkIdentity,
    BenchmarkValidationError,
    ContractError,
    ControllerSample,
    FrameSample,
    RequestedControlAction,
    SessionMetadata,
    TelemetrySample,
    neutral_action,
)
from fh4_agent.controls import DryRunControllerBackend
from fh4_agent.safety import SafetySupervisor

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs" / "benchmark" / "horizon_festival_circuit.toml"


@pytest.fixture
def benchmark_mapping() -> dict[str, object]:
    return {
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


def test_typed_contracts_round_trip_all_fields(
    benchmark_mapping: dict[str, object],
) -> None:
    action = RequestedControlAction(-1.0, 1.0, 0.25, 1.0)
    assert RequestedControlAction.from_mapping(action.to_mapping()) == action

    telemetry = TelemetrySample(
        1.5,
        speed_mps=12.0,
        distance_m=345.0,
        engine_rpm=4_500.0,
        lap_number=2,
        is_race_on=True,
    )
    assert TelemetrySample.from_mapping(telemetry.to_mapping()) == telemetry

    frame = FrameSample(1.5, frame_id=3, width=1280, height=720, source="capture")
    assert FrameSample.from_mapping(frame.to_mapping()) == frame

    controller = ControllerSample(1.5, action, is_physical=False)
    assert ControllerSample.from_mapping(controller.to_mapping()) == controller

    benchmark = BenchmarkIdentity.from_mapping(benchmark_mapping)
    metadata = SessionMetadata.create(
        benchmark,
        game_build="test-build",
        started_monotonic_s=12.0,
        session_id="session-1",
    )
    assert SessionMetadata.from_mapping(metadata.to_mapping()) == metadata
    assert metadata.config_digest


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_action_rejects_non_finite_values(bad: float) -> None:
    with pytest.raises(ActionValidationError, match="finite"):
        RequestedControlAction.from_mapping(
            {"steering": bad, "throttle": 0.0, "brake": 0.0}
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("steering", -1.01),
        ("steering", 1.01),
        ("throttle", -0.01),
        ("throttle", 1.01),
        ("brake", -0.01),
        ("brake", 1.01),
        ("handbrake", -0.01),
        ("handbrake", 1.01),
    ],
)
def test_action_rejects_out_of_range_values(field: str, value: float) -> None:
    mapping: dict[str, object] = {"steering": 0.0, "throttle": 0.0, "brake": 0.0}
    mapping["handbrake"] = 0.0
    mapping[field] = value
    with pytest.raises(ActionValidationError, match="between"):
        RequestedControlAction.from_mapping(mapping)


def test_samples_reject_nan_out_of_range_and_unknown_fields() -> None:
    with pytest.raises(ContractError, match="finite"):
        TelemetrySample(timestamp=float("nan"))
    with pytest.raises(ContractError, match="between"):
        TelemetrySample(timestamp=0.0, speed_mps=200.1)
    with pytest.raises(ContractError, match="unknown"):
        FrameSample.from_mapping(
            {"timestamp": 0.0, "frame_id": 1, "width": 1, "height": 1, "extra": 1}
        )
    with pytest.raises(ContractError, match="non-negative"):
        ControllerSample.from_mapping(
            {
                "timestamp": -1.0,
                "action": {"steering": 0.0, "throttle": 0.0, "brake": 0.0},
            }
        )


def test_benchmark_identity_enforces_fixed_assists(
    benchmark_mapping: dict[str, object],
) -> None:
    benchmark = BenchmarkIdentity.from_mapping(benchmark_mapping)
    assert benchmark.to_mapping() == benchmark_mapping

    assists = benchmark_mapping["assists"]
    assert isinstance(assists, dict)
    for field, invalid in (
        ("racing_line", "off"),
        ("transmission", "manual"),
        ("steering", "simulation"),
    ):
        candidate = dict(benchmark_mapping)
        candidate["assists"] = {**assists, field: invalid}
        with pytest.raises(BenchmarkValidationError, match=field):
            BenchmarkIdentity.from_mapping(candidate)

    for field in ("abs", "traction_control", "assisted_braking"):
        candidate = dict(benchmark_mapping)
        candidate["assists"] = {**assists, field: 1}
        with pytest.raises(BenchmarkValidationError, match=field):
            BenchmarkIdentity.from_mapping(candidate)


def test_disarmed_and_faulted_supervisor_always_emit_neutral() -> None:
    backend = DryRunControllerBackend()
    supervisor = SafetySupervisor(backend)
    request = RequestedControlAction(steering=0.4, throttle=0.7, brake=0.0)

    assert supervisor.submit(request) == neutral_action()
    supervisor.fault("capture lost")
    assert supervisor.submit(request) == neutral_action()
    supervisor.disarm("operator requested")
    assert supervisor.submit(request) == neutral_action()
    assert backend.last_action == neutral_action()
    assert backend.device_writes == 0


def test_supervisor_arm_cannot_bypass_validated_benchmark_evidence() -> None:
    backend = DryRunControllerBackend()
    supervisor = SafetySupervisor(backend)
    request = RequestedControlAction(steering=0.2, throttle=0.3, brake=0.0)

    supervisor.arm()
    assert supervisor.submit(request) == neutral_action()
    assert backend.device_writes == 0


def test_cli_process_exit_behavior_from_outside_repository(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    command = [sys.executable, "-m", "fh4_agent", "dry-run", "--config", str(CONFIG)]
    success = subprocess.run(
        command,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert success.returncode == 0
    assert json.loads(success.stdout)["device_writes"] == 0

    malformed = subprocess.run(
        [*command, "--action", '{"steering":NaN,"throttle":0,"brake":0}'],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert malformed.returncode == 2
    assert "finite" in malformed.stderr


def test_generated_subagent_artifacts_are_ignored() -> None:
    ignore_lines = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert ".pi-subagents/" in ignore_lines
    for directory in ("/artifacts/", "/data/", "/recordings/", "/models/"):
        assert directory in ignore_lines
    for generated in (
        ".pi-subagents/missions/generated.json",
        ".pi-subagents/artifacts/generated.log",
        "src/fh4_agent/models/__pycache__/generated.pyc",
    ):
        result = subprocess.run(
            ["git", "check-ignore", "--quiet", generated],
            cwd=ROOT,
            check=False,
        )
        assert result.returncode == 0, generated
