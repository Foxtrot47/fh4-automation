# Project Progress

## Active plan: FH4 race automation

**Status:** Active — FH4-000, FH4-100, and FH4-200 accepted; FH4-300 in progress
**Repository:** `E:/Software/Projects/fh4-automation`
**Route:** Heavy

## Goal

Deliver a safe, replayable, data-driven FH4 automation stack that progresses from synchronized human demonstrations to reliable solo laps, then to collision avoidance and overtaking in offline or consenting private sessions.

## Fixed scope and constraints

- Steam FH4, Xbox-compatible controller, hood camera.
- MVP benchmark: Horizon Festival Circuit; stock 2017 Ford Focus RS; summer, clear, daytime.
- Full racing line, ABS, TCS, automatic shifting, normal steering; assisted braking off.
- Public online/competitive use, anti-cheat bypass, process injection, and memory reading are prohibited.
- Initial data budget: 2–4 hours of human demonstrations.
- Gaming PC: Windows 11, Ryzen 5 7600X, RX 7700 XT, 32 GB RAM.
- CUDA/PyTorch training targets: local RTX 3050 4 GB/HDD as fallback; Azure `Standard_NC4as_T4_v3` (T4, Central India preferred) under subscription/quota review as the primary scalable option.
- One writer owns the active worktree at a time. Each phase is independently verified before the next phase begins.

## System acceptance criteria

1. Raw FH4 UDP packets, camera frames, and physical-controller samples are recorded with one monotonic timeline and replayable metadata.
2. Schema and session validators reject corrupt, stale, misaligned, mixed-configuration, or incomplete data.
3. Controller output defaults to neutral. After a lost heartbeat, focus, telemetry, capture, model failure, or explicit disarm is observed by the safety supervisor, the backend must receive neutral in the next control cycle and within 100 ms at p99 across at least 100 fault-injection trials; timing runs from the supervisor's monotonic fault-entry timestamp through completion of the backend neutral call.
4. Physical controller takeover wins over autonomous output within 100 ms at p99 across at least 100 trials; timing runs from receipt of the first physical sample beyond the configured takeover deadzone through completion of neutralization or handoff at the output backend. Raw timestamps and summary percentiles are retained.
5. No autonomous controls can be emitted before explicit arming, benchmark/config validation, and an operating-mode assertion of `offline` or `consenting_private`. Private mode additionally requires an explicit consent confirmation in the runtime session metadata.
6. Initial solo policy completes 20 consecutive benchmark laps without rewind, collision, or leaving the valid course, and achieves median lap time within 115% of the held-out human median. Targets may be tightened after the baseline is measured, never weakened to hide a defect.
7. Traffic features have separate gates and cannot regress the accepted solo policy: on a fresh 20-lap solo rerun they may not introduce a collision, rewind, or course departure, and median lap time may degrade by no more than 3% relative to the accepted FH4-600 artifact under the same benchmark.
8. Every model artifact records, at minimum: artifact-schema version, source commit and dirty flag, dependency-lock digest, dataset-manifest digest and split IDs, benchmark/config digest, model architecture identifier, random seed, training timestamp, framework/backend versions, train/validation/test metrics, export format/opset, export checksum, and runtime-compatibility result.

## Ordered phases

### FH4-000 — Foundation and contracts

**Outcome:** Runnable repository skeleton, locked environment, configuration contracts, typed sample/action/session schemas, benchmark manifest, CLI entry points, and test harness.
**Implementation:** `executor_luna`
**Verification:** `tester` after executor self-check
**Gate:** **Accepted.** Clean install and tests on Windows; schemas round-trip; no controller driver or game access required; dry-run is the only control backend. Final evidence: 18 focused acceptance tests and 29 full tests passed; Ruff, Mypy, locked sync, build, fresh wheel install, outside-repository CLI, armed dry-run (`device_writes=0`), and Git-ignore proofs passed.
**Dependencies:** None.

### FH4-100 — Telemetry acquisition and replay

**Status:** Accepted.
**Outcome:** Strict FH4 Horizon UDP decoding, lazy receiver, bounded exact-datagram capture, monotonic timestamps, continuity/drop diagnostics, synthetic golden fixtures, and deterministic offline replay. The decoder explicitly supports only the known tightly packed little-endian 323-byte layout and its 324-byte trailing-byte variant.
**Gate:** **Accepted.** Both packet variants and key field boundaries decode from synthetic golden packets; malformed flags, non-finite fields, incorrect lengths, corrupt/truncated captures, malformed metadata, invalid timestamp order, and hostile declared lengths fail explicitly. Modular uint32 diagnostics cover duplicates, wrap, stale/out-of-order packets, and configurable gap estimates. The bounded capture and strict inspect/replay CLI paths are device-independent and tested with fake sockets. Final evidence: 16 telemetry-focused tests and 45 full tests passed; Ruff, Mypy, locked sync, build, wheel install, outside-repository CLI, and import-time socket checks passed.
**Live validation:** A controlled Steam benchmark capture recorded 15,000 packets over 90.922 seconds; all were 324 bytes from one localhost source. Speed, RPM, steering, throttle, braking, lap, and race-position fields changed plausibly. Advancing game timestamps had 15–16 ms deltas with no gaps or out-of-order events; repeated timestamps reflected the approximately 165 Hz UDP send rate versus approximately 64 Hz game timestamp updates. The opaque 12-byte Horizon region was constant and the trailing byte remained `0x00`, so neither is assigned semantic meaning.
**Dependency:** FH4-000.

### FH4-200 — Vision, XInput, synchronization, and session recorder

**Status:** Accepted.
**Outcome:** Fixed-region hood-view capture, physical XInput sampling, monotonic alignment with telemetry, session metadata, recorder lifecycle, capture-health diagnostics, strict manifests, deterministic replay, and partial-session recovery.
**Fixed capture profile:** Borderless 2560×1440 game output; stored 960×540 JPEG frames at 30 FPS; 8BitDo Ultimate 2C adapter exposed as read-only XInput slot 0; exact FH4 UDP datagrams retained.
**Implementation:** Replaceable camera/controller/telemetry interfaces; lazy Windows adapters with no import-time device access; integer session-relative monotonic nanoseconds; source/game clocks retained separately; bounded queues with explicit drop/stale/disconnect/overload behavior; versioned CRC-protected JPEG and telemetry records; checksummed required-stream manifest bound to benchmark/profile/session metadata; bounded live CLI capture; strict inspect, all-stream replay, and tail recovery. Camera resize/JPEG work uses a bounded ordered four-worker pipeline. DXcam 0.3.0 cleanup avoids its comtypes double-release path. No controller-output API is present.
**Gate:** **Accepted.** Synthetic and adversarial tests cover lifecycle bounds, 64-bit timestamp precision, legacy frame-format reads, strict 323/324-byte telemetry, manifest completeness/types/digests, disconnect continuation, total cleanup, queue overload, replay, and recovery. Final software evidence: 65 full tests, Ruff, Mypy, locked dependency check, build, fresh Python 3.12 wheel install, outside-repository imports, and independent code review passed.
**Live validation:** Accepted local artifact `recordings/fh4-200-hardware-20260810-230908` (session `4ea69fae-5320-4df2-9bfb-6bcd29f9effc`). It contains 341 valid 960×540 frames at 29.9966 source-clock FPS, 1,500 connected physical XInput samples at 125.083 Hz, and 1,978 exact 324-byte telemetry packets at 164.970 Hz. Manifest digests, strict reads, sidecar/binary equality, and repeated deterministic replay passed. Shared-session nearest-neighbor alignment had camera-controller p95 0 ms/max 16 ms and camera-telemetry p95/max 0 ms, below the 33.334 ms gate. All timelines were nondecreasing and health reported zero drops, stale events, disconnects, writer faults, or overload failures. Representative frames showed coherent FH4 hood-view daytime racing.
**Residuals:** The artifact records an explicitly unverified game-build label; bytes alone cannot prove stock tune, exact assists, or physical USB provenance. Windows clock quantization produces duplicate session timestamps, and camera source intervals had 37.288 ms p95/67.753 ms maximum jitter despite the accepted 29.9966 FPS average.
**Dependency:** FH4-100.

### FH4-300 — Dataset quality and human baseline

**Status:** In progress — offline tooling complete; human collection and baseline pending.
**Outcome:** Record 2–4 hours of benchmark driving, including clean laps and recoveries; build session-level train/validation/test splits; generate sequential training shards and baseline lap/intervention metrics.
**Fixed decisions:** Record approximately 15-minute sessions. Assign whole sessions to deterministic 80/10/10 train/validation/test splits before sample or shard generation. Produce uncompressed WebDataset-style tar shards containing ordered JPEG/JSON pairs. Use bounded salvage: reject corrupt, mixed-config, disconnected, stale, writer-fault, or overloaded sessions; filter isolated frame-alignment failures, retain explicit camera-drop diagnostics, and reject sessions whose misalignment is no longer isolated. Keep camera source/QPC and FH4 game clocks separate from shared `session_ns`. Defer temporal window construction to training so raw aligned sequences remain reusable.
**Work packages:** `FH4-310` defines immutable dataset contracts, strict streaming session readers, exact binary/sidecar checks, frame alignment, and acceptance reasons. `FH4-320` adds race/lap segmentation resilient to duplicate timestamps, race resets, and partial laps plus candidate human-baseline metrics. `FH4-330` adds deterministic leakage-safe splits, bounded deterministic tar shards, digests, and dataset manifests. `FH4-340` adds quality reports, offline CLI integration, adversarial tests, live-artifact validation, packaging, and documentation.
**Software evidence:** FH4-310 through FH4-340 are implemented. Strict streaming validation binds source manifests and exact binary/sidecar telemetry, rejects fatal health/config/schema/continuity faults, reports independent per-stream rates and bounded alignment distributions, and performs 1% bounded frame salvage. Lap candidates exclude partial/reset laps and tolerate duplicates/uint32 wrap without claiming collision-free status. Deterministic whole-session splits, safe tar names, 1,024-sample shard bounds, model-safe ego/slip/pose features, checksummed shards/reports, CLI validation/build, and no-overwrite behavior are covered. Final local evidence: 8 focused and 73 full tests, Ruff, Mypy, lock check, diff check, build, fresh wheel install, and outside-repository imports passed. The accepted FH4-200 artifact validates as 341 aligned frames with 29.9718/125.0834/164.9700 Hz frame/controller/telemetry rates, p95 alignment 0/0 ms, max 16/0 ms, and zero estimated gaps/out-of-order events. A real build produced a 341-sample tar whose SHA-256 matched its manifest and excluded opaque/applied-control fields.
**Verification caveat:** Independent review found three blockers (insufficient safe telemetry features, missing alignment distributions, and uint32-wrap handling); all were corrected and covered by tests. The required post-fix independent rerun could not execute because both reviewer routes exhausted their Codex usage quota, so the main agent transparently performed the final software/live/wheel checks. This is a remaining verification gap, not evidence of a known production defect.
**Gate:** No session leakage across splits; every sample carries session/benchmark/config identity; corrupt or incompatible inputs fail closed; frame/controller/telemetry alignment uses integer `session_ns` with a 33.334 ms default bound; reports expose stream rates, controls, health, alignment, accepted/rejected samples, telemetry continuity, and candidate complete-lap coverage. Shard output is deterministic, checksummed, bounded-memory, and refuses to overwrite existing output. Full FH4-300 acceptance still requires the planned 2–4 hours of user demonstrations, held-out split coverage, a human baseline report, and a post-fix independent review when capacity is available.
**Roles:** The main agent owns scope and status. The built-in `worker` substitutes for unavailable configured `executor_luna`; the built-in `reviewer` substitutes for unavailable configured `tester`. The session-long read-only companion uses built-in `scout` because configured `explorer` is not registered.
**Dependency:** FH4-200 and user driving sessions.

### FH4-400 — Solo imitation policy

**Outcome:** Compact multimodal temporal policy with vision, telemetry, target-speed, steering, throttle, and brake outputs; CUDA training on the Azure T4 when quota permits, with the RTX 3050 fallback; reproducible evaluation and ONNX export.
**Gate:** Beats constant/last-action baselines on held-out sessions; no train/test leakage; fits within 4 GB VRAM using mixed precision and bounded batches; exported model matches PyTorch outputs within declared tolerances.
**Dependency:** FH4-300.

### FH4-500 — Safety supervisor and controlled runtime

**Outcome:** Real-time inference loop, dry-run shadow mode, arming state machine, heartbeat/focus/stream guards, physical takeover, action clamps/rate limits, telemetry logging, and replaceable virtual Xbox backend.
**Gate:** Fault-injection tests force neutral output; physical takeover and watchdog latency are measured; shadow-mode predictions are reviewed before the virtual backend is enabled. Driver installation requires a separate checkpoint and source-integrity review.
**Dependency:** FH4-400.

### FH4-600 — Solo closed-loop validation and correction

**Outcome:** Progressive rollout from low-speed tests to full laps, human interventions, recovery-data collection, DAgger-style correction rounds, and benchmark report.
**Gate:** System-level solo acceptance criterion passes from fresh starts over multiple sessions.
**Dependency:** FH4-500.

### FH4-700 — Opponent perception and collision avoidance

**Outcome:** Lightweight opponent/occupancy perception, temporal tracking, time-to-collision estimates, follow/slow/abort behavior, and traffic-specific datasets. Collision avoidance remains rule-supervised before learned tactical control is allowed.
**Gate:** Runtime configuration asserts `offline` or `consenting_private`; private sessions carry explicit consent confirmation. A validated traffic dataset must contain at least 60 minutes of controlled Drivatar driving, 100 close-follow interactions, and 30 near-miss/collision-recovery examples split by recording session. Replay metrics and controlled Drivatar trials demonstrate reduced collision risk without unsafe steering oscillation; uncertain perception causes follow/slow behavior, not a pass.
**Dependency:** Accepted FH4-600 baseline and the validated traffic dataset above.

### FH4-800 — Overtaking policy

**Outcome:** Tactical follow/pass-left/pass-right/abort planner trained from consenting human demonstrations and corrected in controlled trials. Low-level solo control remains separately testable.
**Gate:** Runtime configuration asserts `offline` or `consenting_private`; private sessions carry explicit consent confirmation. Before learned tactical control is enabled, the held-out scenario set must include at least 20 human-demonstrated left passes, 20 right passes, 20 follow/no-pass decisions, and 20 aborts, each with visibility/clearance labels. Pass attempts satisfy visibility/confidence/clearance rules, abort safely, and do not regress the solo gate.
**Dependency:** FH4-700 and the validated controlled pass/follow/abort scenario set above.

### FH4-900 — Optional assist reduction and packaging

**Outcome:** Evaluate removing racing line, TCS, and ABS one at a time. If ABS/TCS are disabled, introduce explicit wheel-slip-based low-level controls and new gates; this is distinct from merely learning to drive while the game assists are enabled. Package capture, training, deployment, and recovery documentation.
**Gate:** Each assist change has a new dataset and acceptance baseline; no combined assist removal without passing individual gates.
**Dependency:** FH4-800 or an explicit decision to stop at the assisted system.

## Validation strategy

- Unit: packet layouts, schemas, clocks/alignment, dataset filters, action transforms, safety transitions.
- Integration: UDP replay through recorder, recorded session through dataset builder, model export parity, runtime dry-run with fault injection.
- Hardware-in-loop: capture health, controller takeover, heartbeat neutralization, focus loss, and sustained inference latency.
- Gameplay: solo lap gates first; traffic/collision/overtaking gates only afterward.
- Evidence: exact commands, actual outputs, artifact paths, dataset/model hashes, and residual risks.

## Parallel boundaries

- Read-only research, model review, and independent validation may run concurrently.
- Source implementation uses one writer in the active worktree.
- Training experiments may run in parallel only when their configs/output directories are isolated and the active dataset manifest is immutable.
- Gameplay capture and controller-output testing do not run concurrently with tests that simulate the same devices or ports.

## Known risks and mitigations

- **No opponent coordinates in FH4 Data Out:** use fixed-camera vision and conservative temporal occupancy; never infer safe passing from ego telemetry alone.
- **Covariate shift in imitation learning:** collect recoveries and human interventions; use correction rounds before adding traffic.
- **RTX 3050 4 GB:** compact encoder, low resolution, mixed precision, gradient accumulation, export-oriented architecture.
- **HDD training host:** sequential shards, prefetch/cache, few loader workers; expect slower iteration and recommend SSD when practical.
- **RX 7700 XT Windows training uncertainty:** do not depend on unsupported Windows ROCm; benchmark only as an optional experiment.
- **Virtual-controller ecosystem risk:** abstract the backend, verify installer provenance, pin versions/hashes, and preserve dry-run operation.
- **Game/config drift:** bind every session and model to benchmark, assists, camera, resolution, car, tune, weather, and game-build metadata.
- **Private-session boundary:** require manual arming and documented consent; do not implement public matchmaking automation or anti-cheat evasion.

## Current blockers

- FH4-300 requires 2–4 hours of 15-minute human demonstration sessions and enough sessions for held-out split/baseline coverage; no full demonstration corpus exists yet.
- Post-fix independent FH4-300 software review is pending because available Codex reviewer routes exhausted their usage quota.
- Azure `Standard_NC4as_T4_v3` quota approval remains pending; this does not block local dataset work.
- The prevalence of the optional 323-byte packet variant and semantics of the opaque Horizon/trailing bytes remain unknown; those bytes must not become model inputs without separate evidence.
- RTX 3050 host OS/RAM/network details are not yet recorded; they do not block local dataset work.
- Virtual-controller driver selection remains deferred until the dry-run runtime is verified.

## Next action

Record one 15-minute benchmark pilot (maximum 27,000 frames/900 seconds), validate it immediately, and inspect its quality/lap report before continuing. If accepted, collect at least eight 15-minute sessions for the two-hour minimum (prefer ten or more for stronger 80/10/10 split granularity), mixing clean laps and deliberate but safe recovery demonstrations. Re-run independent software review when reviewer capacity returns, then build the immutable dataset and human baseline report. Azure quota remains independent of local dataset work.
