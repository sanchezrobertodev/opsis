# Higgsfield live catalog: image, 3D and audio models (snapshot 2026-10-06)

Pulled read-only from Higgsfield's MCP `models_explore`. The video models are in `04-higgsfield-live-catalog-video.md`.

## Image (≈45 models)
| id | Provider | Purpose / key params |
|---|---|---|
| `soul_2` / `soul_v2` | Higgsfield | Soul 2.0: realistic UGC, fashion, characters; `soul_id` for a personalized identity; 1.5k/2k |
| `soul_cinematic` | Higgsfield | Soul Cinema: cinema-grade stills and concept art; Soul Cinema character ID |
| `soul_cast`, `cinematic_studio_soul_cast` | Higgsfield | consistent cinematic character identity (budget 10–500) |
| `soul_location`, `cinematic_studio_soul_location` | Higgsfield | environments and locations |
| `cinematic_studio_2_5`, `cinematic_studio_image` | Higgsfield | cinematic stills up to 4K; **camera_model_id, camera_lens_id, camera_focal_length_id, camera_aperture_id** |
| `soul_cinema_studio` | Higgsfield | style_id, custom_reference_id, enhance_prompt |
| `marketing_studio_image`, `ms_image`, `marketing_studio_2_image` | Higgsfield | product shots, posters, ads, marketplace; brand kits, product ids |
| `image_auto` | Higgsfield | auto-routes to the best image model by prompt intent |
| `autosprite` | Higgsfield | character → game sprite sheet (idle/walk/run/attack/jump, isometric directions) |
| `nano_banana`, `nano_banana_pro`, `nano_banana_2` (+inpaint mask), `nano_banana_2_lite` | Google | image_references; 1k/2k/4k |
| `gpt_image_2`, `gpt_image_2_5` (flare/sunburst, transparent bg), `openai_hazel` | OpenAI | text rendering, editing |
| `seedream_v4_5`, `seedream_v5_lite`, `seedream_5_0_flash`, `seedream_v5_pro` (remove_bg, is_inpaint) | ByteDance | instruction editing, up to 4–6K |
| `3d_jutsu_angles` | ByteDance | **3D Jutsu Angles, powered by Seedream 5.0 Pro**: turns a rendered 3D view into an AI image |
| `flux_2` (pro/flex/max), `flux_2_pro_outpaint`, `flux_3_image` (≤10 refs), `flux_kontext` | BFL | — |
| `kling_omni_image` | Kling | Kling O1 Image |
| `grok_image`, `grok_image_2_0` | xAI | — |
| `ideogram_4_5` (mask, magic prompt), `recraft_v4_1` (vector/utility) | Ideogram / Recraft | typography, logos, vector |
| `z_image` | Tongyi-MAI | fast, budget |
| `image_background_remover`, `outpaint`, `topaz_image`, `topaz_image_generative`, `image_decompose` (layers), `bytedance_image_upscale` | tools | — |

## 3D (17 models)
| id | Provider | Purpose |
|---|---|---|
| `sam_3_3d` | Meta | single image of an object → textured GLB |
| `sam_3_3d_body` | Meta | human body shape and pose → GLB with 3D keypoints |
| `image_to_3d`, `meshy_image_to_3d`, `meshy_v7_image_to_3d` | Meshy | image → GLB with texture, PBR, remesh, quad/tri topology, A/T-pose, **auto-rigging + 678-clip animation library** |
| `multi_image_to_3d`, `meshy_multi_image_to_3d` | Meshy | 1–4 views → GLB |
| `3d_rigging`, `meshy_rigging` | Meshy | rig an existing GLB and optionally animate it |
| `meshy_v6_text_to_3d` | Meshy | text → GLB (preview/full, lowpoly, rigging) |
| `meshy_v5_remesh`, `meshy_v5_retexture` | Meshy | retopology, retexture from text or image |
| `tripo_3d`, `tripo_h3_1_image_to_3d`, `tripo_h3_1_multiview_to_3d` | Tripo | text/image/multiview → GLB with PBR |
| `hunyuan3d_v3_image_to_3d`, `hunyuan3d_v3_1_text_to_3d` | Tencent | GLB, LowPoly/Geometry modes, PBR |

## Audio (8 models)
| id | Provider | Purpose |
|---|---|---|
| `seed_audio` | ByteDance | Seed Audio 1.0: TTS with voice/audio/image reference; speech rate, pitch, loudness |
| `qwen_audio_tts` | Alibaba | Qwen Audio 3.0 TTS Flash; natural-language instructions; preset or cloned voice |
| `elevenlabs_v4`, `elevenlabs_v4_turbo` | ElevenLabs | multi-voice dialogue (≤10 voices), 10k characters |
| `text2speech_v2` | Higgsfield | engine switch: elevenlabs / minimax / seed_speech / vibe_voice / cozy_voice |
| `sonilo_music`, `mirelo_text_to_audio`, `inworld_text_to_speech` | via fal | music, SFX, TTS (game pipeline) |

## Higgsfield MCP workflows (structure we can learn from)
`ad-multiplier`, `ads-studio`, `brand-asset-creation`, `character-sheet`, `faceless-video` (v2.4), `narration`,
`product-photoshoot`, `thumbnail-generation`, `ugc-video`, `video-editing` (Higgsedit), `video-montage`,
`website-builder-flow`.

## Higgsfield MCP tool concepts worth copying
- **Elements** (`manage_reference_elements`): reusable characters, environments and props per workspace.
  - Embed `<<<element_id>>>` in a prompt; the backend injects the image and rewrites the placeholder to
    `@element_name`.
  - Supported models: nano_banana_pro/2, gpt_image_2, seedream 4.5/5 lite, cinema studio image 2.5,
    cinema studio video v2/3.0, seedance_2_0, kling3_0 (needs an explicit start_image).
- **Motion control** (`motion_control`): Kling 3.0 Motion Control with `image_id`, `motion_video_id`, resolution
  720p/1080p, and `scene_control` (background from the image or the video).
- **3D Jutsu** (`scene_builder_3d_*`): projects with revisions; Python edits in server-side Blender with guards;
  asset search and import; render → `generate_angles` (Seedream 5 Pro); export GLB or .blend.
- **Shorts Studio:** restyles a 4–120 s source video into short-form clips using a style preset (CMS or user-made).
- **Presets:** Viral, Marketing Studio and Genjutsu galleries. A preset has an input schema, a readiness check and
  a preview.
