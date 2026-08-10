"""Safe foundation contracts for Forza Horizon 4 automation."""

from .contracts import (
    ActionValidationError,
    ArmingState,
    AssistConfiguration,
    BenchmarkConfig,
    BenchmarkIdentity,
    BenchmarkValidationError,
    ContractError,
    ControllerSample,
    FrameSample,
    RequestedControlAction,
    RuntimeArmingState,
    SessionMetadata,
    TelemetrySample,
    benchmark_digest,
    neutral_action,
)
from .telemetry import (
    FH4TelemetryPacket,
    NormalizedControls,
    decode_packet,
)

__all__ = [
    "ActionValidationError",
    "ArmingState",
    "BenchmarkValidationError",
    "AssistConfiguration",
    "BenchmarkConfig",
    "BenchmarkIdentity",
    "ContractError",
    "ControllerSample",
    "FrameSample",
    "RequestedControlAction",
    "RuntimeArmingState",
    "SessionMetadata",
    "TelemetrySample",
    "benchmark_digest",
    "neutral_action",
    "FH4TelemetryPacket",
    "NormalizedControls",
    "decode_packet",
]

__version__ = "0.1.0"
