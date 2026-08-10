# Project Structure

The repository uses the following modular boundaries:

```text
fh4-automation/
├── AGENTS.md
├── agent_docs/
│   └── workflow/
├── configs/
│   ├── benchmark/
│   ├── capture/
│   ├── model/
│   └── runtime/
├── docs/
├── scripts/
├── src/fh4_agent/
│   ├── telemetry/     # FH4 UDP packet decoding and typed samples
│   ├── capture/       # frame acquisition and capture metadata
│   ├── input/         # physical XInput demonstration sampling
│   ├── sync/          # monotonic-clock alignment and session recording
│   ├── dataset/       # validation, filtering, splits, and training shards
│   ├── models/        # multimodal temporal policies and losses
│   ├── training/      # training/evaluation/export workflows
│   ├── perception/    # later racing-line and opponent perception
│   ├── planning/      # later follow/pass/avoid tactical decisions
│   ├── runtime/       # real-time inference loop
│   ├── controls/      # dry-run and virtual-controller backends
│   ├── safety/        # arming, heartbeat, focus, takeover, neutralization
│   └── evaluation/    # replay, lap, intervention, and collision metrics
└── tests/
    ├── fixtures/
    ├── unit/
    └── integration/
```

## Ownership boundaries

- Telemetry, vision, and controller acquisition produce timestamped observations but do not choose actions.
- Dataset builders are offline and never control the game.
- Models produce requested actions and confidence/diagnostic outputs, never direct device writes.
- The runtime routes model requests through the safety supervisor.
- Only a controller backend may emit virtual-gamepad state; neutral is its fail-safe default.
- Traffic planning remains separate from low-level vehicle control.

FH4-000 through FH4-200 implement the foundation, telemetry, camera, XInput, synchronization, recording, replay, safety, and dry-run boundaries. Later dataset/model/runtime modules remain intentionally skeletal until their phases begin.
