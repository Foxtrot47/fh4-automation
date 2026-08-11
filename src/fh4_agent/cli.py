"""Command-line entry points for config validation and safe dry runs."""
# ruff: noqa: E501

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .capture import (
    CameraProfile,
    CaptureCoordinator,
    CaptureWriter,
    DXcamCamera,
    RecordingError,
    SessionRecorder,
    SessionTelemetrySource,
    inspect_capture,
    profile_digest,
    read_frame_chunks,
    recover_session,
    replay_capture,
    replay_session,
    validate_manifest,
)
from .config import ConfigError, load_config
from .contracts import (
    ActionValidationError,
    RequestedControlAction,
    SessionMetadata,
    neutral_action,
)
from .controls import DryRunControllerBackend
from .dataset import (
    MAX_SAMPLES_PER_SHARD,
    DatasetError,
    StreamingSessionAdapter,
    build_dataset,
    quality_document,
    write_quality_reports,
)
from .input import XInputController
from .safety import SafetySupervisor
from .sync import SessionClock
from .telemetry import (
    TelemetryDecodeError,
    TimestampContinuity,
    UdpTelemetryReceiver,
    decode_packet,
)

DEFAULT_CONFIG = "configs/benchmark/horizon_festival_circuit.toml"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fh4-agent")
    parser.add_argument("--version", action="version", version="fh4-agent 0.1.0")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser(
        "validate-config", help="load and validate a benchmark config"
    )
    validate.add_argument("path", type=str)

    dry_run = commands.add_parser(
        "dry-run", help="exercise the device-free controller backend"
    )
    dry_run.add_argument(
        "--config", default=DEFAULT_CONFIG, help="benchmark TOML/JSON path"
    )
    dry_run.add_argument(
        "--action",
        help="JSON object with steering, throttle, brake, and optional handbrake",
    )
    dry_run.add_argument(
        "--arm",
        action="store_true",
        help="explicitly arm this dry-run (still never accesses a device)",
    )

    capture_parser = commands.add_parser(
        "capture",
        aliases=["capture-live"],
        help="bounded live FH4 UDP capture (no replay or device control)",
    )
    capture_parser.add_argument("output", type=str)
    capture_parser.add_argument(
        "--packets", type=int, required=True, help="positive maximum packet count"
    )
    capture_parser.add_argument("--host", default="0.0.0.0")
    capture_parser.add_argument("--port", type=int, default=5300)
    capture_parser.add_argument(
        "--timeout-s", type=float, default=None, help="per-receive timeout in seconds"
    )
    capture_parser.add_argument(
        "--max-bytes",
        type=int,
        default=None,
        help="total capture-file byte bound, including header",
    )

    inspect_parser = commands.add_parser(
        "capture-inspect",
        aliases=["inspect-capture"],
        help="validate a strict FH4 capture and print its summary",
    )
    inspect_parser.add_argument("path", type=str)

    replay_parser = commands.add_parser(
        "capture-replay",
        aliases=["replay-capture"],
        help="strictly decode and deterministically replay an FH4 capture offline",
    )
    replay_parser.add_argument("path", type=str)
    replay_parser.add_argument(
        "--decode", action="store_true", help="decode packets and report field samples"
    )

    session_inspect = commands.add_parser(
        "session-inspect",
        aliases=["inspect-session"],
        help="validate a recording's manifest and recoverable frame chunks",
    )
    session_inspect.add_argument("path", type=str)
    session_inspect.add_argument("--config", default=DEFAULT_CONFIG)
    session_inspect.add_argument("--recover-tail", action="store_true")
    session_recover = commands.add_parser(
        "session-recover", help="recover all stream tails without a manifest"
    )
    session_recover.add_argument("path", type=str)
    session_replay = commands.add_parser(
        "session-replay", help="deterministically replay all session streams"
    )
    session_replay.add_argument("path", type=str)
    session_capture = commands.add_parser(
        "session-capture", help="bounded synchronized live session capture"
    )
    session_capture.add_argument("path", type=str)
    session_capture.add_argument("--config", default=DEFAULT_CONFIG)
    session_capture.add_argument("--game-build", required=True)
    session_capture.add_argument("--max-frames", type=int, required=True)
    session_capture.add_argument("--max-seconds", type=float, required=True)
    session_capture.add_argument("--host", default="0.0.0.0")
    session_capture.add_argument("--port", type=int, default=5300)
    session_capture.add_argument("--receive-timeout-s", type=float, default=0.25)
    session_capture.add_argument("--output-index", type=int, default=0)

    dataset_validate = commands.add_parser(
        "dataset-validate", help="strictly validate recording sessions offline"
    )
    dataset_validate.add_argument("sessions", nargs="+", type=str)
    dataset_validate.add_argument("--config", default=DEFAULT_CONFIG)
    dataset_validate.add_argument(
        "--report-dir", help="optional directory for quality.json and quality.md"
    )
    dataset_build = commands.add_parser(
        "dataset-build", help="build deterministic offline dataset shards"
    )
    dataset_build.add_argument("output", type=str)
    dataset_build.add_argument("sessions", nargs="+", type=str)
    dataset_build.add_argument("--config", default=DEFAULT_CONFIG)
    dataset_build.add_argument(
        "--max-samples-per-shard",
        type=int,
        default=MAX_SAMPLES_PER_SHARD,
        help="positive shard bound, at most 1024 samples",
    )
    return parser


def _action_from_argument(raw: str | None) -> RequestedControlAction:
    if raw is None:
        return neutral_action()
    try:
        decoded: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ActionValidationError(f"action is not valid JSON: {exc.msg}") from exc
    return RequestedControlAction.from_mapping(decoded)


def _run_validate(path: str) -> int:
    config = load_config(path)
    print(json.dumps(config.to_mapping(), indent=2, sort_keys=True))
    return 0


def _run_capture(
    output: str,
    packets: int,
    host: str,
    port: int,
    timeout_s: float | None,
    max_bytes: int | None,
) -> int:
    if packets <= 0:
        raise ValueError("--packets must be positive")
    receiver: UdpTelemetryReceiver | None = None
    writer: CaptureWriter | None = None
    try:
        receiver = UdpTelemetryReceiver(host, port)
        writer = CaptureWriter(
            output,
            metadata={"host": host, "port": port, "command": "capture"},
            max_records=packets,
            max_bytes=max_bytes,
        )
        for _ in range(packets):
            record = receiver.receive_once(timeout_s)
            writer.append_datagram(
                record.payload,
                record.timestamp_monotonic_s,
                record.source,
            )
        print(json.dumps({"output": output, "records": packets}, sort_keys=True))
        return 0
    finally:
        if writer is not None:
            writer.close()
        if receiver is not None:
            receiver.close()


def _run_capture_inspect(path: str) -> int:
    print(json.dumps(inspect_capture(path), indent=2, sort_keys=True))
    return 0


def _run_session_inspect(path: str, config_path: str, recover_tail: bool) -> int:
    directory = Path(path)
    if recover_tail:
        recover_session(directory)
    config = load_config(config_path)
    profile = CameraProfile()
    manifest = validate_manifest(directory / "manifest.json", config.identity, profile)
    frames = directory / "frames.fh4jpg"
    frame_count = sum(1 for _ in read_frame_chunks(frames))
    print(
        json.dumps(
            {
                "path": str(directory),
                "manifest": manifest.to_mapping(),
                "frame_count": frame_count,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _run_session_recover(path: str) -> int:
    print(json.dumps({"path": path, **recover_session(path)}, sort_keys=True))
    return 0


def _run_session_replay(path: str) -> int:
    counts: dict[str, int] = {}
    for event in replay_session(path):
        output = dict(event)
        jpeg = output.pop("jpeg", None)
        if isinstance(jpeg, bytes):
            output["jpeg_bytes"] = len(jpeg)
            output["jpeg_sha256"] = hashlib.sha256(jpeg).hexdigest()
        stream = str(output["stream"])
        counts[stream] = counts.get(stream, 0) + 1
        print(json.dumps(output, sort_keys=True))
    print(json.dumps({"path": path, "summary": counts}, sort_keys=True))
    return 0


def _run_session_capture(
    path: str,
    config_path: str,
    game_build: str,
    max_frames: int,
    max_seconds: float,
    host: str,
    port: int,
    receive_timeout_s: float,
    output_index: int,
) -> int:
    if max_frames <= 0 or max_seconds <= 0:
        raise ValueError("session bounds must be positive")
    if receive_timeout_s <= 0:
        raise ValueError("--receive-timeout-s must be positive")
    config = load_config(config_path)
    profile = CameraProfile(output_index=output_index)
    clock = SessionClock()
    session_id = str(uuid.uuid4())
    metadata = SessionMetadata.create(
        config.identity,
        game_build=game_build,
        started_monotonic_s=clock.origin_monotonic_ns / 1_000_000_000,
        session_id=session_id,
        profile_digest=profile_digest(profile),
    )
    recorder: SessionRecorder | None = None
    resources: list[Any] = []
    try:
        recorder = SessionRecorder(
            path,
            session_id=session_id,
            benchmark=config.identity,
            profile=profile,
            metadata=metadata,
        )
        camera = DXcamCamera(clock, profile=profile)
        resources.append(camera)
        controller = XInputController(clock)
        resources.append(controller)
        receiver = UdpTelemetryReceiver(
            host,
            port,
            monotonic=lambda: time.monotonic_ns() / 1_000_000_000,
        )
        resources.append(receiver)
        telemetry = SessionTelemetrySource(receiver, clock)
        resources.append(telemetry)
        coordinator = CaptureCoordinator(
            recorder,
            camera,
            controller,
            telemetry,
            max_seconds=max_seconds,
            max_frames=max_frames,
            receive_timeout_s=receive_timeout_s,
        )
        manifest = coordinator.run()
    finally:
        active_error = sys.exception()
        cleanup_errors: list[BaseException] = []
        for resource in reversed(resources):
            try:
                resource.close()
            except BaseException as exc:
                cleanup_errors.append(exc)
        if recorder is not None:
            try:
                recorder.close()
            except BaseException as exc:
                cleanup_errors.append(exc)
        if cleanup_errors:
            detail = "; ".join(repr(exc) for exc in cleanup_errors)
            if active_error is not None:
                active_error.add_note(f"session cleanup failure(s): {detail}")
            else:
                raise RuntimeError("session cleanup failed") from cleanup_errors[0]
    print(json.dumps(manifest.to_mapping(), indent=2, sort_keys=True))
    return 0


def _run_dataset_validate(
    sessions: list[str], config_path: str, report_dir: str | None
) -> int:
    config = load_config(config_path)
    qualities = [
        StreamingSessionAdapter(path, config.identity).validate() for path in sessions
    ]
    document = quality_document(qualities)
    if report_dir is not None:
        output = Path(report_dir)
        if output.exists():
            raise DatasetError(f"quality report output already exists: {output}")
        output.mkdir(parents=True, exist_ok=False)
        write_quality_reports(output / "quality.json", output / "quality.md", qualities)
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0 if all(quality.accepted for quality in qualities) else 2


def _run_dataset_build(
    output: str,
    sessions: list[str],
    config_path: str,
    max_samples_per_shard: int,
) -> int:
    config = load_config(config_path)
    manifest = build_dataset(
        output,
        sessions,
        config.identity,
        max_samples_per_shard=max_samples_per_shard,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


def _run_capture_replay(path: str, decode: bool) -> int:
    records = list(replay_capture(path))
    decoded = 0
    lengths: dict[str, int] = {}
    continuity = TimestampContinuity()
    for record in records:
        key = str(len(record.payload))
        lengths[key] = lengths.get(key, 0) + 1
        if decode:
            packet = decode_packet(record.payload)
            continuity.update(packet.timestamp_ms)
            decoded += 1
    summary: dict[str, object] = {
        "records": len(records),
        "decoded": decoded,
        "packet_lengths": lengths,
    }
    if decode:
        summary["continuity"] = continuity.diagnostics()
    print(
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _run_dry_run(path: str, raw_action: str | None, arm: bool) -> int:
    config = load_config(path)
    action = _action_from_argument(raw_action)
    backend = DryRunControllerBackend()
    supervisor = SafetySupervisor(backend)
    if arm:
        supervisor.arm(config)
    emitted = supervisor.submit(action)
    print(
        json.dumps(
            {
                "mode": "dry-run",
                "config": config.to_mapping(),
                "arming_state": supervisor.state.state.value,
                "requested_action": action.to_mapping(),
                "emitted_action": emitted.to_mapping(),
                "device_writes": backend.device_writes,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate-config":
            return _run_validate(args.path)
        if args.command == "dry-run":
            return _run_dry_run(args.config, args.action, args.arm)
        if args.command in {"capture", "capture-live"}:
            return _run_capture(
                args.output,
                args.packets,
                args.host,
                args.port,
                args.timeout_s,
                args.max_bytes,
            )
        if args.command in {"capture-inspect", "inspect-capture"}:
            return _run_capture_inspect(args.path)
        if args.command in {"capture-replay", "replay-capture"}:
            return _run_capture_replay(args.path, args.decode)
        if args.command in {"session-inspect", "inspect-session"}:
            return _run_session_inspect(args.path, args.config, args.recover_tail)
        if args.command == "session-recover":
            return _run_session_recover(args.path)
        if args.command == "session-replay":
            return _run_session_replay(args.path)
        if args.command == "session-capture":
            return _run_session_capture(
                args.path,
                args.config,
                args.game_build,
                args.max_frames,
                args.max_seconds,
                args.host,
                args.port,
                args.receive_timeout_s,
                args.output_index,
            )
        if args.command == "dataset-validate":
            return _run_dataset_validate(args.sessions, args.config, args.report_dir)
        if args.command == "dataset-build":
            return _run_dataset_build(
                args.output,
                args.sessions,
                args.config,
                args.max_samples_per_shard,
            )
    except (
        ConfigError,
        DatasetError,
        ActionValidationError,
        TelemetryDecodeError,
        ValueError,
        OSError,
        RecordingError,
    ) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    sys.exit(main())
