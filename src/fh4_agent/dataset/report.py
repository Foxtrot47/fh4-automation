"""Machine-readable and human-readable offline dataset quality reports."""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from .contracts import SessionQuality, SplitAssignment


def quality_document(
    qualities: list[SessionQuality],
    *,
    splits: SplitAssignment | None = None,
    shard_counts: dict[str, int] | None = None,
) -> dict[str, object]:
    accepted = sum(quality.accepted for quality in qualities)
    lap_durations = [
        lap.duration_s for quality in qualities for lap in quality.candidate_laps
    ]
    return {
        "schema": 1,
        "summary": {
            "sessions": len(qualities),
            "accepted_sessions": accepted,
            "rejected_sessions": len(qualities) - accepted,
            "aligned_frames": sum(item.aligned_frames for item in qualities),
            "alignment_rejections": sum(
                item.alignment_rejections for item in qualities
            ),
            "excluded_non_race_frames": sum(
                item.excluded_non_race_frames for item in qualities
            ),
            "candidate_complete_laps": len(lap_durations),
            "candidate_lap_median_duration_s": (
                statistics.median(lap_durations) if lap_durations else None
            ),
            "split_session_counts": (
                {name: len(values) for name, values in splits.to_mapping().items()}
                if splits is not None
                else {}
            ),
            "shard_counts": dict(sorted((shard_counts or {}).items())),
        },
        "sessions": [item.to_mapping() for item in qualities],
    }


def quality_markdown(document: dict[str, object]) -> str:
    summary = document["summary"]
    assert isinstance(summary, dict)
    lines = [
        "# Dataset quality report",
        "",
        f"- Sessions: {summary['sessions']}",
        "- Accepted/rejected: "
        f"{summary['accepted_sessions']}/{summary['rejected_sessions']}",
        f"- Aligned frames: {summary['aligned_frames']}",
        f"- Alignment rejections: {summary['alignment_rejections']}",
        f"- Excluded non-race frames: {summary['excluded_non_race_frames']}",
        f"- Candidate complete laps: {summary['candidate_complete_laps']}",
        "- Candidate lap median duration (s): "
        f"{summary['candidate_lap_median_duration_s']}",
        "- Split session counts: `"
        f"{json.dumps(summary['split_session_counts'], sort_keys=True)}`",
        f"- Shard counts: `{json.dumps(summary['shard_counts'], sort_keys=True)}`",
        "",
        "## Sessions",
        "",
        "| Session | Status | Rates (frame/controller/telemetry Hz) | "
        "Alignment rejected | Non-race excluded | Alignment deltas | "
        "Telemetry continuity | Health | Controls | Candidate laps |",
        "|---|---|---|---:|---:|---|---|---|---|---:|",
    ]
    sessions = document["sessions"]
    assert isinstance(sessions, list)
    for raw in sessions:
        assert isinstance(raw, dict)
        rates = raw["rates"]
        counts = raw["counts"]
        assert isinstance(rates, dict) and isinstance(counts, dict)
        status = (
            "accepted" if raw["accepted"] else "rejected: " + "; ".join(raw["reasons"])
        )
        lines.append(
            (
                "| {session} | {status} | "
                "{frame:.3f}/{controller:.3f}/{telemetry:.3f} | {rejected} | "
                "{excluded} | `{alignment}` | `{continuity}` | `{health}` | "
                "`{controls}` | {laps} |"
            ).format(
                session=raw["session_id"],
                status=status,
                frame=rates["frames_hz"],
                controller=rates["controller_hz"],
                telemetry=rates["telemetry_hz"],
                rejected=counts["alignment_rejections"],
                excluded=counts["excluded_non_race_frames"],
                alignment=json.dumps(raw["alignment"], sort_keys=True),
                continuity=json.dumps(raw["telemetry_continuity"], sort_keys=True),
                health=json.dumps(raw["health"], sort_keys=True),
                controls=json.dumps(raw["controls"], sort_keys=True),
                laps=len(raw["candidate_complete_laps"]),
            )
        )
    lines.append("")
    return "\n".join(lines)


def write_quality_reports(
    json_path: str | Path,
    markdown_path: str | Path,
    qualities: list[SessionQuality],
    *,
    splits: SplitAssignment | None = None,
    shard_counts: dict[str, int] | None = None,
) -> dict[str, object]:
    document = quality_document(qualities, splits=splits, shard_counts=shard_counts)
    Path(json_path).write_text(
        json.dumps(document, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    Path(markdown_path).write_text(quality_markdown(document), encoding="utf-8")
    return document


__all__ = ["quality_document", "quality_markdown", "write_quality_reports"]
