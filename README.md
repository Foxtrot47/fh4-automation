# FH4 automation foundation

This repository contains a safe, replayable foundation and synchronized
read-only demonstration recorder for offline and consenting-private-session
Forza Horizon 4 research. It includes strict FH4 Horizon UDP telemetry,
hood-camera capture, and physical XInput sampling. It never reads or injects
the game process and contains no controller-output or virtual-gamepad API.

## Setup

Python 3.12 and [uv](https://docs.astral.sh/uv/) are required.

```console
uv sync --dev
```

The benchmark manifest is
`configs/benchmark/horizon_festival_circuit.toml`: Horizon Festival Circuit,
stock 2017 Ford Focus RS, summer/clear/daytime, hood camera, full racing line,
ABS and traction control, automatic transmission, normal steering, and assisted
braking disabled.

## CLI

Validate the benchmark configuration:

```console
uv run fh4-agent validate-config configs/benchmark/horizon_festival_circuit.toml
```

Exercise the safe dry-run backend (disarmed is the default):

```console
uv run fh4-agent dry-run --config configs/benchmark/horizon_festival_circuit.toml --action '{"steering":0.25,"throttle":0.4,"brake":0}'
```

Capture files are versioned append-only streams of exact datagrams, receive
monotonic timestamps, and source addresses. They include checksums, so
truncation and corruption fail explicitly during offline inspection/replay:

```console
uv run fh4-agent capture-inspect recordings/session.fh4cap
uv run fh4-agent capture-replay recordings/session.fh4cap --decode
```

Record a bounded synchronized live session after enabling FH4 Data Out on UDP
port 5300. Hardware is opened lazily only by this command:

```console
uv run fh4-agent session-capture recordings/session \
  --config configs/benchmark/horizon_festival_circuit.toml \
  --game-build "FH4-Steam-build-label" \
  --max-frames 27000 --max-seconds 900 \
  --start-on-a-release --progress
```

With `--start-on-a-release`, the command opens a temporary read-only XInput
reader, waits for a physical A-button hold, and starts only after the release
edge. `--progress` writes an elapsed/remaining timer to stderr so the final JSON
on stdout remains machine-readable.

Validate a finalized session, replay all streams, or recover the valid tails of
an incomplete session without opening hardware:

```console
uv run fh4-agent session-inspect recordings/session \
  --config configs/benchmark/horizon_festival_circuit.toml
uv run fh4-agent session-replay recordings/session
uv run fh4-agent session-recover recordings/incomplete-session
```

Validate demonstration quality, optionally write JSON/Markdown reports, and
build deterministic session-level splits plus uncompressed tar shards. Report
and dataset output directories must not already exist:

```console
uv run fh4-agent dataset-validate recordings/demo-001 recordings/demo-002 \
  --config configs/benchmark/horizon_festival_circuit.toml \
  --report-dir data/reports/pilot
uv run fh4-agent dataset-build data/datasets/fh4-v1 \
  recordings/demo-001 recordings/demo-002 \
  --config configs/benchmark/horizon_festival_circuit.toml \
  --max-samples-per-shard 1024
```

Dataset splitting is deterministic 80/10/10 by complete session, never by
frame. Misaligned frames use a 33.334 ms bound; isolated failures are filtered,
while corrupt, stale, disconnected, overloaded, mixed-configuration, missing-
telemetry, or out-of-order sessions fail closed. Validation retains substantial
race-on segments across pause/resume boundaries, resets continuity and lap
state between them, and reports excluded pause/post-race or short transient
frames. Shard metadata excludes opaque Horizon bytes and applied controls while
retaining reviewed ego/slip/pose state.

The FH4-200 camera profile is fixed to a borderless 2560x1440 source, copied
DXGI frames, and 960x540 JPEG at 30 FPS. Physical input is read-only XInput slot 0;
all streams carry integer session-relative monotonic nanoseconds while source
and game clocks remain available separately. `dxcam`, NumPy, and Pillow are
lazy Windows-only adapters and are never accessed at import time.

The decoder accepts only tightly packed little-endian packets of exactly 323
or 324 bytes. All documented fields are typed; the 12-byte Horizon region and
optional 324th trailing byte remain opaque. Packet controls are exposed in the
normalized action convention, and `FH4TelemetryPacket.to_telemetry_sample()`
adapts ego telemetry to the shared `TelemetrySample` contract. UDP sockets are
created lazily by `UdpTelemetryReceiver`, never at import time.

## Validation

```console
uv run pytest
uv run ruff check .
uv run mypy src
```

No FH4 process, live network endpoint, virtual-controller driver, or physical
device is required for these checks.
