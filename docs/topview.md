# Topview T1

The `topview-t1@topview` route supports text-to-video only: **720p**, fixed **4–30 seconds**, six aspect ratios,
and optional native audio. It appears in Create Video through the registry. No image/reference modes, automatic
duration, 1080p, seeds or negative prompts are exposed in this first integration.

API and price checked **2026-11-01**:

- [Official request/response contract](https://platform.topview.ai/docs/models/topview/topview-t1)
- [720p text-to-video pricing](https://platform.topview.ai/models/topview/topview-t1/text-to-video): **$0.1649/output second**,
  with or without audio ($0.6596 for 4 seconds, $0.8245 for 5 seconds).
- [Platform setup](https://platform.topview.ai/docs/quickstart)

The person adds a **Topview API Platform** key in **Settings > Providers** (or `TOPVIEW_API_KEY` in their environment).
It needs `models:generate`, `requests:read`, and access to T1 text-to-video. Platform workspace funds and keys are
separate from Topview consumer subscriptions and the legacy API. This integration does not fund the workspace,
change the studio's mode, or run a paid smoke test. Use the mock provider until ready to authorize spending.

The adapter submits once with the studio attempt ID as `Idempotency-Key`, persists the prediction ID, polls,
and hands HTTPS outputs to the studio's normal immediate local ingestion. It records the final `charged_usd`,
not the temporary reservation. Topview may reserve more than the final cost; available workspace balance must
cover the hold. Actual usage may exceed an estimate; the published API does not provide a hard charge cap.

Keep the original creating key until its tasks finish. A rotated key cannot poll those tasks; authorized workspace
members can use Platform history for recovery. Ambiguous submissions, missing results, unsettled billing and
`payment_required` park the studio job as **unknown**. Reconcile the original task; never start a replacement to
recover it. Topview automatically retries settlement after a workspace top-up. No cancellation endpoint is
documented, so canceling locally does not guarantee that remote generation or billing stops. On timeout the
studio conservatively keeps the estimated charge; a confirmed fully refunded failure records zero.

Verification uses intercepted HTTP responses and registry contracts only. Output quality and live account access
have not been tested. Kling 4.0 remains under the existing daily availability watch.
