# Project Overview

## Goal

Build a Forza Horizon 4 race-automation system that learns from synchronized human demonstrations, controller inputs, game-exported UDP telemetry, and a fixed game-camera feed. The system will progressively learn solo racing lines, braking points, throttle/traction behavior, collision avoidance, and opponent overtaking.

## Authorized scope

- Forza Horizon 4 Steam edition on Windows 11.
- Offline races and invitation-only private sessions whose participants consent to automation testing.
- Public online or competitive automation is explicitly out of scope.
- Initial benchmark: Horizon Festival Circuit, stock 2017 Ford Focus RS, summer/clear/daytime, hood camera.
- Initial assists: full racing line, ABS and traction control enabled, automatic transmission, normal steering, assisted braking disabled.
- Initial human-demonstration budget: 2–4 hours.

## Delivery strategy

Use a staged hybrid architecture rather than direct end-to-end reinforcement learning against the game:

1. Capture synchronized telemetry, vision, and human control labels.
2. Train a compact multimodal imitation policy for solo driving.
3. Correct distribution shift through human takeover/recovery data.
4. Deploy behind a deterministic safety supervisor and virtual-controller abstraction.
5. Add traffic perception, collision avoidance, and overtaking only after solo-driving gates pass.
6. Reduce assists one at a time in optional later stages.

## Hardware topology

- Gaming/capture/inference PC: AMD Ryzen 5 7600X, Radeon RX 7700 XT, 32 GB RAM, Windows 11.
- Preferred training service: Azure Machine Learning workspace `ml-workstation` in Central India, targeting scale-to-zero `Standard_NC4as_T4_v3` T4 compute after quota approval.
- Fallback training PC: NVIDIA RTX 3050 with 4 GB VRAM and HDD storage.
- Preferred framework/backend: PyTorch with CUDA on Azure T4 or the local NVIDIA fallback.
- Runtime inference backend will be selected by measured latency and stability: CPU or ONNX Runtime acceleration on the gaming PC.

## Safety principles

- No anti-cheat bypass, process injection, memory reading, network manipulation, or public-match automation.
- Control output remains neutral unless explicitly armed.
- Loss of process focus, model heartbeat, telemetry, or capture forces neutral controls.
- Physical human input has immediate priority over automation.
- Controller injection is isolated behind a replaceable interface and is introduced only after dry-run verification.
