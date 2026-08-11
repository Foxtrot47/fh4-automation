"""Deterministic whole-session splits and bounded WebDataset-style shards."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from collections.abc import Sequence
from pathlib import Path

from ..capture import CameraProfile
from ..contracts import BenchmarkIdentity
from .contracts import (
    MAX_SAMPLES_PER_SHARD,
    DatasetError,
    SessionQuality,
    SplitAssignment,
    validate_session_id,
)
from .report import write_quality_reports
from .session import StreamingSessionAdapter

_SPLITS = ("train", "validation", "test")


def _session_key(session_id: str) -> tuple[str, str]:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest(), session_id


def assign_session_splits(session_ids: list[str]) -> SplitAssignment:
    if not session_ids:
        raise DatasetError("at least one session is required")
    for session_id in session_ids:
        validate_session_id(session_id)
    if len(set(session_ids)) != len(session_ids):
        raise DatasetError("session IDs must be unique")
    ordered = sorted(session_ids, key=_session_key)
    count = len(ordered)
    if count < 2:
        validation_count = test_count = 0
    elif count == 2:
        validation_count, test_count = 1, 0
    else:
        validation_count = max(1, round(count * 0.1))
        test_count = max(1, round(count * 0.1))
        while validation_count + test_count >= count:
            if validation_count >= test_count:
                validation_count -= 1
            else:
                test_count -= 1
    train_count = count - validation_count - test_count
    return SplitAssignment(
        tuple(ordered[:train_count]),
        tuple(ordered[train_count : train_count + validation_count]),
        tuple(ordered[train_count + validation_count :]),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _add_bytes(archive: tarfile.TarFile, name: str, content: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mtime = 0
    info.mode = 0o644
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    archive.addfile(info, io.BytesIO(content))


def _write_split_shards(
    output: Path,
    split: str,
    adapters: list[StreamingSessionAdapter],
    *,
    max_samples: int,
) -> list[dict[str, object]]:
    shards: list[dict[str, object]] = []
    archive: tarfile.TarFile | None = None
    path: Path | None = None
    count = 0
    shard_index = 0

    def close_shard() -> None:
        nonlocal archive, path, count
        if archive is None or path is None:
            return
        archive.close()
        shards.append(
            {
                "path": path.name,
                "split": split,
                "samples": count,
                "sha256": _sha256(path),
            }
        )
        archive = None
        path = None
        count = 0

    try:
        for adapter in adapters:
            quality = adapter.validate()
            for sample in adapter.aligned_frames():
                if archive is None:
                    path = output / f"{split}-{shard_index:05d}.tar"
                    archive = tarfile.open(path, mode="w", format=tarfile.USTAR_FORMAT)
                    shard_index += 1
                key = f"{sample.session_id}-{sample.frame_index:08d}"
                metadata = sample.metadata(
                    benchmark_digest=quality.benchmark_digest,
                    config_digest=quality.config_digest,
                )
                metadata["split"] = split
                _add_bytes(archive, f"{key}.jpg", sample.jpeg)
                _add_bytes(
                    archive,
                    f"{key}.json",
                    json.dumps(
                        metadata,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode("utf-8"),
                )
                count += 1
                if count == max_samples:
                    close_shard()
        close_shard()
    finally:
        if archive is not None:
            archive.close()
    return shards


def build_dataset(
    output: str | Path,
    session_directories: Sequence[str | Path],
    benchmark: BenchmarkIdentity,
    profile: CameraProfile | None = None,
    *,
    max_samples_per_shard: int = MAX_SAMPLES_PER_SHARD,
) -> dict[str, object]:
    if (
        isinstance(max_samples_per_shard, bool)
        or not isinstance(max_samples_per_shard, int)
        or not 1 <= max_samples_per_shard <= MAX_SAMPLES_PER_SHARD
    ):
        raise ValueError("max_samples_per_shard must be between 1 and 1024")
    if not session_directories:
        raise DatasetError("at least one session is required")
    root = Path(output)
    if root.exists():
        raise DatasetError(f"dataset output already exists: {root}")
    adapters = [
        StreamingSessionAdapter(directory, benchmark, profile)
        for directory in session_directories
    ]
    qualities: list[SessionQuality] = [adapter.validate() for adapter in adapters]
    qualities.sort(key=lambda item: _session_key(item.session_id))
    rejected = [quality for quality in qualities if not quality.accepted]
    if rejected:
        details = "; ".join(
            f"{item.session_id}: {', '.join(item.reasons)}" for item in rejected
        )
        raise DatasetError(f"dataset contains rejected session(s): {details}")
    ids = [quality.session_id for quality in qualities]
    splits = assign_session_splits(ids)
    config_digests = {quality.config_digest for quality in qualities}
    benchmark_digests = {quality.benchmark_digest for quality in qualities}
    profile_digests = {quality.profile_digest for quality in qualities}
    if (
        len(config_digests) > 1
        or len(benchmark_digests) > 1
        or len(profile_digests) > 1
    ):
        raise DatasetError("dataset contains mixed configuration or capture profiles")
    root.mkdir(parents=True, exist_ok=False)
    by_id = {adapter.metadata.session_id: adapter for adapter in adapters}
    source_manifest_digests = {
        adapter.metadata.session_id: _sha256(adapter.directory / "manifest.json")
        for adapter in adapters
    }
    all_shards: list[dict[str, object]] = []
    shard_counts: dict[str, int] = {}
    for split in _SPLITS:
        selected = getattr(splits, split)
        written = _write_split_shards(
            root,
            split,
            [by_id[session_id] for session_id in selected],
            max_samples=max_samples_per_shard,
        )
        all_shards.extend(written)
        shard_counts[split] = len(written)
    write_quality_reports(
        root / "quality.json",
        root / "quality.md",
        qualities,
        splits=splits,
        shard_counts=shard_counts,
    )
    manifest: dict[str, object] = {
        "schema": 1,
        "format": "webdataset-uncompressed-tar",
        "temporal_windows": False,
        "max_samples_per_shard": max_samples_per_shard,
        "benchmark_digest": next(iter(benchmark_digests), ""),
        "config_digest": next(iter(config_digests), ""),
        "profile_digest": next(iter(profile_digests), ""),
        "splits": splits.to_mapping(),
        "sessions": [
            {
                "session_id": quality.session_id,
                "benchmark_digest": quality.benchmark_digest,
                "config_digest": quality.config_digest,
                "profile_digest": quality.profile_digest,
                "game_build": quality.game_build,
                "source_manifest_sha256": source_manifest_digests[quality.session_id],
                "aligned_frames": quality.aligned_frames,
            }
            for quality in sorted(
                qualities, key=lambda item: _session_key(item.session_id)
            )
        ],
        "shards": all_shards,
        "reports": {
            "quality.json": _sha256(root / "quality.json"),
            "quality.md": _sha256(root / "quality.md"),
        },
    }
    (root / "dataset-manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return manifest


__all__ = ["assign_session_splits", "build_dataset"]
