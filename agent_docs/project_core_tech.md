# Project Core Technology

## Planned stack

- Python 3.12 with a locked environment and `uv`-managed dependencies.
- PyTorch/torchvision for model development and CUDA training.
- ONNX export plus CPU or ONNX Runtime acceleration for deployment, selected by benchmark.
- DXcam 0.3.0 (DXGI/NumPy) plus NumPy 2.2.6 and Pillow 11.3.0 for the fixed read-only hood-camera recorder; OpenCV is not a capture dependency.
- UDP receiver and explicit little-endian decoder for the FH4 Horizon Data Out packet.
- XInput sampling for physical-controller demonstrations.
- Parquet metadata plus sequential image/video or WebDataset shards for synchronized datasets.
- A virtual Xbox-controller backend behind an interface; initial implementation remains dry-run until safety gates pass.
- Pytest for unit/integration tests and recorded packet/session fixtures.

## Model strategy

The initial policy is a compact temporal multimodal network suitable for 4 GB VRAM:

- lightweight vision encoder;
- telemetry encoder;
- short temporal window using a GRU or temporal convolution;
- separate steering, throttle, brake, and target-speed heads;
- auxiliary racing-line/curvature and future-speed objectives when labels are reliable.

Behavioral cloning is followed by targeted human-correction data collection. Online reinforcement learning against FH4 is not planned because FH4 does not expose a fast, deterministic reset/step training API. Offline policy refinement can be evaluated only after the supervised baseline is reliable.

## Compute decision

Azure Machine Learning is the preferred managed training path. The workspace is `ml-workstation` in resource group `rg-me-2573` (`centralindia`), with intended scale-to-zero compute `fh4-t4` using `Standard_NC4as_T4_v3`; creation remains pending quota approval. PyTorch CUDA is the primary framework path.

The local RTX 3050 CUDA host is the fallback. Four GB of VRAM requires mixed precision, compact models, cropped/downsampled frames, gradient accumulation, and conservative batch sizes. HDD throughput is mitigated with sequential shards, prefetching, and local caching, but remains an iteration bottleneck.

Windows ROCm on the RX 7700 XT is not a committed training backend. AMD publishes limited Windows PyTorch ROCm packages, but the current Windows GPU matrix does not explicitly list RX 7700 XT. DirectML is suitable for experiments or deployment benchmarking but is not the primary training dependency.

## External evidence

- AMD Windows PyTorch ROCm matrix: https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/compatibility/compatibilityrad/windows/windows_compatibility.html
- PyTorch install selector: https://docs.pytorch.org/get-started/locally/
- Microsoft DirectML PyTorch documentation: https://learn.microsoft.com/windows/ai/directml/pytorch-windows
- FH4 Data Out packet reference used for planning: https://github.com/richstokes/Forza-data-tools/blob/master/FH4_packetformat.dat

These references must be rechecked when dependencies are pinned because hardware and framework support matrices change.
