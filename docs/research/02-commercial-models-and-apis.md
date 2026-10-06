# Commercial video models and API access routes (research as of 2026-10-06)

Prices are list prices from mixed sources, dated where known. Re-check vendor pages before committing; promos change
weekly. Abbreviations: t2v text-to-video, i2v image-to-video, FLF first+last frame, ref reference images/elements,
v2v video edit, MC motion transfer, LS lipsync.

## TL;DR
- **The Sora 2 / Sora 2 Pro API was discontinued on 2026-09-24.** Notice was given 2026-03-24 and no successor was
  named. Don't build on it.
- **Higgsfield sells its own API** at open.higgsfield.ai (cloud.higgsfield.ai redirects there): 50+ models,
  including its own Soul 2, DoP, Genjutsu and Cinema Studio 4.0. Runway, Pika and OpenRouter have also become
  multi-model resellers.
- **Current flagships:** Veo 3.1 and Gemini Omni 1.1 Flash (Google), Kling 3.0 / 3.0 Omni / 3.0 Turbo,
  Seedance 2.5, MiniMax H3, Wan 3.0 and HappyHorse 1.1 (Alibaba), FLUX 3 Video, LTX-2.5, Grok Imagine 1.5,
  Runway Gen-4.5 + Aleph 2.0, Luma Ray 3.2. There is no Veo 4 yet.
- **Primary aggregator: fal.ai.** It has the broadest catalog and the best async tooling, at official prices for
  Veo and Kling but about 2× for Seedance 2.5.
- **Integrate directly:** Google (Veo, Omni, Nano Banana), BytePlus (Seedance, if eligible), Runway (Aleph 2.0 and
  Gen-4.5 are not on fal), ElevenLabs; later Kling and Sync.so once volume justifies it.

## 1. Video models
| Model | Provider | Capabilities | Duration / resolution | Price (list) | Routes |
|---|---|---|---|---|---|
| **Veo 3.1** Std / Fast / Lite (`veo-3.1-generate-preview` / `-fast-` / `-lite-`) | Google | t2v, i2v, FLF, ≤3 ref images, extend +7 s ×20 (~148 s, 720p, not Lite), audio | 4/6/8 s; 720p, 1080p, 4K (no 4K on Lite); 16:9, 9:16. 1080p, 4K, refs and extend require 8 s. **Outputs deleted after 2 days** | Std $0.40/s (720p, 1080p), $0.60/s (4K). Fast $0.10 / $0.12 / $0.30. Lite $0.05 / $0.08 | Gemini API, Vertex, fal (same price), Replicate, OpenRouter, Runway API, WaveSpeed |
| **Gemini Omni 1.1 Flash** (`gemini-omni-1.1-flash`, GA 2026-08-27) | Google | any-to-video (text, image, video, audio); conversational multi-turn editing (Interactions API); keyframes/FLF; extension; audio | 10 s per call, extendable to 40 s; 360p draft, 720p native, 1080p/4K upscale | ~$0.03 (360p), $0.10 (720p), $0.15 (1080p), $0.30/s (4K) | Gemini API (paid), Vertex, Runway API, Segmind. Whether it's on fal is unclear |
| ~~Sora 2 / 2 Pro~~ | OpenAI | — | — | — | **Shut down 2026-09-24** |
| **Kling 3.0** (2026-02-05) | Kuaishou | t2v, i2v, FLF, multi-shot (≤6), elements (`@Element`), audio (5 languages), voice control | 3–15 s; 720p, 1080p, 4K | Official: $0.084/s (720p silent), $0.112 (720p audio or 1080p silent), $0.14 (1080p audio), $0.42 (4K) | Kling Open Platform, fal (Pro $0.112–0.196), Replicate (~2×), WaveSpeed, OpenRouter, Higgsfield API |
| **Kling 3.0 Omni ("O3")** | Kuaishou | ref-to-video (images, elements, video), **Omni Edit (v2v)**, multi-shot, element voice | 3–15 s, native 4K at 60fps (4K mode takes no ref video or voice) | same as 3.0 | Kling, fal, Replicate |
| **Kling 3.0 Turbo** | Kuaishou | fast t2v/i2v, multi-shot, audio | 3–15 s; 720p, 1080p | ~$0.11 (720p), ~$0.14/s (1080p) | Kling (needs newly issued keys), aggregators |
| Kling **Motion Control 3.0**, **Avatar 2.0**, Lip Sync; legacy 2.6, 2.5 Turbo, 2.1, O1 | Kuaishou | MC from a ref clip; avatar from image + audio; LS | — | MC $0.126 (Std), $0.168/s (Pro). Avatar $0.056 (720p), $0.112 (1080p) | fal, WaveSpeed, Kling |
| **Seedance 2.0** / Fast / Mini | ByteDance | t2v, i2v, FLF, refs (9 img, 3 vid, 3 aud), edit/extend, audio. **Blocks real human faces in reference images** | 4–15 s; 480p, 720p (1080p/4K on some routes) | BytePlus ~$0.07 (480p), $0.15 (720p), $0.37/s (1080p) | BytePlus (**not offered in the US**), fal, Replicate, OpenRouter, WaveSpeed |
| **Seedance 2.5** (2026-07-31, `dreamina-seedance-2-5-260628`) | ByteDance | single take, ≤50 refs (30 img, 10 vid, 10 aud), multi-round extension, audio | 4–30 s; 480p/720p native (1080p+ is a reseller upscale); 21:9 to 9:16 | BytePlus/Replicate $0.1028 (480p), $0.2312/s (720p). **fal $0.2205 / $0.473** | BytePlus (not offered in US/CA/UK/AU/NZ), Replicate, fal, WaveSpeed, OpenRouter, Runway API, Higgsfield API |
| **MiniMax H3** / H3 Max / Max Turbo / Director | MiniMax | t2v, i2v, FLF, refs (image, video, audio), stereo audio, extend | 5–15 s; 768p, 2K (4K on fal) | Official: H3 $0.08 (768p), $0.13/s (2K). H3 Max $0.05 (480p), $0.08 (768p) | MiniMax, fal, OpenRouter, Runway, Runware, Higgsfield. Open weights, but the license **excludes US, EU, UK and South Korea** |
| Hailuo 2.3 / Fast (legacy) | MiniMax | t2v, i2v, no audio | 6–10 s; 768p, 1080p | ~$0.28 per 6 s at 768p | MiniMax, Replicate, OpenRouter, fal |
| **Wan 3.0** / 3.0 Prime (`wan3.0-video`) | Alibaba | t2v, FLF, all-in-one refs (text, image, audio, video), audio | 2–30 s; 480p, 720p, 1080p | $0.05 / $0.10 / $0.20/s (2 concurrent, 30 RPM) | Model Studio, OpenRouter (from $0.0425/s), Runway, Runware, Higgsfield. **Closed weights** |
| Wan 2.7 / 2.6 / 2.5 | Alibaba | 2.7: t2v, i2v, r2v (with voice), videoedit | 2–15 s; 720p, 1080p | ~$0.10/s and up | Model Studio, fal, Replicate, OpenRouter. **Only Wan 2.1/2.2 (+Animate, S2V) have open weights** |
| **HappyHorse 1.0 / 1.1** | Alibaba | t2v, i2v, r2v (≤9 refs), video-edit (1.0), audio with multilingual LS | 3–15 s; 480p–1080p | fal $0.14 (720p), $0.18 (1080p). OpenRouter $0.0988 / $0.1278 | Model Studio, fal, OpenRouter, Replicate, Runware |
| **Runway Gen-4.5**, Gen-4 Turbo | Runway | t2v, i2v, native HDR (native audio unconfirmed) | 2–10 s | $0.12/s; Turbo $0.05/s | Runway API, Replicate. **Not on fal** |
| **Runway Aleph 2.0**, **Act-Two** | Runway | strongest instruction-based v2v editor; performance capture | — | credit-based ($0.01/credit) | Runway API (also OpenRouter for Aleph 2) |
| **Luma Ray 3.2** (2026-06-09) | Luma | t2v, i2v, ≤16 keyframes, **Modify (v2v)**, loop/extend, HDR / 16-bit EXR. No audio | 5–10 s; 360p draft to 1080p, 4K upscale | $0.012 (draft), $0.03 (540p), $0.06 (720p), $0.24/s (1080p). V2V 1080p $0.432/s | Luma API, fal, Replicate |
| **FLUX 3 Video** (paid API Aug–Sep 2026) | BFL | t2v, i2v, keyframes, continuation, edit/extend, audio | ≤20 s; HD up to UHD | $0.06 (draft), $0.17 (HD), $0.29 (FHD), $0.40 (QHD), $0.80/s (UHD) | BFL, fal, Runware, Segmind. FLUX Video Edit is $0.03/s |
| **LTX-2.5** Fast / Pro | Lightricks | t2v, i2v, multi-shot, audio | Fast ≤20 s, 720p–4K; Pro 6–10 s | Fast $0.09–0.30. Pro $0.12 / $0.17 | LTX API, fal, Runware, Higgsfield. **Open weights (free <$10M ARR)** |
| **Grok Imagine Video 1.5** | xAI | t2v, i2v, audio | 480p–1080p | $0.08 / $0.14 / $0.25 per s, +$0.01 per input image | xAI, fal, Replicate, OpenRouter, Runway |
| **PixVerse V6**, C1 | PixVerse | t2v, i2v, multi-shot, 20+ lens controls, audio | 1–15 s; 360p–1080p | from $100/month official; aggregators $0.025–0.115/s | PixVerse, Replicate, fal, Higgsfield |
| **Vidu Q3** | Shengshu | t2v, i2v, FLF, ref2video, audio | ≤16 s; 540p–1080p | $0.10 (720p), $0.12/s (1080p); half price off-peak | Vidu, Replicate, Runware |
| HeyGen Video 1 | HeyGen | t2v, i2v, ref2v, audio | 5–15 s | $0.02/s | HeyGen, OpenRouter, Runware |
| Pika 2.5 / Pikaformance | Pika | t2v, i2v, Pikaffects / Pikadditions, audio-driven | — | $0.04 (720p), $0.09/s (1080p) | Pika API, fal |
| Pruna P-Video-2 | Pruna | cheapest draft tier | ≤20 s, 1080p 48fps | $0.015–0.05/s | Replicate, Segmind, Runware |
| **Higgsfield DoP / Genjutsu / Cinema Studio 4.0** | Higgsfield | camera/VFX presets; v2v MC + object replace; shot planning | — | DoP $0.125 per generation; Genjutsu $0.159/s (promo); CS $0.206/s | **Higgsfield API only** |

## 2. Lipsync and avatars
| Model | Capabilities | Price | Routes |
|---|---|---|---|
| Sync.so lipsync-2 / **2-pro** / **sync-3** / react-1 | v2v LS; sync-3 outputs native 4K and handles occlusions; react-1 edits emotion | $0.04–0.167/s by tier | sync.so, fal, WaveSpeed |
| OmniHuman 1.5 | image + audio → avatar, multiple characters | $0.12/s BytePlus, $0.16/s fal | BytePlus, fal, WaveSpeed |
| Kling Avatar 2.0 | image + audio → avatar | $0.056 (720p), $0.112/s (1080p) | Kling, fal |
| HeyGen Avatar IV / V | photo avatar, digital twin | $0.05–0.067/s | HeyGen API |
| Hedra Character-3 / Omnia | talking character; Omnia adds camera moves and environments | $0.025–0.0625/s | Hedra API |
| VEED Fabric 1.0 | image + audio → talking video, ≤5 min | $0.08 (480p), $0.15/s (720p) | fal, WaveSpeed, Replicate |

## 3. Image models (keyframes and characters)
| Model | Price | Notes |
|---|---|---|
| **Nano Banana 2** (Gemini 3.1 Flash Image) | $0.045–0.151 per image (512px–4K); Lite $0.034 | Gemini API, Vertex, fal, Replicate, OpenRouter |
| **Nano Banana Pro** (Gemini 3 Pro Image) | $0.134 (1K/2K), $0.24 (4K) | best text rendering, multi-ref |
| **Seedream 5.0** Pro / Lite / Flash | $0.045–0.09 / $0.035 / $0.027 | BytePlus, fal, Runware, OpenRouter |
| **GPT Image 2 / 2.5** (Flare, Sunburst) | ~$0.006–0.211 per 1024² image | 2.5 takes up to 16 refs; OpenAI, fal |
| FLUX.2 (klein/pro/flex/max); **FLUX 3 Image** (2026-10-01) | klein $0.014; FLUX 3 $0.041–0.607 | Kontext is legacy |
| Ideogram 4.x, Recraft V4.1, Qwen-Image-Edit-2511 (open, Apache), Qwen-Image-3.0 (closed), Grok Image 2 | — | — |
| Higgsfield Soul 2 / Soul Cinema | $0.0032 per image | Higgsfield API only |

## 4. Audio
- **ElevenLabs Eleven v4 / v4 Turbo** (2026-09-28): $0.08 / $0.04 per 1K characters. SFX, music and voice changer
  are $0.12–0.15 per minute.
- **Google Lyria 3 / 3.5:** $0.04 per 30 s clip, $0.08 per song.
- **Suno has no public API.** A partner program was announced in July 2026, so avoid the unofficial wrappers.
- **Video-to-audio:** MMAudio (open, but NC weights), HunyuanVideo-Foley, Kling V2A, Mirelo, Seed Audio 1.0.
  Less needed now that most flagship models generate audio natively.

## 5. Aggregators
| Aggregator | Coverage | Price vs direct | API style | SDKs | Files / retention | Limits |
|---|---|---|---|---|---|---|
| **fal.ai** | broadest: Veo 3.1, Kling 3.0/O3/Turbo/MC/Avatar, Seedance 2.0/2.5, H3, Wan, HappyHorse, Luma, Grok, PixVerse, Vidu, LTX, FLUX 3, sync, OmniHuman, Fabric, Nano Banana, Seedream, GPT Image, ElevenLabs, MMAudio. **No Runway** | official for Veo and Kling; ~2× for Seedance 2.5 | durable queue (submit, status, stream, result, cancel), webhooks, ≤10 auto-retries, priorities | `fal-client` (Python), `@fal-ai/client` (JS) | built-in storage upload; outputs kept ≥7 days | 2 concurrent at start, rising to 40 as you buy credits |
| **Replicate** (owned by Cloudflare) | Veo 3.1, Kling v3/Omni, Seedance, Wan 3/2.7, Hailuo, **Runway Gen-4.5**, Luma Ray 3.2, PixVerse V6, Vidu Q3, Grok, HappyHorse, Fabric | official for Veo; Kling ~2× fal; Seedance 2.5 t2v at the BytePlus rate | async predictions, webhooks, `Prefer: wait` | many | Files API; **outputs deleted after 1 hour** | 600 creates/min |
| **WaveSpeedAI** | 700–1000+ models | Kling at official; Seedance 2.5 Turbo 1080p $0.21 | submit/poll + webhooks | Py, JS | upload API | tiered (default: 5/min, 2 concurrent) |
| **Runware** | Veo, Kling, Seedance 2.5, H3, Wan 3.0, HappyHorse, LTX-2.5, FLUX 3, PixVerse, Vidu | claims lowest (Wan 3.0 $0.05 at 480p) | one endpoint, async + webhooks | JS, Py | — | unpublished |
| **OpenRouter** (video since Apr 2026) | Veo 3.1, Seedance 2.x, Kling v3, Wan 3.0/2.7, HappyHorse 1.1, H3, Hailuo, Grok 1.5, Runway Aleph 2, HeyGen. No lipsync or most edit tools | mostly pass-through; cheapest seen for Wan and HappyHorse | `POST /api/v1/videos`, then poll or HMAC-signed webhooks; `/api/v1/videos/models` lists capabilities | OpenAI-style REST | inputs by URL | unpublished |
| **Higgsfield API** (open.higgsfield.ai) | 50+ models, including Soul 2, DoP, Genjutsu, CS 4.0. Veo and Nano Banana reportedly absent | heavy launch promos; list prices near official | async, poll or webhooks | `higgsfield-client` (Py), TS | outputs kept ≥7 days | 20 concurrent at start |
| **Runway API** (now reselling) | Gen-4.5, Aleph 2, Act-Two, plus Veo 3.1, Seedance 2/2.5, Wan 3, H3 Max, Grok, Omni, GPT Image, Nano Banana, ElevenLabs | $0.01/credit; Veo at Google's price | task-based, plus a Model Router | Node, Py | — | — |
| kie.ai, PiAPI, AIML, Together, Segmind, EachLabs, Atlas | various | kie claims 30–80% below official (opaque sourcing) | task APIs | REST | — | fine for prototyping; avoid on critical paths |

**Same model, different route (per output second):**

| Model | Direct | fal | Replicate | WaveSpeed | OpenRouter |
|---|---|---|---|---|---|
| Veo 3.1 Std, with audio | $0.40 | $0.40 | $0.40 | — | — |
| Kling 3.0, 1080p with audio | $0.14 | $0.168 | $0.336 | — | from $0.126 |
| Seedance 2.5, 720p | $0.2312 | $0.473 | $0.2312 | $0.36 | — |
| HappyHorse 1.1, 1080p | — | $0.18 | — | — | $0.1278 |

The cheapest route changes from model to model, so the code must route each model to more than one provider.

## 6. Recommendation
- **Primary aggregator:** fal.ai. Ask for higher concurrency early.
- **Secondary routes:** OpenRouter (fallback router, cheapest Wan and HappyHorse, signed webhooks, capability
  discovery); Replicate or WaveSpeed (cheaper Seedance 2.5; Replicate also has Runway Gen-4.5).
- **Direct integrations, in priority order:**
  1. **Google Gemini API / Vertex:** Veo 3.1 (all tiers), Gemini Omni 1.1, Nano Banana 2/Pro, Lyria. Same price as
     fal, but day-one features. Copy outputs out within 2 days.
  2. **BytePlus ModelArk:** Seedance, Seedream, OmniHuman. About 2× cheaper than fal, but only for companies
     outside US/CA/UK/AU/NZ. Plan for the face-in-reference block.
  3. **Runway API:** Aleph 2.0, Gen-4.5, Act-Two. None of these are on fal.
  4. **ElevenLabs:** v4 TTS, SFX, music.
  5. **Kling direct**, once spend justifies prepaid packs.
  6. **Sync.so**, at volume.
  7. **Higgsfield API (optional):** the only source of Soul 2, DoP, Genjutsu and CS 4.0, but Higgsfield is a
     competitor and its prices are promos.
- **Avoid:** Sora, unofficial Suno wrappers, and deep-discount resellers on critical paths.
- **Policy flags:** MiniMax H3's open license has territory exclusions. Veo limits image-based person generation.
  Replicate outputs vanish after 1 hour, so ingest them immediately.

## Sources (accessed 2026-10-06)
Google: [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing), [Veo docs](https://ai.google.dev/gemini-api/docs/veo) ·
Sora: [OpenAI discontinuation notice](https://help.openai.com/en/articles/20001152-what-to-know-about-the-sora-discontinuation) ·
Kling: [dev pricing](https://kling.ai/dev/pricing), [Omni API](https://kling.ai/document-api/apiReference/model/OmniVideo), [fal Kling v3](https://fal.ai/models/fal-ai/kling-video/v3/pro/image-to-video) ·
Seedance: [fal Seedance 2.5](https://fal.ai/models/bytedance/seedance-2.5/image-to-video), [US exclusion](https://the-decoder.com/bytedance-rolls-out-seedance-2-0-to-100-countries-but-keeps-the-us-off-the-list/) ·
MiniMax: [pricing](https://platform.minimax.io/docs/pricing/overview) · Alibaba: [Model Studio video](https://www.alibabacloud.com/help/en/model-studio/use-video-generation) ·
Runway: [API pricing](https://docs.dev.runwayml.com/guides/pricing/) · Luma: [Ray 3.2](https://lumalabs.ai/news/introducing-ray-3-2) ·
BFL: [release notes](https://docs.bfl.ml/release-notes) · Higgsfield: [API blog](https://higgsfield.ai/blog/higgsfield-api), [open.higgsfield.ai](https://open.higgsfield.ai/) ·
Lipsync: [Sync.so models](https://sync.so/docs/models/lipsync) · ElevenLabs: [API pricing](https://elevenlabs.io/pricing/api) ·
Aggregators: [fal queue](https://fal.ai/docs/documentation/model-apis/inference/queue), [fal concurrency](https://fal.ai/docs/documentation/model-apis/concurrency-limits),
[Replicate rate limits](https://replicate.com/docs/topics/predictions/rate-limits), [OpenRouter video](https://openrouter.ai/docs/guides/overview/multimodal/video-generation),
[Runware pricing](https://runware.ai/pricing), [WaveSpeed docs](https://wavespeed.ai/docs)
