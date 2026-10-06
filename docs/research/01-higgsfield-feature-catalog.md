# Higgsfield AI: video feature catalog (research as of 2026-10-06)

Method: Higgsfield's blog, changelog (covering 2026-06-02 to 10-05), help center, GitHub CLI and third-party reviews,
plus a read-only look at Higgsfield's MCP catalog. Most `higgsfield.ai` app pages render client-side and returned
empty to fetches. Credit costs are point-in-time samples (1 credit ≈ $0.05). The platform changes weekly.

## The core pattern
Higgsfield owns little video-model IP: **DoP** (image-to-video camera/VFX model, Mar 2025), the **Soul** image
family (Soul 2.0, Soul Cinema, Soul Cast, Soul Location, Soul ID), **Popcorn** (storyboard images) and **Speak**.
Everything else is either a third-party model in a picker or a "studio" (workflow + preset UI) on top of
third-party models:
- **Seedance (ByteDance)** is now the backbone. Cinema Studio 4.0 runs on Seedance 2.5, and talking avatars use
  Seedance 2.0/2.5.
- **Kling** powers Motion Control, Recast and the editing tools.
- **Gemini Omni Flash** powers Explainer and probably Shorts Studio.

Company context: $400M Series B at a $5.4B valuation (Aug 2026), $700M annualized revenue, 30M users, and a
pivot toward enterprise marketing ([TechCrunch](https://techcrunch.com/2026/08/17/higgsfield-raises-400m-series-b-quadrupling-its-valuation-in-8-months-to-5-4b/)).
Best primary source: [changelog](https://higgsfield.ai/creator-hub/changelog).

---

## 1. Create Video (text-to-video, image-to-video, start/end frame) and the model picker
UI flow:
1. Type a prompt in the prompt bar (optional "enhance").
2. Pick a model.
3. Set per-model controls:
   - start frame, end frame, or both
   - reference images or `@elements`
   - duration, resolution, aspect ratio (9:16, 16:9, 1:1, 4:3, 3:4, 21:9 depending on model), audio toggle
   - camera presets (DoP) or `#tags` (Cinema Studio)
4. Generate. The cost is shown on the Generate button.

| Family | Variants on Higgsfield | Key specs |
|---|---|---|
| Seedance | 2.5, 2.5 Edit, 2.5 Draft, 2.0, 2.0 Fast, 2.0 Mini, 1.5 Pro (legacy) | **2.5:** 4–30 s; 480p/720p/1080p native (4K by upscale); 9:16 to 21:9; native audio (ambience, foley, score); refs up to 30 images + 10 video + 10 audio; edit modes (region, Draw to Video, Extend/Transition/Reverse). **2.0:** 4–15 s; 480p–4K; 9 img + 3 vid + 3 aud refs; "High Bitrate". Ref roles `@character`, `@style`, `@motion`, `@audio` |
| Kling | 3.0, 3.0 Turbo, 3.0 Omni, 3.0 Omni Edit, 3.0 Motion Control, 2.6 (+MC, Lipsync), O1 Video/Edit, 2.5 Turbo, 2.1, Avatars 2.0 | **3.0:** 3–15 s; 720p/1080p/4K; start/end frames; multi-shot (up to 5 shots, Auto or Custom, 15 s total); tagged Elements; audio toggle |
| Google | Veo 3.1, Veo 3.1 Lite, Veo 3, Gemini Omni Flash, Gemini Omni 1.1 Flash | **Veo 3.1:** 4–8 s, up to 4K, ≤3 refs, first/last frame. **Omni 1.1:** first/last frame, 360p draft mode, ≤10 s |
| Wan | 3.0, 3.0 Prime, 2.7, 2.6, 2.5 (+Speak), 2.2 Animate | **3.0:** ≤30 s, multi-subject consistency, audio. **2.7:** first/last frame, 2–15 s |
| MiniMax | Hailuo 02, 2.3, H3, H3 Max | **H3 Max:** ≤30 s at 768p |
| xAI | Grok Imagine Video / Video 1.5 | t2v, i2v, reference-to-video (≤7 refs), extension, sound, 480p–1080p, 1–15 s |
| Others | LTX 2.5 Fast/Pro, PixVerse 6, FLUX 3 Video Edit | — |
| Higgsfield-native | DoP (Lite/Turbo/Standard), Cinema Studio video (v2, 2.5, 3.0, 3.5, 4.0), Marketing Studio video, Genjutsu | **DoP:** i2v only; 3 or 5 s; seed/steps controls |

Notes:
- **Sora 2 / Sora 2 Pro were removed.** The API shut down 2026-09-24 ([migration guide](https://higgsfield.ai/blog/sora-api-migration-guide)).
- Higgsfield API per-second prices: Kling 2.5 $0.042, Kling 2.6 $0.07, Seedance 2.5 $0.0738, Kling 3.0 $0.112,
  PixVerse 6 $0.115, MiniMax H3 $0.13, LTX 2.5 Pro $0.17, Wan 3.0 $0.20 ([API blog](https://higgsfield.ai/blog/higgsfield-api)).
- Credit samples:

  | Model | Clip | Credits |
  |---|---|---|
  | Seedance 2.0 | 5 s | 23 at 720p / 45 at 1080p |
  | Kling 3.0 | 5 s | 10 at 720p / 12.5 at 1080p |
  | Wan 2.7 | 5 s | 8 at 720p / 13 at 1080p |
  | Cinema Studio | 5 s | 25 at 720p / 50 at 1080p |
  | Seedance 2.5 | 10 s | 30 at 480p / 70 at 720p / 120 at 1080p |
  | Kling 3.0 | 15 s | 30 at 720p / 37.5 at 1080p / 90 at 4K |
  | Veo 3.1 | 4 s | 29 |
  | DoP Lite / Standard | 3 s, 720p | 3 / 7 |

## 2. Edit Video (video-to-video)
There is no single editor; each edit is a separate model or tool.
- **Kling O1 Edit:** 3–10 s input, ≤4 image refs. Changes background, grade, lighting or outfits, transfers
  camera motion, and follows text commands like "remove bystanders".
- **Kling 3.0 Omni Edit** (std/pro/4K): object removal or swap, background swap or removal, restyle.
- **Seedance 2.5 Edit:** prompt edits or "Draw to Edit" (mark regions on a frame). ≤30 s input, ≤50 refs,
  ~90 credits for 10 s at 1080p.
- **Gemini Omni Flash "edit by talking":** locks the footage while adding characters, swapping props, changing
  materials or adding FX. Clips run 6–10 s.
- **Cinema Studio 4.0 Edit:** regional edits plus forward/backward Extend, up to 30 s.
- **Video Relight + Color Palette (HEX)**, standalone since 2026-08-21, 135 credits per 15 s:
  - Relight presets: Practicals, Silhouette, Contre-jour, Soft Cross, Window, Overhead Fall, Auto.
  - Custom: 2 lights with color, brightness, diffuse and angle.
  - Color Palette: 50+ grading presets plus match-to-reference.
- **Plugins** (Premiere, After Effects, Resolve): Edit Video, Draw to Edit, Reframe, Remove Background, Upscale,
  Color Match.
- No Runway Aleph on Higgsfield.

## 3. Camera controls (DoP presets, Cinema Studio moves)
**DoP library** ([/camera-controls](https://higgsfield.ai/camera-controls)), 65 moves:

General, Eyes In, Bullet Time, Aerial Pullback, Arc Left/Right, BTS, Buckle Up, Car Chasing, Car Grip,
Crane Down/Up/Over The Head, Crash Zoom In/Out, Dolly In/Out/Left/Right, Dolly Zoom In/Out, Double Dolly,
Dutch Angle, Eating Zoom, Fisheye, Flying Cam Transition, Focus Change, FPV Drone, Glam, Handheld,
Head Tracking, Hero Cam, Hyperlapse, Incline, Jib Up/Down, Lazy Susan, Low Shutter, Mouth In, Object POV,
Overhead, Pan Left/Right, Rapid Zoom In/Out, Road Rush, Robo Arm, Snorricam, Static, Super Dolly In/Out,
Through Object In/Out, Tilt Up/Down, Timelapse Glam/Human/Landscape, Whip Pan, Wiggle, YoYo Zoom, 360 Orbit,
3D Rotation, Zoom In/Out.

Categories: Effects, Basic, Epic, "Catch the Pulse", **Mix** (combines presets), New/Trending.
Input is a single keyframe; output is 3 or 5 s.

**Cinema Studio camera moves:**
- 2.5: 18 Director-panel moves (Static, Handheld, Zoom, Follow, Pan, Tilt, Orbit, Dolly, Jib, Drone, 360 Roll).
- 3.5: 10 "MoveSets".
- 4.0: 30+ presets (POV, Robot Arm, Helicopter…) as `#tags`, stackable per shot. `/create/video` claims up to 3
  simultaneous moves.

**Other apps:**
- Camera Motion / Angles 2.0 / Shots / Zooms: new angles or 9 shots from one image.
- Transitions: two clips (≤5 s each) morphed into one (≤15 s).

## 4. Motion Transfer (a reference video drives a character image)
1. **Higgsfield Animate / Recast** (late 2025): Wan 2.2 Animate, with Animate and Replace modes.
2. **Kling Motion Control (2.6 → 3.0):**
   - Reference clip: 3–30 s, one clear subject, head and body visible, no cuts.
   - Inputs: the reference clip plus a character image.
   - Output: 720p or 1080p.
   - Orientation: "Matches Video" or "Matches Image".
   - Scene source: background from the video or from the image. Optional prompt.
   - Reported cost: ~15 credits/s Std, 20 credits/s Pro.
3. **Genjutsu Motion Transfer** (2026-09-01): keeps the motion, camera and timing of a 4–30 s clip and rebuilds
   the cast and location from ≤30 reference images.

## 5. Object Swap and Restyle: Higgsfield Genjutsu
Modes:
- **Motion Transfer.**
- **Object Swap:** replaces one character, outfit, product or prop; camera, lighting and motion stay as filmed.
- **Restyle** (2026-09-30): 20+ animation styles, or a style from your own reference.

Specs:
- Inputs: one 4–30 s clip, ≤30 refs, an optional prompt, and 30+ presets (community "trending" feed).
- Output: ≤1080p.
- Credits for 15 s: 40 at 480p, 104 at 720p, 144 at 1080p.

The underlying model is not disclosed. The MCP routes it through `generate_video` with
`hf_mult_motion_control` / `hf_mult_replace_object`, likely a wrapper over Kling Omni or Seedance-class editing.
Related tools: Ad Multiplier (one ad becomes many variants via swaps), Recast, Face Swap.

## 6. Character creation
- **Soul ID:**
  - Trains on 20–80 photos in minutes to ~10 min, for ~25 credits.
  - Works across the Soul family and carries into Seedance video as an Element.
  - One person per Soul ID; it can't be exported.
- **Elements:** reusable Characters, Locations and Props, used via `@tags` in Cinema Studio, Kling and Seedance.
- **Soul Cast** (Mar 2026): builds AI actors from these parameters:
  - genre (14), era by decade, budget "in millions", archetype (13)
  - gender, race, age, build, height, eyes
  - hair style/texture/color, facial hair
  - details (scar, freckles, tattoos, eye patch), outfit (7)
- **AI Influencer** (relaunched 2026-10-02):
  - Game-style builder with 19 settings: type Average/Bold/Extreme, 25 facial features, 21 hairstyles,
    14 distinctive features, 7 styles, Randomize.
  - Supports non-humans (animals, aliens, hybrids) and creation from your own photo.
  - Output: a close-up plus a full-body shot.
  - Video: via Genjutsu, lipsync and Soul ID.
- **Character sheets:** an MCP workflow. Nano Banana Pro is used for turnarounds.

## 7. World creation
**There is no dedicated explorable-world (Genie/Marble-like) product.** World-building is spread across:
- **Soul Location:** an image model for environments without people.
- **Location Elements:** in Cinema Studio, reused across shots.
- **Cinema Studio 3D Mode (2.0/2.5):** reconstructs a Hero Frame as a **Gaussian splat** you can orbit, push into
  or find reverse angles in, then use as start frames.
- **3D Jutsu** (§10).
- **Higgsfield Games:** browser-game 3D environments.
- **3D generation:** Meshy image→3D / multi-image→3D / rigging (678-animation library), Tripo, SAM 3 3D,
  Hunyuan3D v3. All output GLB.

## 8. Faceless Studio (standalone since 2026-08-20)
Flow:
1. Create a channel.
2. Pick one of 4 AI-suggested topics, or upload a script.
3. Pick a style.
4. Pick a narrator voice.
5. Generate.

Options:
- **Themes:** Education, History, Kids, Storytelling.
- **12 styles:** Editorial Motion Graphics, Stickman Cartoon, Fairy Tale & Myth, Paper Diorama, Hand Drawn,
  Mannequin, Poster Vector, Colorful 3D, Watercolor Chronicle, Paper Collage, Studio 3D, Pastel Flat 2D.
- **Modes:** "Fully animated" or "Frame by Frame".

Specs:
- 1–5 min episodes, 16:9 or 9:16.
- Output is one file with AI script, narration, animation, music and subtitles. Processing takes up to 30 min.
- Style, voice and characters persist per channel.
- Cost: ~246 credits/min.

Related:
- **Explainer:** up to 10 min, 10+ presets, 40+ voices. Input is a topic, files or a URL.
- **Explainer pipeline (confirmed):** Seed Audio narration + Gemini Omni clips in matched 10-second blocks.

From the Higgsfield MCP `faceless-video` workflow (read-only), the pipeline phases are:
1. Intake.
2. Style anchor.
3. Asset roster (characters, locations, props).
4. Script + block plan.
5. Batch-generate the blocks.
6. Voiceover through the narration skill.
7. Assemble an immutable clean master (single assembler command).
8. Captions (if subtitles are on).
9. Optional thumbnail.
10. Deliver.

Two output formats: a motion-clip montage (10 s blocks of hard cuts) or narrated stills (one image held per
spoken beat). Kids also has a song mode. It includes a retry ladder for NSFW/failed clips and a final QC checklist.

## 9. Cinema Studio
| Version | Highlights |
|---|---|
| 2.0 (2026-02-17) | Two phases: **Hero Frame** (sensor, lens, focal 8–50 mm, aperture, 2×2/3×3/4×4 grid, 3D Mode) → motion. Multi-shot up to 6 shots (1–12 s each, 12 s total), start/end frames, speed ramp, genres, ≤3 characters, 1080p |
| 2.5 (03-18) | Soul Cast (≤3 actors); 6 camera bodies (e.g. 16mm Film, 70mm Film); 10 lenses (Anamorphic, Swirl Bokeh…); focal 8/14/35/50; f/1.4, f/4, f/11; in-studio grading |
| 3.0 (03-30) | ≤9 refs; genres General/Action/Horror/Comedy/Noir/Drama/Epic; speed ramps (Linear, Auto, Flash In/Out, Slow-mo, Bullet Time, Impact, Ramp Up); native audio |
| 3.5 (~May–Jun) | **Claude "AI Director" chat** writes prompts and splits scripts into shots; Elements; 9 palettes, 10 MoveSets, 7 lighting presets; own workspace (Projects, Characters, Locations, Collab) |
| **4.0 (08-12)** | 30 s generations, ≤50 refs; Film Setup (7 genres, Era 60s–2020s, Tempo Chaotic/Dynamic/Calm/Single Shot); **Emotion Wheel** (`@character [emotion]`); 4 bodies (Modern, 35mm, 8mm, DV), 5 lenses, 3 apertures, 30+ moves, 50+ palettes, 2-source lighting console; forward/backward Extend, regional Edit, co-directing. **Engine: Seedance 2.5** |

The live MCP schema for `cinematic_studio_video_4_0` has these params: mode (t2v / omni_reference / video_edit /
video_extension), duration 4–30, resolution 480/720/1080p, generate_audio, bitrate_mode, extension_mode, era_id,
camera_model_id, camera_lens_id, camera_aperture_id, pacing_id, genre_id, light (preset/custom/user), light_id,
light_custom, color_palette.

## 10. 3D Jutsu (2026-09-04) and Blender Scene Builder
Inputs: a prompt, optional refs, 1–60 s of scene time (default 15) and an aspect ratio.

Process:
1. A Supercomputer agent builds editable geometry in a 3D viewport.
2. The user edits it: primitives, sun/point/spot lights, cameras, a curated GLB library, **Mixamo characters**,
   a timeline with camera trajectories and character animation, transforms, checkpoints.

Output: an animated MP4, a still, or GLB. Only the agent LLM costs credits.

Per the MCP tools, the scene runs in server-side Blender driven by Python (`scene_builder_3d_run_python`,
`query_python`, `import_asset`, `search_assets`, `get_glb`, `get_blend`). `scene_builder_3d_generate_angles` turns
a rendered view into an AI image with `3d_jutsu_angles` (Seedream 5.0 Pro). The same scene layer runs in the
Blender plugin.

## 11. Lipsync Studio, talking avatars, UGC
- **Lipsync Studio** (10 models):
  - Image-to-video: Veo 3.1, Kling 2.6 Lipsync, Wan 2.5 Speak, Kling Avatars 2.0, Higgsfield Speak 2.0,
    InfiniteTalk (≤600 s).
  - Video-to-video: Kling Lipsync, Sync Lipsync 3 (≤4K).
  - Input: a script (TTS) or uploaded audio.
- **Speak 2.0:** categories (Vlog, Podcast, Beauty, Reporter, Selling…), 80+ motion presets, 70+ languages.
- **Talking-avatar recipe:** Soul 2.0 frame → Soul ID → Seed Audio voice (preset or clone) → Seedance 2.5 renders
  the lipsync.
- **UGC Factory:** its templates (Unboxing, Review, SaaS, Try-On, Tutorial, Localization) were folded into
  Marketing Studio.

## 12. Everything else
- **Draw-to-Video:** arrows (direction/speed), curves (paths), loops (idle), numbers (sequence) on the start frame.
  It is now an edit workflow and Seedance 2.5's "Draw to Video".
- **Viral Effects** ([/effects](https://higgsfield.ai/effects)): ~50 one-click i2v presets (Bullet Time,
  Earth Zoom, Melting…), plus a broader 100+ motion library.
- **Upscale:** ByteDance Upscale (4K/8K, 30/60 fps), FLUX 3 Video Upscaler, Topaz Video, deflicker.
  Also FPS Boost, SDR→HDR, Depth Anything Video, SAM 3 background removal.
- **Reframe:** outpaints to 9:16 / 16:9 / 1:1 / 4:5 / 21:9 with subject tracking.
- **Shorts Studio:** restyles footage with 44 presets, or a custom preset from 1–20 refs. ≤2 min input, 720p.
  Reportedly Gemini Omni Flash.
- **Marketing Studio:** Product Shots, Ads, Marketplace, Posters, UGC Videos (Faceless, Talking Head, Silent),
  Motion (Hypermotion, 2D Product, Mixed Media), Ref→Video. ≤15 s.
- **Ads Studio** (static ads), **Recast / Character Swap / Face Swap.**
- **Virality Predictor:** a `brain_activity` job, likely Meta TRIBE v2.
- **Audio:** TTS (Eleven v3/v4, MiniMax Speech, VibeVoice, Seed Speech, Qwen, Inworld), Seed Audio 1.0,
  voice change, dubbing (18 languages), voice clone.
- **Popcorn:** ≤8 consistent storyboard frames.
- **Vibe Motion:** Claude + Remotion motion graphics.
- **Canvas:** a node board.
- **Supercomputer:** an agent with skills.
- **Distribution:** MCP, CLI, API (2026-09-16), plugins for Adobe, Resolve, Blender and Figma, TikTok publishing.

## Pricing (individual plans, Sep 2026; sources conflict)
| Plan | Price | Credits/month |
|---|---|---|
| Free | $0 | 10/day, watermarked |
| Starter | $19 | 270 |
| Plus | $59 monthly / $47 annual | 1,200 |
| Ultra | $129 monthly / $99 annual | 3,000 |
| Team | $79/seat | 1,000/seat |
| Scale | $215/seat | 2,500/seat |

"All Unlimited" costs $99/day (23 models, web only).

## Competitors (where they add insight)
- **Krea:** aggregator with real-time generation and Nodes.
- **Freepik → Magnific:** 36+ models; Spaces node canvas.
- **LTX Studio:** storyboard-first; script → scenes/shots; Elements; **timeline editor (Higgsfield has none)**.
- **Runway:** owns its models (Gen-4.5, Aleph 2, Act-Two); Workflows.
- **Kling app:** Motion Brush; Elements with voice; multi-shot storyboards.
- **Hedra:** Character-3 avatars.
- **Pika:** Pikaffects / Pikadditions / Pikaswaps / Pikaframes / Pikaformance.
- **OpenArt:** one-click "Story" + shot timeline.

## Summary table
| Feature | What it does | Underlying model(s) | Key controls |
|---|---|---|---|
| Create Video | text/image/start-end → video | Seedance 2.5/2.0, Kling 3.0/Turbo/Omni, Veo 3.1, Omni Flash, Wan 3.0/2.7, H3, Grok 1.5, LTX 2.5, PixVerse 6, DoP | 3–30 s, 480p–4K, 6 aspect ratios, refs/@elements, audio, multi-shot |
| Edit Video | prompt/region edits | Kling O1/3.0 Omni Edit, Seedance 2.5 Edit, Omni Flash, FLUX 3 Video Edit | 3–30 s input, 4–50 refs, Draw to Edit |
| Relight / Palette | relight, regrade | undisclosed | 6 presets, 2 lights, 50+ palettes |
| Camera controls | cinematic moves from one image | DoP; Cinema Studio `#tags` | 65 presets, Mix, 3/5 s |
| Motion Transfer | image performs a ref video | Kling MC 2.6/3.0, Wan Animate, Genjutsu | 3–30 s ref, orientation, scene source |
| Genjutsu | motion transfer / object swap / restyle | wrapper (`hf_mult_*`) | 4–30 s, ≤30 refs, presets |
| Characters | consistent identities | Soul 2.0/ID/Cast, AI Influencer, Elements | 20–80 photos; 19 traits |
| Worlds | environments / 3D | Soul Location, CS 3D Mode (splat), 3D Jutsu, Meshy/Tripo | Location Elements, GLB |
| Faceless / Explainer | script → narrated video | Gemini Omni + Seed Audio | 4 themes, 12 styles, 1–10 min |
| Cinema Studio 4.0 | directed scenes | Seedance 2.5; Soul Cinema/Cast | genre, era, tempo, emotion, body, lens, aperture, moves, palette, light, 50 refs, 30 s |
| 3D Jutsu | prompt → 3D scene → MP4 | agent LLM + GLB/Mixamo + Blender | 1–60 s, lights, cameras, timeline |
| Lipsync / avatars | talking heads | Speak 2.0, InfiniteTalk, Kling Avatars, Sync 3, Veo 3.1, Seedance 2.5 | TTS/audio, voice clone |

## Key sources
[Changelog](https://higgsfield.ai/creator-hub/changelog) · [Help center](https://higgsfield.ai/creator-hub/help-center) ·
[Cinema Studio 4.0](https://higgsfield.ai/blog/cinema-studio-4-0) · [CS 3.5](https://higgsfield.ai/blog/cinema-studio-3.5-full-tutorial) ·
[CS 2.0](https://higgsfield.ai/blog/cinema-studio-guide) · [Genjutsu](https://higgsfield.ai/blog/higgsfield-genjutsu) ·
[3D Jutsu](https://higgsfield.ai/blog/higgsfield-3d-jutsu) · [Kling MC 3.0](https://higgsfield.ai/blog/kling-motion-control-3) ·
[Seedance 2.5](https://higgsfield.ai/blog/seedance-2-5-on-higgsfield-2026) · [Kling 3.0](https://higgsfield.ai/blog/Kling-3.0-is-on-Higgsfield-User-Guide-AI-Video-Generation) ·
[Edit without regenerating](https://higgsfield.ai/blog/edit-ai-video-without-regenerating) · [Omni VFX](https://higgsfield.ai/blog/Higgsfield-Omni-VFX) ·
[Relight/HEX](https://higgsfield.ai/blog/video-relight-color-palette-higgsfield) · [Camera controls](https://higgsfield.ai/camera-controls) ·
[DoP help](https://higgsfield.ai/creator-hub/help-center/ai-models/how-do-i-use-dop) · [Faceless Studio](https://higgsfield.ai/blog/faceless-youtube-videos-faceless-studio) ·
[Explainer](https://higgsfield.ai/blog/ai-video-from-text-and-url) · [Shorts Studio](https://higgsfield.ai/shorts-studio-intro) ·
[AI Influencer](https://higgsfield.ai/blog/new-ai-influencer) · [Soul ID](https://higgsfield.ai/creator-hub/help-center/ai-models/how-do-i-create-and-use-a-soul-id-character) ·
[Soul Cast](https://higgsfield.ai/blog/soul-cast-ai-filmmaking) · [Popcorn](https://higgsfield.ai/creator-hub/help-center/ai-models/how-do-i-use-popcorn) ·
[Lipsync](https://higgsfield.ai/creator-hub/help-center/ai-models/how-do-i-use-lipsync-voiceover-and-aspect-ratios) ·
[Marketing Studio](https://higgsfield.ai/blog/new-marketing-studio-higgsfield) · [Upscale](https://higgsfield.ai/blog/ai-video-restoration-high-quality) ·
[Effects](https://higgsfield.ai/effects) · [Apps](https://higgsfield.ai/apps) · [API](https://higgsfield.ai/blog/higgsfield-api) ·
[CLI](https://github.com/higgsfield-ai/cli) · [Sora migration](https://higgsfield.ai/blog/sora-api-migration-guide) ·
[Krea pricing analysis](https://www.krea.ai/blog/higgsfield-pricing-explained-2026-unlimited-credits-and-real-monthly-costs)
