# Latest Session Work

## 2026-08-10 — FH4-200 accepted

Implemented, exercised on the live Windows/FH4 hardware path, and independently accepted the synchronized read-only session recorder.

### Delivered

- Bounded `session-capture` CLI wiring for DXcam, physical XInput slot 0, FH4 UDP telemetry, the session recorder, and deterministic total cleanup.
- Fixed 2560×1440 DXGI source profile with ordered four-worker resizing/JPEG encoding to 960×540 at a 30 FPS target.
- Read-only physical XInput sampling with raw state, normalized action labels, packet counters, connection/physical provenance, and explicit disconnect handling.
- Integer session-relative monotonic nanoseconds on every stream while preserving camera source/QPC timestamps and FH4 game timestamps separately.
- Exact 323/324-byte telemetry validation, binary datagram preservation, integer-nanosecond records, and JSONL alignment sidecars.
- Versioned CRC-protected JPEG chunks with 64-bit session/source clocks and backward reads for the earlier v1 32-bit source-clock format.
- Required-stream streaming SHA-256 manifest validation bound to session, benchmark, profile, metadata, and exact typed health counters without loading large recordings into memory.
- All-stream deterministic replay, strict inspect, and recoverable frame/JSONL/telemetry tails.
- Explicit camera/controller drop diagnostics, lossless telemetry overload faults, disconnect/stale events, and source-fault cleanup.
- DXcam 0.3.0 compatibility handling that avoids its comtypes double-release path; no controller-output API or device-write path was added.

### Software verification

- `uv run pytest -q`: 65 passed.
- `uv run ruff check .`: passed.
- `uv run mypy src`: passed.
- `uv lock --check`: passed.
- `uv build`: passed.
- Fresh Python 3.12 wheel install and imports outside the repository: passed.
- Focused adversarial lifecycle/cleanup rerun: 17 passed.
- Independent final software review: PASS with no blockers.

### Accepted live artifact

- Path: `recordings/fh4-200-hardware-20260810-230908` (local and Git-ignored).
- Session ID: `4ea69fae-5320-4df2-9bfb-6bcd29f9effc`.
- Five required manifest-owned streams were present and every independently recomputed SHA-256 digest matched.
- Vision: 341 valid 960×540 JPEG frames over 11.3346327 camera-source seconds, 29.9966 FPS average; representative first/middle/last frames showed coherent FH4 hood-view daytime racing.
- Controller: 1,500 connected physical samples over 11.984 seconds, 125.083 Hz; raw packet counter and full steering/throttle/brake activity were retained with no disconnect.
- Telemetry: 1,978 exact 324-byte packets over 11.984 seconds, 164.970 Hz, all from `127.0.0.1:49697`; binary payload/session timestamps matched every sidecar record, with no out-of-order game timestamp or estimated gap.
- Shared-session alignment: frame-to-controller p95 0 ms/max 16 ms; frame-to-telemetry p95/max 0 ms; no frame exceeded the 33.334 ms gate.
- Timelines were nondecreasing. Manifest health recorded zero drops, stale events, disconnects, writer faults, and overload failures.
- Strict readers and two complete replay passes agreed on 341 frame, 1,500 controller, and 1,978 telemetry events.
- Independent artifact review: PASS with no blockers.

### Important residuals

- The live metadata deliberately labels the FH4 Steam build as unverified. Artifact bytes cannot independently prove the exact build, stock tune, configured assists, or physical USB provenance.
- Windows clock quantization creates duplicate session timestamps. Alignment uses only the shared session clock; camera source/QPC and FH4 game clocks remain separate.
- Average camera rate passed, but camera-source interval jitter was 37.288 ms at p95 and 67.753 ms maximum.
- Three `_validation-*.jpg` representative previews were generated beside the accepted streams and are not manifest-owned.
- Azure T4 quota approval remains pending.
- The repository has no initial commit; project files remain untracked.

### Next step

Begin FH4-300: define dataset session validators and race segmentation, construct leakage-safe session-level splits and sequential shards, generate quality/baseline reports, then collect the planned 2–4 hours of demonstrations.
