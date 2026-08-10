from __future__ import annotations

import json
from pathlib import Path

import pytest

from fh4_agent.cli import main
from fh4_agent.config import ConfigError, load_config
from fh4_agent.contracts import RequestedControlAction, neutral_action
from fh4_agent.controls import DryRunControllerBackend
from fh4_agent.safety import SafetySupervisor

CONFIG = Path("configs/benchmark/horizon_festival_circuit.toml")


def test_benchmark_config_loads() -> None:
    config = load_config(CONFIG)
    assert config.track == "Horizon Festival Circuit"
    assert config.car == "2017 Ford Focus RS (stock)"
    assert config.identity.assists.assisted_braking is False


def test_malformed_config_fails_explicitly(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text("[benchmark]\ntrack = 42\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid config"):
        load_config(path)


def test_unarmed_supervisor_neutralizes_without_device_writes() -> None:
    backend = DryRunControllerBackend()
    supervisor = SafetySupervisor(backend)
    request = RequestedControlAction(steering=0.4, throttle=0.7, brake=0.0)
    assert supervisor.submit(request) == neutral_action()
    assert backend.last_action == neutral_action()
    assert backend.device_writes == 0


def test_armed_dry_run_records_only_memory() -> None:
    backend = DryRunControllerBackend()
    supervisor = SafetySupervisor(backend)
    supervisor.arm(load_config(CONFIG))
    request = RequestedControlAction(steering=-0.2, throttle=0.5, brake=0.0)
    assert supervisor.submit(request) == request
    assert backend.last_action == request
    assert backend.device_writes == 0


def test_cli_validate_and_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate-config", str(CONFIG)]) == 0
    validated = json.loads(capsys.readouterr().out)
    assert validated["benchmark"]["track"] == "Horizon Festival Circuit"

    assert (
        main(
            [
                "dry-run",
                "--config",
                str(CONFIG),
                "--action",
                '{"steering":0.5,"throttle":0.4,"brake":0}',
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["arming_state"] == "disarmed"
    assert output["emitted_action"]["throttle"] == 0.0
    assert output["device_writes"] == 0


def test_cli_rejects_malformed_action(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(
            [
                "dry-run",
                "--config",
                str(CONFIG),
                "--action",
                '{"steering":2,"throttle":0,"brake":0}',
            ]
        )
    assert error.value.code == 2
    assert "between" in capsys.readouterr().err
