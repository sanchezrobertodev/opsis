# Optional FastH3-Omni through Reactor

Verified 2026-10-24. This is Reactor's distilled H3 model, registered separately from MiniMax H3 and H3 Max.
The integration is preview quality: mocked protocol tests cover the lifecycle; a paid live recording has not
yet been verified. Existing providers and defaults are unchanged.

## Billing and whether it saves money

[Reactor bills GPU session wall-clock time](https://docs.reactor.inc/resources/billing), including idle time,
uploads after ready, generation, playback and recording download. Allocation/queue wait is free. Dropping the
network connection alone does not stop billing; terminate the session. Failed jobs can still incur charges.

The [public pricing endpoint](https://api.reactor.inc/pricing) reports FastH3-Omni at **$0.0125/second = $0.75/minute**.
This is not a price per generated video second. For example, 20 billed seconds cost $0.25; 40 cost $0.50;
60 cost $0.75. At the studio's currently registered fal H3 rate of $0.05/output second (480p), a 5-second clip
costs $0.25: Reactor saves money only if its entire billed session finishes in less than 20 seconds. The
models, durations and resolutions differ, so this is a spending comparison, not a quality equivalence.

The Studio reserves the full selected session cap: 30s/$0.375, 60s/$0.75 (default), or 120s/$1.50. Longer caps
allow more time to upload and save. A short cap can expire without delivering a usable recording. At submit,
the adapter checks the live price and refuses to connect if it exceeds the registry rate or cannot be verified.
It mints a model-scoped token permitting exactly one session with that server-enforced lifetime. This also
limits spending if the worker crashes. Successful jobs report an explicitly estimated cost, measured from
connection attempt through confirmed termination and bounded by the cap; free allocation time may inflate it.

Reactor currently has no usage/credit-balance API. Check the [dashboard](https://reactor.inc/dashboard) for actual
charges; the Studio balance card links there and does not invent an account balance. Prepaid credit purchase
minimum is $1, purchased credits expire after 365 days, and optional auto-top-up can be managed in Reactor.

## Enable

1. Install the optional SDK on the machine running the worker: `uv sync --extra reactor`.
   SDK 1.13.0 is pinned because recording and token-limit APIs have changed. Preserve the extra in deployment
   sync commands; a plain sync can remove optional dependencies. The normal `uv run` launch commands retain
   already-installed extras unless exact synchronization is requested.
2. Set `REACTOR_API_KEY` in the server/worker environment (or private `.env`). Get a key in Reactor's dashboard.
   Keep credentials out of frontend code. Ensure `ffmpeg` and `ffprobe` are installed.
3. Restart the API and worker after changing the environment. Select **FastH3-Omni** in Create Video.
   Without a key its live route is unavailable. Keep the existing provider-mode and budget controls in place.
   For a controlled trial, use mixed mode with `LIVE_ROUTE_ALLOWLIST=fast-h3-omni@reactor`.

No paid session was started during implementation. A future live smoke test can be explicitly capped:

```powershell
uv run --extra reactor python scripts/smoke_live.py --routes fast-h3-omni@reactor --cap 0.50 --confirm-spend
```

The smoke example uses the 30-second cap ($0.375 reserved). This test verifies whether generation, playback,
and recording finalization fit that time window. Raise the example's session cap and matching smoke budget
deliberately if it proves too short; do not automatically retry paid failures.

## Saving output

The adapter waits for `clip_generated`, plays the clip, waits for `clip_finished` (both video and audio sent),
then requests and downloads the full recording. Each session contains exactly one clip. The pinned Python
SDK assembles an authenticated fragmented MP4 to disk. The public recordings guide still describes older
Python MPEG-TS output; ffmpeg can read either. Reactor's recording links expire after 24 hours, so the Studio
downloads immediately instead of putting a temporary provider link into the library.

After downloading, the adapter terminates the session, remuxes locally into a fast-start MP4 with both tracks,
and checks that video, audio and at least the requested duration (within 0.25s) exist. The normal ingest flow
then saves it to the Studio media library. Download from the resulting asset as with other video providers.
No external storage bucket is required.

Temporary captures and recovery metadata live beside `MEDIA_ROOT` under `provider_results/reactor/<id>/`.
Metadata includes the Studio client reference and Reactor session/clip IDs, never the API key or JWT. A
successful capture is `00.mp4`; unfinished raw captures remain under `capture/` for manual recovery. If a
session may have billed but capture/termination fails, the job becomes `unknown`, retains its reservation,
and is never auto-resubmitted. Check Reactor before manually reconciling/retrying. A missing locally saved
recording after restart also parks the accepted job instead of generating another paid take.

This adapter completes a bounded session during submit. Canceling in the UI during that phase does not
instantly interrupt Reactor; it waits for completion or the session cap. The SDK disconnect is attempted
on errors/timeouts, with authenticated session deletion as fallback; the server lifetime is the final bound.

## Supported controls

- Text to video, image references, and pinned first/last frames with an ordinary appearance reference.
- Up to nine image references (three recommended for speed); JPEG, PNG or WebP.
- Up to three audio references, each 2–15 seconds, alongside image references.
- Durations 5.875, 8, 10.125, 12.25 or 14.375 seconds; seed and aspect selection.
- Native dimensions: 16:9 → 1344×768; 1:1 → 768×768; 9:16 → 768×1344; 4:3 → 1024×768.

Reactor's continuation IDs are session-local. This first integration deliberately closes each session and
does not expose cross-job continuation or arbitrary video references. See the official
[model schema](https://docs.reactor.inc/model-api-reference/fast-h3-omni/schema),
[recordings guide](https://docs.reactor.inc/concepts/recordings), and
[token constraints](https://docs.reactor.inc/authentication).
