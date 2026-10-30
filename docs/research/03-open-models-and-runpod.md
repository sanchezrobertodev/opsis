# Open-weights video stack and RunPod deployment (research as of 2026-10-06)

## Summary
- **Wan 2.5, 2.6, 2.7 and 3.0 are API-only.** The Wan-AI Hugging Face org stops at Wan 2.2, plus
  **Wan2.2-Animate-2-14B** (Aug 2026) and **Wan-Dancer-14B** (Jul 2026). Ignore SEO sites that claim Wan 3.0 is
  open.
- **Wan 2.2 (Apache 2.0) is the commercial-safe backbone.** Almost every Higgsfield-style feature has an
  Apache-licensed Wan module, all in one ComfyUI ecosystem.
- **Open models with native audio:** LTX-2.3/2.5 (free under $10M revenue), MiniMax H3 (excludes US/EU/UK/KR),
  MAGI-2 (needs 8 Hopper GPUs).
- **Licensing is the biggest trap** for anything commercial (see the traffic-light table below). For personal use
  this matters less, but track it per model.
- Speed figures are community reports and vary widely. Wan 2.2 14B at 720p on a 4090 ranges from ~2.7 to 12 min.
  Benchmark yourself.

## 1. Base text/image-to-video models
| Model | License | VRAM | ComfyUI | Notes |
|---|---|---|---|---|
| **Wan 2.2 T2V/I2V A14B** (MoE, 14B active) | Apache 2.0 | 80GB official; fp8 fits 24GB (experts load sequentially); GGUF Q4–Q5 runs at 480p on 12–16GB | Native + Kijai WanVideoWrapper | 4090 fp8 720p: 2.7–12 min. A step below Veo, Kling and Seedance |
| **Wan 2.2 TI2V-5B** | Apache 2.0 | 8GB min, 24GB for 720p | Native | Draft tier. The opt-in local route (`comfy_local`) |
| Wan 2.5–3.0 | API only | — | API nodes | RunPod resells Wan 2.6 at $0.10/s |
| **LTX-2.3** (22B, audio + video) | LTX-2 Community License (free under $10M) | fp8 12–24GB | Native | 8-step distilled; 5090: 720p, 481 frames in 82 s; ~5.7× faster than Wan, but less stable camera |
| **LTX-2.5** (2026-08-11) | same | int8 ~21.5GB; NVFP4 on Blackwell | Native day 0 | multi-shot, native 4K HDR up to 50fps, audio, IC-LoRAs (Restore, Upscaler, Layout-to-Render, Alpha, Ingredients) |
| HunyuanVideo 1.5 (8.3B) | Tencent (excludes EU/UK/KR) | 14GB with offload; GGUF ~8GB | Native | 1080p super-resolution model |
| MiniMax H3 / FastH3 | H3 license (excludes US/EU/UK/KR) | ~70GB bf16 | Native | strong, but territory-blocked |
| MAGI-2 preview | Apache 2.0 | 8× Hopper | none | not practical yet |
| Kandinsky 5 (Lite 2B / Pro 19B) | MIT | 12–24GB | custom | camera LoRAs |
| LongCat-Video, SkyReels V3 | MIT / Skywork | 24GB+ | community | long-video continuation; R2V |

**Speed-ups:**
- Wan2.2-Lightning and Distill-Models: 4 steps, no CFG, Apache.
- LightX2V framework: runs 14B in 8GB VRAM + 16GB RAM.
- rCM (NVIDIA): 2–4 steps.
- **TurboDiffusion:** Wan I2V 720p drops from 4549 s to ~38 s on a 5090 (CC BY 4.0).
- **Avoid CausVid** (non-commercial).

## 2. Video editing / v2v
| Model | License | Notes |
|---|---|---|
| **Wan VACE 14B / Wan2.2-VACE-Fun-A14B** | Apache 2.0 | Native `WanVaceToVideo`. Workhorse for inpaint, outpaint, ref swap, depth/pose restyle |
| **JoyAI-Video-Edit** (16B, Aug 2026) | Apache 2.0 | instruction-based (Kontext-like); 32GB; no ComfyUI nodes yet |
| Lucy Edit 1.1, Ditto | NC | avoid commercially |
| LTX-2.5 IC-LoRAs | LTX (under $10M) | restore, refine, layout-to-render, alpha |
| **ROSE** (object removal incl. shadows/reflections) | Apache 2.0 | top of a 2026 removal benchmark; small (Wan-Fun 1.3B) |
| VOID (Netflix) | verify | native in ComfyUI; also removes physical interactions |
| MiniMax-Remover | unclear | fast; leaves edge artifacts |
| EffectErase, ProPainter, DiffuEraser | NC / effectively NC | avoid |

## 3. Motion transfer / character animation
| Model | License | ComfyUI | Notes |
|---|---|---|---|
| **Wan2.2-Animate-14B** (move + mix/replace) | Apache | Native `WanAnimateToVideo` (+ DWPose, SAM2) | open equivalent of Higgsfield motion control and character swap |
| **Wan-Animate-2** (2026-08-07) | Apache | Native `WanAnimate2ToVideo` (ComfyUI ≥0.31) | uses the driving video directly (no pose extraction); multi-character; 10-step distilled version |
| SCAIL / SCAIL-2 | Apache / MIT | WanVideoWrapper | 3D-consistent pose, good for multiple people |
| Wan Fun Control | Apache | Native | canny, depth, pose, trajectory |
| Wan-Dancer-14B | Apache | todo | music → dance |

**Extractors and masks:**
- DWPose and ViTPose: Apache. SAM 2: Apache, the safe default for mask tracking.
- SAM 3: SAM License (commercial use allowed, with restrictions).
- Depth Anything V2/V3 and Video Depth Anything: **only the Small checkpoints are Apache**; larger ones are NC.

## 4. Object swap / subject replacement
- **VACE + SAM2 masks + reference image** (Apache): the most flexible approach.
- **Wan Animate "mix":** character swap that keeps the original motion and lighting.
- **Reference-to-video models (all Apache):** Phantom-Wan (multi-subject), MAGREF (any reference), Stand-In
  (identity, works with VACE), Lynx (single-image identity).
- Others: SkyReels-V3 R2V, LTX "Ingredients" IC-LoRA, HunyuanCustom (Tencent territories).

## 5. Camera control
| Tool | License | ComfyUI | Notes |
|---|---|---|---|
| **Wan Fun Camera** | Apache | Native + wrapper | pan, tilt, zoom presets |
| **Uni3C** | verify | WanVideoWrapper `WanVideoUni3C_ControlnetLoader` | copies camera motion from a reference video |
| ReCamMaster | verify | wrapper | re-shoots an existing video with a new camera path |
| ATI (any-trajectory) | verify | Native | — |
| **Wan-Move** | Apache | Native | point trajectories (Kling motion-brush class) |
| LTX-2 camera LoRAs | LTX | Native | dolly in/out/left/right, static |
| Higgsfield-style motion LoRAs | per LoRA | LoRA loader | likely train your own with musubi-tuner |
| Wan-Animate-2 viewpoint | Apache | Native | text-driven viewpoint |

## 6. Character consistency / identity
| Model | License | Notes |
|---|---|---|
| **Qwen-Image-Edit-2509/2511** | Apache 2.0 | best permissive multi-ref character edit; RunPod public endpoint $0.02/image |
| Qwen-Image 2.1 | Qwen Research License (NC) | — |
| **FLUX.2 klein 4B** | Apache 2.0 (the 9B is NC) | fast; ~8GB fp16 |
| FLUX.2 dev, FLUX.1 Kontext dev | FLUX non-commercial | needs a paid BFL license to serve |
| PuLID, InstantID, IP-Adapter FaceID, ReActor | InsightFace weights are NC | avoid or license |
| LoRA training: **musubi-tuner** (Wan, LTX-2.3, H3), **ai-toolkit** | Apache / MIT | ~12GB for image LoRAs, 24GB+ for video LoRAs |

## 7. World creation and 3D
| Model | License | Notes |
|---|---|---|
| **HY-World 2.0** (HY-Pano 2.0, WorldStereo 2.0, WorldMirror 2.0, WorldNav) | HY-World Community (territory limits) | text/image → 360 pano → 3DGS world or mesh; pod-class GPU |
| HY-WorldPlay (HY-World 1.5) | Tencent | real-time 24fps world model, keyboard/mouse; 5B low-VRAM variant |
| WorldMirror | Tencent | video → 3DGS in <2 s |
| **Matrix-Game 3.0** | MIT | most permissive Genie-like model; 720p at 40fps (5B) |
| **TRELLIS.2** (4B image→3D, PBR) | MIT | native in ComfyUI ≥0.34 |
| Hunyuan3D 2.1 | Tencent | last open version; 2.5–3.1 are hosted only |
| 360 pano LoRAs (Flux, Qwen 360, FLUX.2-klein pano) | inherit base license | — |
| **Marble (World Labs) API** | commercial | ~$0.12 (draft) to $1.20–2.48 per world; exports splats. AMD acquisition announced, a continuity risk |
| Tripo API | commercial | $0.10–0.30 per model |

## 8. Lipsync / talking avatars
| Model | License | Notes |
|---|---|---|
| **InfiniteTalk** (Wan 2.1, i2v + v2v dubbing) | Apache | 4090 OOMs at 720p; use lightx2v LoRAs; RunPod public endpoint $0.25 (480p) / $0.50 (720p) |
| LongCat-Video-Avatar 1.5 | MIT / CC BY (verify) | better long-sequence stability |
| MultiTalk, Wan2.2-S2V | Apache | — |
| **LatentSync 1.6** | Apache | best mouth-only dubbing for short clips |
| MuseTalk 1.5, EchoMimic V3, OmniAvatar, FantasyTalking | MIT / Apache | — |
| LivePortrait | MIT (but its InsightFace detector is NC) | swap in a MediaPipe detector |

## 9. Audio for faceless content
- **TTS:** Qwen3-TTS (Apache, 4–6GB), Chatterbox / Turbo / Multilingual (MIT), Kokoro (Apache), OmniVoice
  (Apache). Use with caution: Higgs Audio v2 (capped), IndexTTS2, Fish S2, F5-TTS, VibeVoice.
- **Foley:** LTX native audio; Stable Audio 3 Small-SFX (under $1M ARR); HunyuanVideo-Foley (Tencent). Avoid
  MMAudio and ThinkSound weights (NC).
- **Music:** **ACE-Step 1.5** (MIT, <4GB), YuE2 (Apache, native ComfyUI), HeartMuLa (Apache).
- **Captions:** WhisperX (BSD-2, word timestamps), Parakeet TDT / Canary.

## 10. Upscaling / interpolation
- **SeedVR2** 3B/7B (Apache): native ComfyUI; GGUF runs in ≤8GB. Best restoration quality, but slow.
- **FlashVSR v1.1** (Apache): streaming; ~17 FPS on an A100.
- LTX IC-LoRA upscaler; Hunyuan 1.5 super-resolution.
- **RIFE** (MIT) for interpolation; GIMM-VFI; Real-ESRGAN (BSD-3).

## License traffic light (for commercial use)
- **Green:**
  - Video: the Wan 2.1/2.2 family (VACE, Fun, Animate 1/2, S2V, Move, Dancer), Lightning/rCM, TurboDiffusion
    (CC BY), Kandinsky 5, LongCat, MAGI-2.
  - Identity, animation and swap: SCAIL, Phantom, MAGREF, Stand-In, Lynx.
  - Lipsync: InfiniteTalk, MultiTalk, LatentSync, MuseTalk, EchoMimic.
  - Editing and removal: ROSE, JoyAI-Video-Edit.
  - Image: Qwen-Image-Edit-2511, FLUX.2 klein 4B.
  - 3D and world: TRELLIS.2, Matrix-Game.
  - Upscale and interpolation: SeedVR2, FlashVSR, RIFE, Real-ESRGAN.
  - Audio: Qwen3-TTS, Chatterbox, Kokoro, ACE-Step, YuE2, WhisperX.
  - Masks, pose and depth: SAM 2, DWPose, small Depth Anything checkpoints.
- **Yellow:** LTX-2.x (under $10M), Stable Audio 3 (under $1M), Higgs Audio (capped), SAM 3, all Tencent HY
  models (territory limits), SkyReels.
- **Red:** MiniMax H3/FastH3 (territory), Lucy Edit, Ditto, EffectErase, ProPainter/DiffuEraser, MMAudio,
  ThinkSound, Fish S2, FLUX.2 dev / Kontext dev / klein 9B, Qwen-Image 2.1, InsightFace-based tools, CausVid.

## Deployment

### RunPod serverless pricing (Oct 2026)
| GPU | Serverless flex $/hr (≈ $/s) | Pod secure / community $/hr |
|---|---|---|
| RTX 4090 24GB | $1.10 (≈$0.000306) | $0.74 / from $0.34 |
| RTX 5090 32GB | $1.58 (≈$0.000439) | $0.99 / from $0.69 |
| L40 / L40S / 6000 Ada 48GB | $1.75 (≈$0.000486) | $1.09 / from $0.79 |
| A6000 / A40 48GB | $1.22 | — |
| A100 80GB | $2.72 (≈$0.000756) | $1.59 / from $1.19 |
| RTX PRO 6000 96GB | $3.49 | $2.09 |
| H100 80GB | $4.79 (≈$0.00133) | $2.89 / from $1.99 |
| H200 141GB | $5.93 | $4.59 |
| B200 180GB | $8.64 | $6.79 |

Billing is per second, covering start, execution and idle timeout. Image pulls are free. Active workers get a
~20–40% discount. Network volumes cost $0.07/GB/month (under 1TB).

### worker-comfyui ([repo](https://github.com/runpod-workers/worker-comfyui))
- Latest release is 5.11.0 (2026-09-21), pinned to ComfyUI 0.34; ComfyUI core is at 0.38.1. Pin your own
  version in the Dockerfile.
- **Submitting:** `POST https://api.runpod.ai/v2/{ENDPOINT_ID}/run` with
  `{"input":{"workflow":<API JSON>,"images":[{"name":"ref.png","image":"<base64>"}]}}`. Then poll
  `/status/{id}`, or use `/runsync`.
- **Outputs:** returned in `output.images[]` as base64, or as S3 URLs if `BUCKET_ENDPOINT_URL`,
  `BUCKET_ACCESS_KEY_ID` and `BUCKET_SECRET_ACCESS_KEY` are set.
- **Payload limits:** `/run` takes 10MB and `/runsync` 20MB. Results are kept 30 min and 1 min respectively.
  Pass large inputs as URLs.
- **Video gotcha:** upstream historically collected only `images`, but `VHS_VideoCombine` reports MP4s under
  `gifs`. Patch the handler to collect images, gifs, videos and audio.
- **Customizing:** install nodes with `RUN comfy-node-install comfyui-kjnodes comfyui-wanvideowrapper …`.
  Models go on a network volume at `/runpod-volume/models/...` or are baked into the image. Network volumes hold
  models only, not custom nodes.
- **Timeouts:** default execution timeout is 600 s, so raise it for video. Default idle timeout is 5 s. FlashBoot
  speeds up restarts.
- **Use queue-based endpoints.** Load-balancer endpoints cap processing at 5.5 min with 30MB payloads.

### Cold starts
A Wan 2.2 fp8 stack is ~35–40GB (estimate). Wan 2.2 took 144 s to cold-start vs 49 s for LTX-2.3 on a 5090.
Mitigations: bake models into the image (fastest), a network volume (easy, but pinned to one datacenter), RunPod
model caching (HF only, one model per endpoint), FlashBoot + 1 active worker at peak, and a 30–120 s idle timeout.

### RunPod public endpoints (zero setup)
- Wan 2.2 I2V 720p: **$0.30 per 5 s**; with camera-control LoRA: $0.35.
- InfiniteTalk: $0.25 (480p) / $0.50 (720p).
- Qwen Image Edit 2511: $0.02 per image.
- Chatterbox Turbo: $0.001/s.
- Closed models also offered: Wan 2.5/2.6, Kling, Seedance, Vidu.

### Pods vs serverless
Serverless wins below ~10 busy hours/day on an H100. Use pods for LoRA training, interactive ComfyUI workflow
building, world models, and batches.

### Local ComfyUI as an execution backend
- **API:**
  - Open a websocket at `ws://host:8188/ws?clientId=<uuid>`.
  - Submit with `POST /prompt` and body `{"prompt": <API JSON>, "client_id": "<uuid>"}`; it returns `prompt_id`.
  - Listen for `execution_start`, `executing`, `progress` and `executed`.
  - Fetch results with `GET /history/{prompt_id}`, then `GET /view?filename=&subfolder=&type=output`.
  - Other routes: `/upload/image`, `/interrupt`, `/free`, `/queue`, `/object_info`, `/system_stats`.
- **Security:** no authentication. Keep it on localhost, or behind Tailscale or Cloudflare Tunnel + auth.
- **What fits on 8–12GB consumer cards:**
  - Wan 5B; Wan 14B GGUF at 480p with Lightning; LTX GGUF at 540p.
  - Pre/post tools: SAM 2, DWPose, depth, RIFE, ESRGAN, FlashVSR, WhisperX, TTS, ACE-Step.
  - Blackwell supports NVFP4 where models ship it.

### Alternatives to RunPod
- **Modal:** H100 $0.001097/s, L40S $0.000542/s, $30/month free credit.
- **Replicate Cog:** private models are billed for setup and idle time.
- **fal serverless:** enterprise-gated.

## Recommended RunPod architecture
- **Local PC:** drafts, plus pre/post-processing, as an opportunistic worker. Not the main production path.
- **EP1 "wan-core"** (L40S; H100 for priority): Wan 2.2 A14B fp8, Lightning/Distill, VACE-Fun, Fun
  Control/Camera, Wan-Move, Uni3C, Phantom/MAGREF/Stand-In, Animate v1/v2, SCAIL, InfiniteTalk, S2V,
  WanVideoWrapper + KJNodes.
- **EP2 "ltx-av":** LTX-2.3/2.5 + IC-LoRAs + camera LoRAs, for native-audio clips.
- **EP3 "post"** (L40S / 4090): SeedVR2, FlashVSR, RIFE/GIMM, LatentSync, Stable Audio SFX.
- **EP4 "image":** Qwen-Image-Edit-2511, FLUX.2 klein, TRELLIS.2.
- **Pods:** LoRA training, world models, batches.

### Estimated cost per 5 s 720p clip (flex, warm worker)
| Workflow | GPU | Runtime | Cost |
|---|---|---|---|
| Wan 2.2 A14B, 4-step Lightning | L40S | 2.5–4 min | $0.07–0.12 |
| same | H100 | 1–1.7 min | $0.08–0.13 |
| Wan 2.2 full quality (20+ steps) | H100 | 10–15 min | $0.80–1.20 |
| TurboDiffusion Wan I2V | 5090 | ~40–60 s | ~$0.02–0.03 |
| LTX-2.x distilled, with audio | H100 / 5090 | 20–60 s | $0.01–0.08 |
| VACE edit / object swap | L40S | 3–5 min | $0.09–0.15 |
| Wan Animate / SCAIL | H100 | 2–4 min | $0.16–0.32 |
| InfiniteTalk 720p | H100 | 1.5–3 min | $0.12–0.24 |
| SeedVR2 3B / FlashVSR | L40S | 0.2–3 min | $0.01–0.09 |

Add ~$0.03–0.20 if a cold start is billed. For comparison, the public Wan 2.2 endpoint is $0.30 per 5 s and
Kling is ~$0.10/s.

### Uncertainties
1. Benchmark Wan Lightning, LTX-2.5 int8 and TurboDiffusion on L40S, H100 and PRO 6000 yourself.
2. Verify the licenses marked "verify": Uni3C, ReCamMaster, ATI, VOID, MiniMax-Remover, LongCat-Avatar 1.5,
   SkyReels, SAM 3.
3. Check whether upstream worker-comfyui now returns video outputs.
4. Decide target markets before using Tencent or MiniMax models.

## Key sources
[Wan-AI HF](https://huggingface.co/Wan-AI) · [Wan2.2 GitHub](https://github.com/Wan-Video/Wan2.2) · [ComfyUI Wan 2.2 docs](https://docs.comfy.org/tutorials/video/wan/wan2_2) ·
[Wan Animate docs](https://docs.comfy.org/tutorials/video/wan/wan2-2-animate) · [VACE docs](https://docs.comfy.org/tutorials/video/wan/vace) ·
[LTX-2.5](https://huggingface.co/Lightricks/LTX-2.5) · [TurboDiffusion](https://github.com/thu-ml/TurboDiffusion) · [LightX2V](https://github.com/ModelTC/LightX2V) ·
[ROSE](https://github.com/Kunbyte-AI/ROSE) · [JoyAI-Video-Edit](https://huggingface.co/jdopensource/JoyAI-Video-Edit) · [InfiniteTalk](https://github.com/MeiGen-AI/InfiniteTalk) ·
[SeedVR2 node](https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler) · [FlashVSR](https://github.com/OpenImagingLab/FlashVSR) · [TRELLIS.2](https://github.com/microsoft/TRELLIS.2) ·
[HY-World 2.0](https://github.com/Tencent-Hunyuan/HY-World-2.0) · [Matrix-Game](https://github.com/SkyworkAI/Matrix-Game) · [Marble pricing](https://docs.worldlabs.ai/api/pricing) ·
[RunPod pricing](https://www.runpod.io/pricing) · [RunPod serverless pricing](https://docs.runpod.io/serverless/pricing) ·
[worker-comfyui](https://github.com/runpod-workers/worker-comfyui) · [RunPod public endpoints](https://docs.runpod.io/public-endpoints/reference) ·
[ComfyUI server routes](https://docs.comfy.org/development/comfyui-server/comms_routes) · [Modal pricing](https://modal.com/pricing)
