"""Offline validation, segmentation, session splits, and dataset shards."""

from .builder import assign_session_splits, build_dataset
from .contracts import (
    DEFAULT_ALIGNMENT_NS,
    MAX_MISALIGNED_FRACTION,
    MAX_SAMPLES_PER_SHARD,
    AlignedFrame,
    DatasetError,
    LapCandidate,
    SessionQuality,
    SplitAssignment,
)
from .laps import LapStateMachine
from .report import quality_document, quality_markdown, write_quality_reports
from .session import StreamingSessionAdapter

__all__ = [
    "AlignedFrame",
    "DEFAULT_ALIGNMENT_NS",
    "DatasetError",
    "LapCandidate",
    "LapStateMachine",
    "MAX_MISALIGNED_FRACTION",
    "MAX_SAMPLES_PER_SHARD",
    "SessionQuality",
    "SplitAssignment",
    "StreamingSessionAdapter",
    "assign_session_splits",
    "build_dataset",
    "quality_document",
    "quality_markdown",
    "write_quality_reports",
]
