# Latest Session Work

## 2026-08-11 — FH4-300 solo collection started

FH4-310 through FH4-340 are published at commit `925c8aa8b470ed01e0ff3563695ef182e665a398`. Live collection then exposed and corrected capture-start and race-segmentation edge cases before the first production solo session was accepted.

### Live capture controls

- `session-capture --start-on-a-release` waits on the selected read-only physical XInput controller's A hold and begins after the release edge.
- Live capture auto-selects exactly one connected XInput slot, fails closed on zero or multiple controllers, accepts `--controller-slot 0..3` as an explicit override, and persists the resolved slot in backward-compatible session metadata.
- `--progress` writes an elapsed/remaining timer to stderr while preserving final JSON on stdout.
- The physical 8BitDo slot-0 path successfully armed and started the first accepted live captures. Session 002 used an operator-identified Xbox controller through an auto-selected slot 0; XInput state and slot metadata do not independently prove the controller model.
- Dataset validation retains substantial race-on segments across pause/resume boundaries, resets lap and game-clock continuity state per segment, and excludes short transients plus pause/post-race frames.
- Race-segment count and candidate-lap storage are bounded. Open segments end at the last telemetry timestamp plus the 33.334 ms alignment allowance, never at unrelated frame/controller tails.

### Live artifacts

#### Traffic pipeline pilot

- Path: `recordings/fh4-300-pilot-20lap-20260811-174054`.
- Complete 3.6 GB recording with zero health faults.
- The 20-lap race ended at 895.735 seconds; 1,918 post-race frames were excluded.
- 26,858 race frames, zero race alignment rejections, zero race timestamp gaps/out-of-order events, and 18 candidate complete laps with a 44.468-second median.
- Representative frames visibly contain a 12-car Drivatar field, so this artifact validates the pipeline but is excluded from the solo training corpus.

#### Rivals solo session 001

- Path: `recordings/fh4-300-rivals-solo-001-20260811-181528`.
- Session ID: `e4a61b8b-437e-4485-ba88-2f1b305bfa7b`.
- Complete 4.0 GB recording with zero drops, disconnects, stale events, writer faults, or overloads.
- Accepted report: `data/reports/fh4-300-rivals-solo-001-20260811-181528-segments-v4`.
- 26,159 usable frames (approximately 14.53 minutes), three isolated alignment rejections, and 820 excluded transient/pause frames.
- Selected race intervals: 7.609–539.812 seconds, 548.062–630.906 seconds, and 642.812–900.033 seconds.
- Selected telemetry: 143,881 packets; zero estimated missing or out-of-order timestamps.
- Sixteen conservative complete-lap candidates; median 44.754 seconds.
- Full controller scan: no Y/rewind (`0x8000`) records. Start/B activity brackets the two pause intervals.
- Four representative start/middle/end frames show solo hood-view Rivals driving with no cars or visible ghost. This cannot prove every frame is ghost-free.
- Count this as one FH4-300 solo corpus session. Recorded bytes still cannot prove exact tune, assists, or game build; collision status remains unavailable.

#### Rivals solo session 002

- Path: `recordings/fh4-300-rivals-solo-002-20260811-205759`.
- Session ID: `ac6a88e8-65bb-4f97-bd12-81bb39cdf242`.
- Complete 4.1 GB recording; manifest SHA-256 `583c862ae4bceafb30cdb79d9fad3a0e90fda3b2ea0fb02ae17744f873b7739a`.
- Operator identified the controller as an Xbox controller; auto-selection resolved XInput slot 0 and persisted it in the manifest. XInput cannot independently verify the model.
- Accepted report: `data/reports/fh4-300-rivals-solo-002-20260811-205759-segments-v1`.
- One continuous race segment from 0.016 through 900.018 seconds, with 26,979 usable frames, zero alignment rejections, and zero excluded non-race frames.
- 148,437 selected telemetry packets; zero estimated missing or out-of-order timestamps.
- Nineteen conservative complete-lap candidates; median 44.145 seconds.
- Full 112,500-record controller scan contained only button mask `0x0000`: no pause, A/B, or Y/rewind input during capture.
- Four representative start/minute-1/middle/end frames show solo hood-view Rivals driving with no cars or visible ghost. This cannot prove every frame is ghost-free.
- Count this as the second FH4-300 solo corpus session. Cumulative accepted corpus: 53,138 aligned frames, approximately 29.52 frame-equivalent minutes.

#### Preserved aborted retry

- `recordings/fh4-300-rivals-solo-002-20260811-205154` was stopped by the operator after approximately 5.5 minutes to remove per-second progress output. It has no manifest, is preserved unchanged, and is excluded from validation and corpus counts.

### Verification

- 17 focused checks passed before independent review.
- Final full suite: 80 tests passed; Ruff, Mypy, `uv lock --check`, package build, fresh-wheel live slot discovery, and `git diff --check` passed.
- Real `aligned_frames()` iteration exactly matched both accepted report counts.
- Independent code review initially found unbounded lap candidates and an open-segment tail error. Both were corrected with adversarial tests; rereview passed with no blockers.
- Independent artifact review passed Rivals session 001 for solo-corpus inclusion, subject to the provenance and visual-sampling caveats above.
- Independent controller-selection code review passed with no blockers. A boundary audit requested explicit-slot and legacy/new manifest integration checks; both were added and passed in the 26-test focused suite.
- Session 002 strict validation, complete controller scan, and representative visual inspection passed with the caveats recorded above.

### Remaining work

- Accepted solo corpus: approximately 29.52 frame-equivalent minutes of the 120–240 minute target; approximately 90.48 minutes remain to the minimum.
- Record at least seven more accepted approximately 15-minute sessions for the two-hour minimum; prefer eight more so ten whole sessions provide stronger deterministic 80/10/10 split granularity.
- Keep clean-lap and deliberate safe-recovery sessions identifiable, do not use rewind, and continue immediate validation plus representative-frame inspection after every capture.
- Build the immutable dataset and held-out human baseline only after collection is complete.
- Azure T4 quota approval remains pending but does not block local collection.
