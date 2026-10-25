# Optional Pika route for Seedance 2.5

Verified 2026-10-25 against Pika's live catalog, OpenAPI and free quote endpoint. No paid generation was run.

Set `PIKA_API_KEY` in the private environment of the API and worker, then restart them. No extra SDK is needed.
Select Seedance 2.5 and pin the Pika provider (`seedance-2.5@pika`) when you want to use it. Without a key the
live route is unavailable. Existing Mock/Mixed/Live controls still apply; for a restricted trial, use Mixed
with `LIVE_ROUTE_ALLOWLIST=seedance-2.5@pika`. Cheapest-healthy routing may still prefer Replicate for text/image
generation; Pika adds a cheaper alternative to fal for references, editing and extension.

Supported: text, first/optional last frame, multi-shot, multi-shot from a frame, omni references, edit and
extension at the model's existing 480p/720p resolutions and 4–30s lengths. Pika does not accept a seed, so this
route drops it. Edit/extend explicitly select the task and adaptive shape instead of asking Pika to infer it.
Draft-to-final and 1080p are not exposed by this change. Image/audio/video uploads go directly to Pika; no fal
key or storage bucket is required. Local audio references must be WAV/MP3, videos MP4/MOV. Video/audio refs
must each be 2–30s and total at most 30s per kind. Upload caps are 20 MB for images, 15 MB audio, 50 MB video.

## Cost

Pika API Club charges $10/month membership plus generation usage, with a $10 first-month credit advertised
on its [pricing page](https://dev.pika.art/pricing). Membership is not included in the Studio per-job budget.
The [Seedance rates](https://dev.pika.art/models/bytedance/seedance-2.5/reference-to-video/pricing) are
$11.235/million tokens without video inputs and $6.72/million with video inputs at 480p/720p. Video-input jobs
bill input plus output duration; editing uses input length for output. Audio on/off has the same rate.

The registry reserves nominal tokens with 12% headroom (and a conservative 480p pixel allowance). The free
quote endpoint currently reserves about 10% above nominal 720p cost: a 5s 720p text job quotes $1.334729,
versus about $1.21338 nominal usage. These are estimates, not hard provider spending caps. Actual billing
depends on reported tokens and account pricing. The adapter checks the free live quote against the Studio
reservation and checks prepaid balance or remaining invoice-cycle allowance before submitting. A higher
quote blocks submission so pricing can be reviewed; it never silently increases the budget.

The Settings balance card reads actual Pika spending capacity. Completed jobs reconcile to the provider's
settled `charge_micro_usd`, including an explicit zero. Tokens alone do not substitute for a receipt.
If terminal billing remains unavailable after short bounded retries, the job is parked as unknown for
reconciliation instead of inventing a charge or submitting another generation.

## Saving and failures

The adapter submits once with an attempt-specific idempotency key and polls the returned job ID. Dropped
submit responses remain unknown and are never automatically resubmitted. Documented rejected-job envelopes
are polled even when their HTTP status is 403/429/503, so their final billing and error codes are retained.
Completed output is downloaded through normal Studio ingest and saved to the local media library as MP4.
API credentials are sent only to Pika's API origin, never to upload storage or a redirected CDN.

Pika has no generation cancellation endpoint. Canceling in Studio may leave the paid job running remotely.
Its DELETE operation erases stored inputs/outputs; the adapter deliberately does not call it for cancellation.
Only successful generations are billed under Pika's documented policy. Missing/deleted outputs after a
completed generation park the job for investigation rather than trigger another paid render.

Sources: [API index](https://dev.pika.art/llms.txt), [OpenAPI](https://dev.pika.art/openapi.json),
[live catalog](https://api.dev.pika.art/catalog/apis/bytedance/seedance-2.5/reference-to-video?expand=inputs),
[operation specification](https://dev.pika.art/llms/bytedance/seedance-2.5/reference-to-video).
