# Project Diary

## 2026-08-10 — FH4 live telemetry timing

- A Steam benchmark capture produced only 324-byte FH4 Data Out packets. The decoder must continue accepting both evidenced community variants, but the current game installation should be treated as 324-byte until another live capture proves otherwise.
- UDP packets arrived at approximately 165 Hz while the unsigned game timestamp advanced at approximately 64 Hz in 15–16 ms steps. Repeated `TimestampMS` values are therefore expected observations, not automatically packet duplication or a fault. Loss diagnostics operate on advancing game timestamps and report repeats separately.
- Pressing A to start the event was not observable as handbrake telemetry in the captured race stream. Race/session synchronization must use `CurrentRaceTime`, `IsRaceOn`, monotonic receive time, and motion/control transitions rather than assuming menu button input appears in Data Out.
- The 12-byte Horizon extension stayed constant and the 324th byte was always zero in this single capture. This is insufficient to assign semantics; both remain opaque.

## 2026-08-10 — FH4 synchronized recorder acceptance

- DXcam 0.3.0 manually calls `Release()` on comtypes-owned pointers, after which comtypes finalizers can release the same pointers again and crash with an access violation. The pinned adapter stops capture on the owning thread, marks the camera released, and lets comtypes perform its single owned finalization instead of invoking DXcam's double-release path. A finalized live session and separate hardware probes exited cleanly with this handling.
- Synchronous Pillow resize/JPEG encoding throttled live capture. A bounded ordered four-worker pipeline plus contiguous BGR-buffer decoding and BOX downsampling sustained a 29.9966 FPS camera-source average while preserving deterministic frame order and 960×540 JPEG output.
- Camera source timestamps exceed 32 bits. Frame format v2 stores both session and source clocks as uint64 while the reader retains v1 compatibility. Session telemetry uses a distinct integer-nanosecond record variant so values above `2**53` do not pass through float seconds.
- Camera QPC timestamps, controller/UDP source clocks, FH4 game timestamps, and shared session timestamps are intentionally separate. Cross-stream acceptance alignment must use only `session_ns`; source clocks remain for per-source diagnostics and must not be subtracted across clock domains.
- The accepted hardware artifact measured 341 frames at 29.9966 source FPS, 1,500 physical XInput samples at 125.083 Hz, and 1,978 exact telemetry packets at 164.970 Hz. Shared-session p95 alignment passed the 33.334 ms gate with no health faults or drops.

## 2026-08-10 — FH4 dataset foundation

- Demonstrations are recorded as approximately 15-minute sessions. Whole sessions are deterministically assigned 80/10/10 to train/validation/test before sample generation; temporal windows are deferred to training to prevent leakage and preserve reusable sequences.
- Dataset storage uses deterministic uncompressed USTAR shards with at most 1,024 ordered JPEG/JSON pairs. Every dataset manifest binds source session-manifest digests, shard digests, report digests, benchmark/config/profile identity, split IDs, and aligned sample counts.
- Bounded salvage rejects fatal health, corruption, mixed configuration, missing/out-of-order telemetry, or sessions with more than 1% misaligned frames. Isolated frame alignment failures are filtered and capture drops remain visible warnings rather than silently discarded evidence.
- Shard telemetry uses an explicit reviewed allowlist. Opaque Horizon/trailing bytes, applied controller bytes, AI-brake fields, and other label-leaking controls are excluded; ego motion, pose, wheel speed, tire slip, suspension, power/thermal, gear, and race state remain available.
- Candidate lap segmentation requires two observed lap boundaries, tolerates duplicate timestamps and uint32 wrap, discards race/time/lap regressions, and deliberately records collision status as unknown because Data Out cannot prove a clean lap.

## 2026-08-11 — Live dataset collection boundaries

- Capture can arm on a read-only physical XInput A hold and begin on the release edge. Progress is emitted only to stderr so the manifest JSON remains machine-readable on stdout.
- FH4 Rivals toggles `IsRaceOn` across pause/resume boundaries. Quality validation therefore treats substantial race-on intervals as separate reusable segments, resets lap and timestamp continuity state per segment, and excludes short transients and pause/post-race frames. A segment is substantial when it spans at least 2% of the telemetry session; segment and lap-candidate counts are explicitly bounded.
- Open race segments end at the last telemetry timestamp plus the 33.334 ms alignment allowance. Frame/controller tails beyond available telemetry are excluded rather than absorbed into the race or mislabeled as alignment failures.
- The first 20-lap live pilot contained Drivatar traffic and is retained only as pipeline evidence. The first accepted solo corpus session uses Rivals, contributes 26,159 frames (approximately 14.53 minutes), has no observed Y/rewind input, and passed independent code and artifact review.
