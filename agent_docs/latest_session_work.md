# Latest Session Work

## 2026-08-10 — FH4-300 offline tooling complete; collection pending

Committed and published the accepted FH4-000 through FH4-200 stack, then implemented the offline FH4-300 dataset foundation.

### Repository publication

- Public repository: `https://github.com/Foxtrot47/fh4-automation`.
- Initial commit: `5c9e1e0c5485c9cc0864921c8b5b78e96c743c68` on `main`.
- Local `main` tracks `origin/main`; recordings, data, build products, environments, caches, and subagent artifacts remain ignored.

### FH4-300 fixed decisions

- Record approximately 15-minute sessions; the 2–4 hour target therefore requires about 8–16 sessions.
- Assign complete sessions to deterministic 80/10/10 train/validation/test splits before sample generation.
- Write uncompressed WebDataset-style tar shards with at most 1,024 ordered JPEG/JSON samples.
- Use bounded salvage: reject corrupt, mixed-config, disconnected, stale, writer-fault, overloaded, telemetry-gap, or out-of-order sessions; report capture drops; filter isolated alignment failures; reject sessions over 1% misalignment.
- Align only on integer shared `session_ns` with a 33.334 ms bound. Preserve source/game clocks separately.
- Defer temporal-window construction to training.

### Delivered software

- Immutable dataset/sample/quality/split/lap contracts and safe session-ID rules.
- Strict bounded-memory session adapter that validates manifests, JPEGs, physical controller records, binary/sidecar telemetry equality, monotonic clocks, health, and game-timestamp continuity.
- Nearest controller/telemetry alignment with bounded deterministic p50/p95/p99/max diagnostics and exactness metadata.
- Explicit model-safe telemetry feature allowlist containing useful ego, acceleration, velocity, angular, pose, position, wheel-speed, tire-slip, suspension, power, thermal, gear, and race state while excluding opaque Horizon/trailing bytes and applied-control/AI fields.
- Conservative lap state machine that excludes partial/reset laps, accepts duplicate timestamps and uint32 wrap, and leaves collision status unknown.
- Deterministic leakage-safe whole-session splits, source-manifest provenance, deterministic USTAR shard metadata, streaming SHA-256, report digests, and refusal to overwrite outputs.
- JSON/Markdown quality reports with per-stream rates, controls, health, continuity, alignment, rejection counts, and candidate-lap median.
- Offline CLI commands: `dataset-validate` and `dataset-build`.

### Verification

- `uv run pytest -q tests/unit/test_dataset.py`: 8 passed.
- `uv run pytest -q`: 73 passed.
- Ruff, Mypy, `uv lock --check`, and `git diff --check`: passed.
- Build, fresh Python 3.12 wheel install, CLI help, and outside-repository dataset imports: passed.
- Accepted FH4-200 artifact: 341/1,500/1,978 aligned frame/controller/telemetry records; rates 29.9718/125.0834/164.9700 Hz; p95 alignment 0/0 ms; max 16/0 ms; zero estimated missing or out-of-order game timestamps.
- A real temporary build produced one 341-sample/682-entry tar shard, matching shard SHA-256, matching source-manifest SHA-256, and 61 safe telemetry fields with no opaque/applied-control keys.

### Verification caveat

Independent review found and precisely identified three implementation blockers; all were corrected and tested. Both available reviewer routes then exhausted their Codex quota before the required post-fix rerun. The main agent performed the final checks transparently. A post-fix independent review remains pending when capacity returns.

### Current blockers and next entry point

- The 2–4 hour human corpus and held-out human baseline do not exist yet.
- Azure T4 quota remains pending but does not block recording or dataset work.
- E: has approximately 229 GB free; the planned corpus is expected to consume roughly 30–60 GB at the accepted capture profile.
- Next: record one 15-minute pilot (`--max-frames 27000 --max-seconds 900`), immediately run `dataset-validate`, inspect complete-lap coverage, then continue to at least eight sessions (prefer ten or more for stronger split granularity).
