# wan-core: your own RunPod endpoint for open models

A RunPod serverless endpoint running ComfyUI with LTX-2.5 (the open default for text, image and first-and-last-frame
to video, with sound; from 0.4.0), Wan 2.2 14B (image to video, Lightning 4-step), Wan 2.2 Fun Camera (preset camera moves), Wan-Move (drawn motion paths), Wan 2.1 VACE (masked edits with a
reference picture, the Object swap route), Wan Animate-2 (motion transfer) and Uni3C (camera copied from a
reference clip, on the Wan 2.2 experts). The studio reaches it through the
`runpod_wan_core` provider (`apps/server/src/studio/providers/runpod_comfy.py`); the workflows are the
`workflows/comfy/runpod_*.json` templates.

## What's here

| File | Purpose |
|---|---|
| `Dockerfile` | `runpod/worker-comfyui:5.10.0-base` (ComfyUI 0.34.0) with torch 2.11 reinstalled for CUDA 13 (cu130, from 0.3.0), plus the files below. No custom nodes: every node the workflows use is built into ComfyUI 0.34. |
| `handler.py` | Replaces worker-comfyui's handler. Takes `{"workflow", "inputs": [{name, url or data}], "uploads": [{url, file_url}]}`, returns `{"outputs": [{node, filename, kind, mime, data or url}], "gpu", "seconds"}`. Also `{"action": "check"}` and `{"action": "fetch_models"}`. |
| `fetch_models.py`, `models.yaml` | The 20 model files (180.4 GB, exact sizes) and an idempotent downloader for the network volume. Gated repos (LTX-2.5) need `HF_TOKEN`. |
| `extra_model_paths.yaml` | Points ComfyUI at `/runpod-volume/models/<folder>` (upstream's file lacks `diffusion_models` and `text_encoders`; 0.4.0 adds `latent_upscale_models` for LTX-2.5's upscaler). |

## Layout on RunPod

- **Network volume** `wan-core-models`, 200 GB, STANDARD, in **US-NE-1**: models at `/runpod-volume/models/...`.
  US-NE-1 has the RTX PRO 6000 plus standard volumes. A volume pins the endpoint to its data center.
- **Endpoint** `wan-core`: queue-based, workers 0 to 2, GPU pool `BLACKWELL_96` (RTX PRO 6000, $3.49/h), idle
  timeout 60 s, execution timeout 3600 s, FlashBoot on. The `ADA_80_PRO` pool (H100, $4.79/h) was dropped on
  2026-10-08 (AMS-13): every H100 job was slower and dearer, so a take now waits for a PRO 6000 instead.
- Costs: the volume is $0.07/GB/month ($14.00/month for 200 GB, grown from 130 GB on 2026-10-08 and 180 GB on 2026-10-11) whether or not you
  use it. Workers
  bill per second only while running, plus the 60 s idle tail. The first take after a pause also loads about
  35 GB of weights from the volume.

## Deployed (2026-10-06)

| Resource | Id | Notes |
|---|---|---|
| Image | `sanchezrobertodev/wan-core:0.4.0` | Docker Hub, public; 0.4.0 (2026-10-11) maps `latent_upscale_models` for LTX-2.5, 0.3.0 (2026-10-08) runs torch on CUDA 13, 0.2.0 puts big results in upload slots; older tags still there |
| Network volume | `w5wwmiagkn` (`wan-core-models`) | 200 GB STANDARD, US-NE-1; the first 15 files fetched 2026-10-06 (14 min on a CPU pod), the VACE, Animate-2 and Uni3C groups 2026-10-08; the Wan t2v group swapped for the LTX group 2026-10-11 |
| Template | `yjkg0eh9ka` (`wan-core-0.4.0`) | serverless, 30 GB container disk; `1nphescjl4` (0.3.0), `2gfqvv1ajl` (0.2.0) and `hgugk70qn7` (0.1.0) are the previous ones |
| Endpoint | `ovktgiubxwld1j` (`wan-core`) | pool BLACKWELL_96 (ADA_80_PRO dropped 2026-10-08), workers 0 to 2, idle 60 s, timeout 3600 s |

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
the price, and a 720p take on 0.2.0 landed there too (192 s, $0.336), so the `ADA_80_PRO` pool was dropped.

Wan-Move pins (AMS-16, 2026-10-08): the "one path plus one pin" take, re-run twice with the same frame, paths and
seed ($0.147 each), measured by phase correlation and frame differences. None of the takes moved the camera (0 px
shift), and the pinned spot changed 3.1-3.2 / 255 between its first and last frame, the same as untouched background.
Packing the pin with a disc of still tracks made it worse: Wan-Move drew the patch on as a visible ring (9.4 / 255).
Asking for a locked-off camera in the prompt changed nothing measurable here, so neither change shipped.

CUDA 13 (0.3.0, AMS-12, 2026-10-08): the base image's torch is cu128, and ComfyUI only turns on comfy-kitchen's CUDA
kernels (fused rope/norm, int8 and quantized-weight ops) with a cu130 torch, so 0.3.0 reinstalls the same torch 2.11 for
CUDA 13 and the endpoint asks for hosts with CUDA 13 drivers (`minCudaVersion` 13.0; the PRO 6000 hosts report 13.2).
Same takes and seeds on an RTX PRO 6000, one after another on one worker (cost includes the 60 s idle tail):

| Take | 0.2.0 (cu128) | 0.3.0 (cu130) |
|---|---|---|
| Wan 2.2 14B Fast t2v, 480p 3 s, cold | 106.9 s, $0.162 | 81.1 s, $0.137 |
| Wan Animate-2, 480p, after it (its model loads cold) | 114.6 s, $0.169 | 65.7 s, $0.122 |
| Wan 2.2 14B Fast t2v, warm, new seed | - | 10.3 s, $0.068 |

The same seed twice is served from ComfyUI's cache (1.2 s), so warm runs need a new seed. Pictures from the same seeds
look the same (small shifts from the different kernels). Cold times include reading weights from the volume, which varies.

### VACE, Animate-2 and Uni3C (2026-10-08, AMS-11)

The three workflows use only nodes built into ComfyUI 0.34 (`WanVaceToVideo`, `WanAnimate2ToVideo` + `WanAnimate2Cache`,
`ModelPatchLoader` + `WanUni3CControlnetApply`), so the image didn't change: the graphs travel with each request and
`model_patches/` was already mapped. Images before 0.3.0 carry an older `models.yaml`, so their `check` action doesn't list
the new files. Live takes through the studio (480p, cold worker each time, RTX PRO 6000; cost includes the 60 s idle tail):

| Workflow | Input | Wall time | Cost | Result |
|---|---|---|---|---|
| Animate-2 (`wan-animate-2`) | portrait + 3 s dance clip, 49 frames | 201 s | $0.305 | the character performs the clip's motion |
| Uni3C (`wan-uni3c`), on both experts | keyframe + the color-bar test clip | 157 s | $0.267 | copied the test clip's picture: no camera move to copy |
| Uni3C, high-noise expert only | ramen-shop still + Seedance lighthouse push-in, 4 s | ~150 s | $0.174 | clean; a gentle push-in, weaker than the reference's |
| VACE (`wan-vace`) | color-bar test clip + matte, 69 frames | 180 s | $0.211 | edited only the masked bar, but no bricks |
| VACE via Object swap | Veo paper boat (4 s, 24 fps), SAM 3 matte | ~190 s | $0.190 | the boat became a yellow rubber toy; the rest untouched |

So Uni3C is applied to the high-noise expert only (the camera path is set early; the low-noise expert leaked the
guidance clip's picture). Clip inputs are re-timed to Wan's 16 fps before upload (the routes' `media_fps`), so the 81
frames cover up to 5 s of any clip; those slots take clips up to 5 s. (The takes above ran before that change, when a
24 fps clip's first 81 frames, 3.4 s, were used.)

The new groups were fetched with the 0.1.0 image on a CPU pod (4 files, 53.6 GB at 8-11 MB/s per file, about 55 min,
$0.15), the manifest passed in an env var:

```bash
echo "$MANIFEST_B64" | base64 -d > /tmp/new.yaml
WAN_CORE_MANIFEST=/tmp/new.yaml python -u /fetch_models.py --dest /runpod-volume/models
```

### LTX-2.5 (2026-10-10): the open default for text and image to video

LTX-2.5 replaces Wan 2.2 14B Fast for text, image and first-and-last-frame to video (registry `ltx-2.5`, route
`ltx-2.5@wan_core`, graphs `runpod_ltx25_{t2v,i2v,flf2v}`). It makes sound and speech in the same pass. The Wan 2.2
text-to-video group (31.0 GB) left `models.yaml`; Wan's image-to-video experts stay, since Uni3C runs on them. The
graphs come from ComfyUI's own LTX-2.5 templates (two stages: 8 steps at half size, the x2 latent upscaler, 3 steps at
full size; first-and-last-frame is one 8-step pass at full size), without the templates' Gemma E2B prompt enhancer.
ComfyUI 0.34 already has every node and the Gemma 4 text encoder, and `/prompt` on the local 0.34 accepted the graphs
apart from the absent weights. Weights: the `ltx` group, 39.7 GB (22B distilled int8, Gemma 4 12B int8, video and audio
VAEs, x2 spatial upscaler). An nvfp4 transformer (18.7 GB, Blackwell only) exists too; the int8 one is what the
templates use. Run times in the registry are estimates until the first live takes.

Rolled out 2026-10-11:

- The license is accepted on Hugging Face, and the downloader reads the token from the RunPod secret `hf_token`
  (`HF_TOKEN={{ RUNPOD_SECRET_hf_token }}` in the pod's env).
- The volume grew from 180 to 200 GB (+$1.40/month). It held exactly the 19 manifest files, with nothing stray.
- On a 4-vCPU CPU pod (`cpu3c`, $0.12/h, `python:3.12-slim` with the fetcher and manifest passed in env vars), the four
  Wan t2v files were deleted and the `ltx` group fetched: 39.7 GB in 2 min 37 s (60-157 MB/s per file), about $0.01.
  All 20 files are present at their exact sizes.
- `wan-core:0.4.0` was pushed (only the top layers uploaded), template `yjkg0eh9ka` (`wan-core-0.4.0`) made, and
  the endpoint switched to the image (release 7). Workers move to it as they cycle: a job can still land on a 0.3.0
  worker for a few minutes, which fails LTX-2.5 with "LatentUpscaleModelLoader ... not in []".

- First live take (the registry's smoke example, through the studio's wan-core adapter): text to video, 720p, 2 s, from a
  cold worker on an RTX PRO 6000. 72.3 s of run time, including loading the weights, so $0.128 with the idle tail
  against the $0.163 estimate. The clip is 1280x704, 24 fps, 49 frames, with an AAC stereo track (quiet lake ambience,
  -50 dB mean). The first try landed on a 0.3.0 worker before the rollout finished and failed at validation for $0.08.

Left: deploy the studio (merge, then Deploy on the Hub).

## Big results (0.2.0)

RunPod refuses very large job results, so the handler returns each output inline as base64 only while it is small.
With each job the studio sends one fal storage upload slot per output the workflow saves (`uploads`: a one-file
PUT link and the address it will be served at, kept a day; free to make, needs `FAL_KEY`). An output over
`WAN_CORE_INLINE_MB` (5) is put there and comes back as `url`, and the adapter ingests it from that link at once.
If an upload fails, say because the link lapsed while the job queued, the output comes back inline if the whole
result is still under `WAN_CORE_MAX_OUTPUT_MB` (20); otherwise the job fails with a readable message. Without
`FAL_KEY` no slots are sent and the 20 MB inline limit applies. Images before 0.2.0 ignore `uploads` and always
return inline.

Checked 2026-10-08: a Wan 2.2 14B Fast take at 720p, 5 s through the studio on 0.2.0 (H100, 192 s from a cold
worker, $0.336) came back inline as it should at 1.9 MB; the same file put through the handler's upload code into a
real slot was ingested byte for byte from its link. Wan clips are small (0.3-1.9 MB for 3-5 s), so uploads are for
longer or higher-quality outputs. Slots took uploads 1, 15, 40 and 70 minutes after they were made; fal doesn't document
how long they last, which is why a failed upload falls back inline.

## Build and push

```bash
cd infra/runpod/wan-core
docker build --platform linux/amd64 -t <dockerhub-user>/wan-core:0.3.0 .
docker push <dockerhub-user>/wan-core:0.3.0
```

On Docker Hub, the 15 GB of base layers are mounted from `runpod/worker-comfyui`, so the push uploads only
what this image adds (the CUDA 13 torch layer, a few GB, from 0.3.0).

## Test locally (free, uses your GPU)

```bash
docker run --rm --gpus all \
  -v C:/Dev/AI/VideoStudio/models:/runpod-volume/models:ro \
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
and worker. LTX-2.5 and the Wan models route there; add their `@wan_core` routes to
`LIVE_ROUTE_ALLOWLIST` (mixed mode) or run `scripts/smoke_live.py --routes ltx-2.5@wan_core --cap 0.50`.
