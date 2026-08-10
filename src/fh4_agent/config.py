"""Configuration loading and validation for benchmark manifests."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from .contracts import BenchmarkConfig, ContractError


class ConfigError(ValueError):
    """Raised when a config path or document cannot be loaded or validated."""


def load_config(path: str | Path) -> BenchmarkConfig:
    """Load and validate a TOML or JSON benchmark configuration."""
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"config file does not exist: {config_path}")
    try:
        raw = config_path.read_bytes()
        if config_path.suffix.lower() == ".toml":
            document: Any = tomllib.loads(raw.decode("utf-8"))
        elif config_path.suffix.lower() == ".json":
            document = json.loads(raw.decode("utf-8"))
        else:
            raise ConfigError("config format must use .toml or .json")
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"invalid config syntax in {config_path}: {exc}") from exc
    try:
        return BenchmarkConfig.from_mapping(document)
    except ContractError as exc:
        raise ConfigError(f"invalid config in {config_path}: {exc}") from exc


load_benchmark_config = load_config

__all__ = ["ConfigError", "load_benchmark_config", "load_config"]
