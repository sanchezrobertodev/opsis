# wan-core: your own RunPod endpoint for open Wan models

A RunPod serverless endpoint running ComfyUI with Wan 2.2 14B (text and image to video, Lightning 4-step),
Wan 2.2 Fun Camera (preset camera moves) and Wan-Move (drawn motion paths). The studio reaches it through the
`runpod_wan_core` provider (`apps/server/src/studio/providers/runpod_comfy.py`); the workflows are the
`workflows/comfy/runpod_*.json` templates.

## What's here

| File | Purpose |
|---|---|
| `Dockerfile` | `runpod/worker-comfyui:5.10.0-base` (ComfyUI 0.34.0, CUDA 12.8, torch 2.11) plus the files below. No custom nodes: every node the workflows use is built into ComfyUI 0.34. |
| `handler.py` | Replaces worker-comfyui's handler. Takes `{"workflow", "inputs": [{name, url or data}]}`, returns `{"outputs": [{node, filename, kind, mime, data}], "gpu", "seconds"}`. Also `{"action": "check"}` and `{"action": "fetch_models"}`. |
| `fetch_models.py`, `models.yaml` | The 15 model files (118 GB, exact sizes) and an idempotent downloader for the network volume. |
| `extra_model_paths.yaml` | Points ComfyUI at `/runpod-volume/models/<folder>` (upstream's file lacks `diffusion_models` and `text_encoders`). |

## Layout on RunPod

- **Network volume** `wan-core-models`, 130 GB, STANDARD, in **US-NE-1**: models at `/runpod-volume/models/...`.
  US-NE-1 has both GPU types below plus standard volumes. A volume pins the endpoint to its data center.
- **Endpoint** `wan-core`: queue-based, workers 0 to 2, GPU pools `BLACKWELL_96` (RTX PRO 6000, $3.49/h) and
  `ADA_80_PRO` (H100, $4.79/h), idle timeout 60 s, execution timeout 3600 s, FlashBoot on.
- Costs (2026-10-06): the volume is $0.07/GB/month ($9.10/month for 130 GB) whether or not you use it. Workers
  bill per second only while running, plus the 60 s idle tail. The first take after a pause also loads about
  35 GB of weights from the volume.

## Deployed (2026-10-06)

| Resource | Id | Notes |
|---|---|---|
| Image | `sanchezrobertodev/wan-core:0.1.0` | Docker Hub, public |
| Network volume | `w5wwmiagkn` (`wan-core-models`) | 130 GB STANDARD, US-NE-1; all 15 files fetched (14 min on a CPU pod) |
| Template | `hgugk70qn7` (`wan-core-0.1.0`) | serverless, 30 GB container disk |
| Endpoint | `ovktgiubxwld1j` (`wan-core`) | pools BLACKWELL_96 + ADA_80_PRO, workers 0 to 2, idle 60 s, timeout 3600 s |

First live take through the studio: Wan 2.2 14B Fast, text-to-video, 480p, 3 s. It took 92.7 s on an RTX PRO 6000
from a cold worker, including loading the text encoder and both experts from the volume. Cost: $0.090, against a
$0.065 estimate made for a warm worker.

All four workflows have now rendered through the studio (480p, 3 s, start frame from the first take):

| Model | GPU | Run time | Cost |
|---|---|---|---|
| Wan 2.2 14B Fast, text-to-video | RTX PRO 6000 | 92.7 s | $0.090 |
| Wan 2.2 14B Fast, image-to-video | RTX PRO 6000 | 93.5 s | $0.091 |
| Wan-Move, one path plus one pin | RTX PRO 6000 | 91.7 s | $0.089 |
| Wan 2.2 Fun Camera, pan right | H100 (the PRO 6000s were throttled) | 174.2 s | $0.232 |

Every run was a cold load of that workflow's weights. On this job the H100 was about twice as slow and 2.6 times
the price, so dropping the `ADA_80_PRO` pool is worth considering if waiting for a PRO 6000 is acceptable.

Known: the worker logs "You need pytorch with cu130 or higher to use optimized CUDA operations". The base
image's torch is cu128, so ComfyUI's comfy-kitchen CUDA kernels stay off. A cu130 build may run faster on
Blackwell hosts, which report CUDA 13.2.

## Build and push

```bash
cd infra/runpod/wan-core
docker build --platform linux/amd64 -t <dockerhub-user>/wan-core:0.1.0 .
docker push <dockerhub-user>/wan-core:0.1.0
```

On Docker Hub, the 15 GB of base layers are mounted from `runpod/worker-comfyui`, so the push uploads only
the few KB this image adds.

## Test locally (free, uses your GPU)

```bash
docker run --rm --gpus all \
  -v C:/Dev/AI/VideoStudio/ComfyUI_windows_portable/ComfyUI/models:/runpod-volume/models:ro \
  -v ./test_input.json:/test_input.json wan-core:local
```

With no RunPod job queue around, the RunPod SDK runs `/test_input.json` once and exits. The scratch test renders
the local Wan 2.2 5B workflow, which fits in 12 GB.

## Fill the volume

Either way skips files that are already complete, so rerunning is safe:
- a CPU pod in the volume's data center, using this image with
  `python -u /fetch_models.py --dest /runpod-volume/models` (pennies), or
- a job on the endpoint: `{"input": {"action": "fetch_models"}}` (bills GPU time while it downloads).

Check with `{"input": {"action": "check"}}`: it returns the GPU, ComfyUI version, and present/missing files.

## Wire it into the studio

Put the endpoint id in `.env` as `RUNPOD_WAN_CORE_ENDPOINT=...` next to `RUNPOD_API_KEY`, then restart the API
and worker. The models Wan 2.2 14B Fast and Wan 2.2 Fun Camera route there; add `wan-2.2-14b-fast@wan_core` to
`LIVE_ROUTE_ALLOWLIST` (mixed mode) or run `scripts/smoke_live.py --routes wan-2.2-14b-fast@wan_core --cap 0.50`.
