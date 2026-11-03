"""A paid route end to end (Seedance 2.5 on Replicate) with Replicate's HTTP mocked.

Real API, real worker task code, real Postgres; only api.replicate.com is faked. Pins what matters for
money: the estimate, the reservation -> release + charge ledger trail, the confirmation threshold, and
that an input is uploaded once and then reused.
"""

from __future__ import annotations

import io
import json
import uuid
from decimal import Decimal

import httpx
import pytest
import respx
from PIL import Image
from sqlalchemy import delete, select

from studio.api.main import app as api
from studio.config import get_settings
from studio.db.models import AssetProviderUpload, GenerationAttempt, ProviderRouteHealth, SpendLedger
from studio.db.session import session_scope
from studio.domain.enums import AttemptStatus, ErrorClass
from studio.media.mock_media import make_video
from studio.worker.tasks.generation import run_ingest, run_poll, run_submit

R = "https://api.replicate.com/v1"
DELIVERY = "https://replicate.delivery/test/output.mp4"


@pytest.fixture
async def mp4_bytes(tmp_path):
    dest = tmp_path / "clip.mp4"
    await make_video(dest, tmp_path / "w", model_id="seedance-2.5", mode="i2v", prompt="t", duration=1,
                     resolution="480p", aspect="16:9")
    return dest.read_bytes()


@pytest.fixture
def live(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "provider_mode", "live")
    monkeypatch.setattr(s, "replicate_api_token", "test-token")
    monkeypatch.setattr(s, "fal_key", "")  # only Replicate is configured, so routing must pick it


@pytest.fixture
def http():
    """Fake api.replicate.com for the duration of a test (anything unmocked raises)."""
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
async def client(db, in_memory_queue, live):
    async with session_scope() as s:  # failures in earlier tests would leave a route's breaker open here
        await s.execute(delete(ProviderRouteHealth))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


async def upload_image(client) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (854, 480), (30, 90, 160)).save(buf, "PNG")
    r = await client.post("/uploads", files={"file": ("frame.png", buf.getvalue(), "image/png")})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def run(gen_id: uuid.UUID) -> None:
    await run_submit(gen_id)
    async with session_scope() as s:
        attempt = (await s.execute(select(GenerationAttempt).where(GenerationAttempt.generation_id == gen_id)
                                   .order_by(GenerationAttempt.attempt_no.desc()))).scalars().first()
    assert attempt is not None, "no attempt was created"
    await run_poll(attempt.id)
    await run_ingest(attempt.id)


def mock_replicate(http: respx.MockRouter, mp4: bytes) -> dict[str, respx.Route]:
    return {
        "files": http.post(f"{R}/files").respond(201, json={"id": "f1", "urls": {"get": f"{R}/files/f1"}}),
        "predict": http.post(f"{R}/models/bytedance/seedance-2.5/predictions").mock(
            side_effect=lambda req: httpx.Response(201, json={"id": f"p-{uuid.uuid4().hex[:6]}", "status": "starting"})),
        "status": http.get(url__regex=rf"{R}/predictions/p-\w+").respond(
            200, json={"status": "succeeded", "output": DELIVERY, "metrics": {"predict_time": 38.0}}),
        "delivery": http.get(DELIVERY).respond(200, content=mp4, headers={"content-type": "video/mp4"}),
    }


async def test_seedance_on_replicate_charges_the_estimate_and_caches_the_upload(client, http, mp4_bytes):
    routes = mock_replicate(http, mp4_bytes)
    image_id = await upload_image(client)
    body = {"model_id": "seedance-2.5", "mode": "i2v", "prompt": "the ship sails out of the harbor",
            "params": {"duration": 4, "resolution": "480p"}, "media": [{"role": "start_image", "asset_id": image_id}],
            "route_policy": "cheapest_healthy"}

    est = (await client.post("/registry/estimate", json=body)).json()
    assert est["chosen_route_id"] == "seedance-2.5@replicate"
    assert est["estimate_usd"] == pytest.approx(4 * 0.1028)

    budget_before = (await client.get("/budget")).json()
    gen = (await client.post("/generations", json=body)).json()
    await run(uuid.UUID(gen["id"]))
    done = (await client.get(f"/generations/{gen['id']}")).json()
    assert done["status"] == "succeeded", done["error_message"]
    assert done["actual_cost_usd"] == pytest.approx(0.4112)
    assert done["assets"][0]["kind"] == "video"

    sent = json.loads(routes["predict"].calls.last.request.content)["input"]
    assert sent["image"] == f"{R}/files/f1"  # uploaded once, referenced by Files API URL
    assert sent["duration"] == 4 and sent["resolution"] == "480p" and sent["prompt"].startswith("the ship")

    async with session_scope() as s:
        attempt = (await s.execute(select(GenerationAttempt).where(
            GenerationAttempt.generation_id == uuid.UUID(gen["id"])))).scalar_one()
        ledger = {e.entry_type.value: e.amount_usd for e in (await s.execute(
            select(SpendLedger).where(SpendLedger.generation_attempt_id == attempt.id))).scalars()}
        cached = (await s.execute(select(AssetProviderUpload).where(
            AssetProviderUpload.asset_id == uuid.UUID(image_id)))).scalar_one()
    assert ledger == {"reservation": Decimal("0.411200"), "release": Decimal("-0.411200"), "charge": Decimal("0.411200")}
    assert cached.provider == "replicate" and cached.remote_ref == f"{R}/files/f1"
    budget_after = (await client.get("/budget")).json()
    assert budget_after["today_usd"] - budget_before["today_usd"] == pytest.approx(0.4112)
    assert budget_after["reserved_usd"] == budget_before["reserved_usd"]

    history = (await client.get("/budget/history")).json()
    entry = next(e for e in history["entries"] if e["generation_id"] == gen["id"])
    assert entry == {**entry, "kind": "generation", "amount_usd": pytest.approx(0.4112),
                     "label": "the ship sails out of the harbor", "model_id": "seedance-2.5"}
    assert any(p["provider"] == "replicate" and p["name"] == "Replicate" and p["usd"] >= 0.4112 for p in history["by_provider"])

    # Second take with the same start frame reuses the upload.
    gen2 = (await client.post("/generations", json=body)).json()
    await run(uuid.UUID(gen2["id"]))
    assert routes["files"].call_count == 1


async def test_expensive_runs_need_confirmation(client, http, mp4_bytes):
    mock_replicate(http, mp4_bytes)
    body = {"model_id": "seedance-2.5", "mode": "t2v", "prompt": "a long take",
            "params": {"duration": 30, "resolution": "720p"}, "route_policy": "cheapest_healthy"}
    r = await client.post("/generations", json=body)
    assert r.status_code == 409
    problem = r.json()
    assert problem["code"] == "confirmation_required"
    assert problem["estimate_usd"] == pytest.approx(30 * 0.2312)
    r = await client.post("/generations", json={**body, "confirm_cost_usd": problem["estimate_usd"]})
    assert r.status_code == 201


async def test_rejected_key_fails_over_and_explains(client, http):
    http.post(f"{R}/models/bytedance/seedance-2.5/predictions").respond(401, json={"detail": "Invalid token."})
    body = {"model_id": "seedance-2.5", "mode": "t2v", "prompt": "x", "params": {"duration": 4, "resolution": "480p"},
            "route_policy": "cheapest_healthy"}
    gen = (await client.post("/generations", json=body)).json()
    gid = uuid.UUID(gen["id"])
    await run_submit(gid)  # 401 -> route unavailable -> failover requeues
    await run_submit(gid)  # nothing else is configured -> fails with the reasons
    done = (await client.get(f"/generations/{gen['id']}")).json()
    assert done["status"] == "failed" and done["error_code"] == "route_unavailable"
    assert "no API key for fal.ai" in done["error_message"]
    budget = (await client.get("/budget")).json()
    assert budget["reserved_usd"] == 0  # the failed attempt's reservation was released


async def test_a_pinned_provider_runs_there_and_never_falls_back(client, http, monkeypatch):
    """The Create form's Provider choice: fal is dearer than Replicate, but a take pinned to it is priced and sent
    there, and when fal refuses it, it fails instead of moving to Replicate."""
    monkeypatch.setattr(get_settings(), "fal_key", "test-key")
    fal = http.post("https://queue.fal.run/bytedance/seedance-2.5/text-to-video").respond(401, json={"detail": "bad key"})
    replicate = http.post(f"{R}/models/bytedance/seedance-2.5/predictions").respond(201, json={"id": "p-1"})
    body = {"model_id": "seedance-2.5", "mode": "t2v", "prompt": "x", "params": {"duration": 4, "resolution": "480p"}}
    auto = (await client.post("/registry/estimate", json=body)).json()
    assert auto["chosen_route_id"] == "seedance-2.5@replicate"

    body["route_policy"] = "pinned:seedance-2.5@fal"
    pinned = (await client.post("/registry/estimate", json=body)).json()
    assert pinned["chosen_route_id"] == "seedance-2.5@fal" and pinned["estimate_usd"] > auto["estimate_usd"]

    gen = (await client.post("/generations", json=body)).json()
    gid = uuid.UUID(gen["id"])
    await run_submit(gid)  # 401 -> route unavailable -> failover requeues
    await run_submit(gid)  # the pin leaves no other route -> fails
    done = (await client.get(f"/generations/{gen['id']}")).json()
    assert done["status"] == "failed" and done["error_code"] == "route_unavailable"
    assert fal.call_count == 1 and not replicate.called


async def test_runpod_public_endpoint_charges_what_runpod_reports(client, http, mp4_bytes, monkeypatch):
    """Wan 2.2 14B on a RunPod public endpoint: the input image goes inline (no FAL_KEY), and the take is
    charged RunPod's reported `cost` rather than the price-list estimate."""
    monkeypatch.setattr(get_settings(), "runpod_api_key", "rp-key")
    RP = "https://api.runpod.ai/v2/wan-2-2-i2v-720"
    video = "https://video.runpod.ai/test/output.mp4"
    run_route = http.post(f"{RP}/run").respond(200, json={"id": "job-9", "status": "IN_QUEUE"})
    http.get(f"{RP}/status/job-9").respond(200, json={
        "status": "COMPLETED", "output": {"video_url": video, "cost": 0.29}})
    http.get(video).respond(200, content=mp4_bytes, headers={"content-type": "video/mp4"})

    image_id = await upload_image(client)
    body = {"model_id": "wan-2.2-14b", "mode": "i2v", "prompt": "grass sways", "params": {"duration": 5},
            "media": [{"role": "start_image", "asset_id": image_id}], "route_policy": "cheapest_healthy"}
    est = (await client.post("/registry/estimate", json=body)).json()
    assert est["chosen_route_id"] == "wan-2.2-14b@runpod" and est["estimate_usd"] == pytest.approx(0.30)

    gen = (await client.post("/generations", json=body)).json()
    await run(uuid.UUID(gen["id"]))
    done = (await client.get(f"/generations/{gen['id']}")).json()
    assert done["status"] == "succeeded", done["error_message"]
    assert done["actual_cost_usd"] == pytest.approx(0.29)

    sent = json.loads(run_route.calls.last.request.content)["input"]
    assert sent["image"].startswith("data:image/png;base64,") and sent["size"] == "1280*720"
    assert sent["duration"] == 5 and sent["num_inference_steps"] == 30
    async with session_scope() as s:
        attempt = (await s.execute(select(GenerationAttempt).where(
            GenerationAttempt.generation_id == uuid.UUID(gen["id"])))).scalar_one()
        charge = (await s.execute(select(SpendLedger).where(
            SpendLedger.generation_attempt_id == attempt.id, SpendLedger.entry_type == "charge"))).scalar_one()
    assert charge.amount_usd == Decimal("0.290000") and charge.estimated is False


async def test_a_runs_cap_stops_a_take_however_cheap(client, in_memory_queue):
    """A take under the confirmation threshold still has to fit in what's left of its run's cap."""
    from test_pipelines import drain, get

    from studio.db.models import PipelineRun

    r = await client.post("/pipelines", json={"recipe": "sequence", "input": {
        "model_id": "seedance-2.5", "shots": [{"prompt": "a lighthouse at dusk", "duration": 4}]}})
    assert r.status_code == 201, r.text
    run_id = r.json()["id"]
    async with session_scope() as s:  # the cap shrinks to less than the take costs (as if earlier takes spent it)
        run = await s.get(PipelineRun, uuid.UUID(run_id))
        run.cap_usd = Decimal("0.01")
    await drain(in_memory_queue)
    done = await get(client, run_id)
    assert done["status"] == "failed" and "cap" in done["steps"][0]["error"]
    assert done["steps"][0]["generation_id"] is None  # nothing was created, so nothing was sent


async def test_an_unreadable_status_parks_the_take_instead_of_paying_again(client, http, mp4_bytes):
    """The status call fails with 401 (a rotated key) while the accepted job runs: no failover, a human decides."""
    routes = mock_replicate(http, mp4_bytes)
    routes["status"].respond(401, json={"detail": "Invalid token"})
    body = {"model_id": "seedance-2.5", "mode": "t2v", "prompt": "a calm sea", "params": {"duration": 4}}
    gen = (await client.post("/generations", json=body)).json()
    await run_submit(uuid.UUID(gen["id"]))
    async with session_scope() as s:
        attempt = (await s.execute(select(GenerationAttempt).where(
            GenerationAttempt.generation_id == uuid.UUID(gen["id"])))).scalar_one()
    await run_poll(attempt.id)
    done = (await client.get(f"/generations/{gen['id']}")).json()
    assert done["status"] == "unknown" and "401" in done["error_message"]
    assert routes["predict"].call_count == 1  # never sent again
    budget = (await client.get("/budget")).json()
    assert budget["reserved_usd"] > 0  # its money stays held until someone checks the provider


async def test_a_reroute_never_goes_over_what_is_left_of_the_runs_cap(client, http, mp4_bytes):
    """The take was affordable when made; by the time the worker routes it (a failover, a cheaper route gone) the
    route it picks must still fit the limit the take carries."""
    from studio.db.models import Generation

    routes = mock_replicate(http, mp4_bytes)
    gen = (await client.post("/generations", json={"model_id": "seedance-2.5", "mode": "t2v", "prompt": "a calm sea",
                                                   "params": {"duration": 4}})).json()
    async with session_scope() as s:  # as if made by a run with $0.10 left
        g = await s.get(Generation, uuid.UUID(gen["id"]))
        g.routing = {**g.routing, "max_cost_usd": 0.10}
    await run_submit(uuid.UUID(gen["id"]))
    done = (await client.get(f"/generations/{gen['id']}")).json()
    assert done["status"] == "failed" and "approved for" in done["error_message"]
    assert "$0.10" in done["error_message"] and ": $" in done["error_message"]  # the limit and the cheapest route left
    assert routes["predict"].call_count == 0  # nothing sent


async def test_a_failover_over_the_limit_says_why_the_first_route_refused(client, http, mp4_bytes):
    """The route it was priced on refused it and the next costs more than it was approved for: the error leads with
    the refusal, or it reads like the estimate was wrong."""
    from studio.db.models import Generation

    routes = mock_replicate(http, mp4_bytes)
    gen = (await client.post("/generations", json={"model_id": "seedance-2.5", "mode": "t2v", "prompt": "a calm sea",
                                                   "params": {"duration": 4}})).json()
    async with session_scope() as s:  # as if Pika's quote came in high and the take failed over
        g = await s.get(Generation, uuid.UUID(gen["id"]))
        g.routing = {**g.routing, "max_cost_usd": 0.10, "tried": ["seedance-2.5@pika"]}
        s.add(GenerationAttempt(generation_id=g.id, attempt_no=1, route_id="seedance-2.5@pika", provider="pika",
                                status=AttemptStatus.failed, error_class=ErrorClass.route_unavailable,
                                error_message="Pika quoted $5.59, above the $1.63 the studio priced it at"))
    await run_submit(uuid.UUID(gen["id"]))
    done = (await client.get(f"/generations/{gen['id']}")).json()
    assert done["error_message"].startswith("Pika couldn't take it: Pika quoted $5.59")
    assert "approved for" in done["error_message"] and routes["predict"].call_count == 0


async def test_a_take_canceled_while_its_send_is_in_doubt_says_so(client, http, mp4_bytes, monkeypatch):
    """Canceled while being sent, and the send ends with no answer: the provider may bill it, so the take says to
    check instead of showing a clean cancel."""
    from studio.providers.base import AmbiguousSubmitError
    from studio.providers.replicate import ReplicateAdapter

    mock_replicate(http, mp4_bytes)
    gen = (await client.post("/generations", json={"model_id": "seedance-2.5", "mode": "t2v", "prompt": "a calm sea",
                                                   "params": {"duration": 4}})).json()

    async def cancel_then_drop(self, route, endpoint, payload, *, client_ref):
        assert (await client.post(f"/generations/{gen['id']}/cancel")).status_code == 200
        raise AmbiguousSubmitError("Replicate submit may have gone through: server disconnected")

    monkeypatch.setattr(ReplicateAdapter, "submit", cancel_then_drop)
    await run_submit(uuid.UUID(gen["id"]))
    done = (await client.get(f"/generations/{gen['id']}")).json()
    assert done["status"] == "canceled" and "may have taken it" in done["error_message"]
    async with session_scope() as s:
        attempt = (await s.execute(select(GenerationAttempt).where(
            GenerationAttempt.generation_id == uuid.UUID(gen["id"])))).scalar_one()
    assert attempt.status.value == "unknown"
    held = (await client.get("/budget")).json()["reserved_usd"]
    assert held >= float(attempt.estimated_cost_usd) > 0  # held while nobody knows whether it's billed

    # checked on the provider's dashboard: marking it releases the money; the take stays canceled
    r = await client.post(f"/generations/{gen['id']}/resolve-unknown")
    assert r.status_code == 200 and r.json()["status"] == "canceled"
    assert (await client.get("/budget")).json()["reserved_usd"] == pytest.approx(held - float(attempt.estimated_cost_usd))


async def test_a_take_canceled_while_sending_that_was_taken_is_canceled_there(client, http, mp4_bytes, monkeypatch):
    """The send that a cancel raced with got a job id: the job is canceled at the provider and the money released."""
    from studio.domain.generations import cancel_generation
    from studio.providers.base import SubmitResult
    from studio.providers.replicate import ReplicateAdapter

    mock_replicate(http, mp4_bytes)
    gen = (await client.post("/generations", json={"model_id": "seedance-2.5", "mode": "t2v", "prompt": "a calm sea",
                                                   "params": {"duration": 4}})).json()

    async def cancel_then_taken(self, route, endpoint, payload, *, client_ref):
        assert (await client.post(f"/generations/{gen['id']}/cancel")).status_code == 200
        return SubmitResult(job_id="p-taken")

    monkeypatch.setattr(ReplicateAdapter, "submit", cancel_then_taken)
    await run_submit(uuid.UUID(gen["id"]))
    async with session_scope() as s:
        attempt = (await s.execute(select(GenerationAttempt).where(
            GenerationAttempt.generation_id == uuid.UUID(gen["id"])))).scalar_one()
        kinds = {e.entry_type.value for e in (await s.execute(
            select(SpendLedger).where(SpendLedger.generation_attempt_id == attempt.id))).scalars()}
    assert attempt.status.value == "canceled" and attempt.provider_job_id == "p-taken"
    assert kinds == {"reservation", "release"}
    assert cancel_generation  # the cancel itself left the attempt to the send


async def test_a_take_carries_the_most_it_was_approved_for(client, http, mp4_bytes):
    """Without a run, the limit is what the user saw: the estimate, a confirmed amount, or the no-confirmation line."""
    mock_replicate(http, mp4_bytes)
    cheap = (await client.post("/generations", json={"model_id": "seedance-2.5", "mode": "t2v", "prompt": "a calm sea",
                                                     "params": {"duration": 4}})).json()
    policy = (await client.get("/budget")).json()
    assert cheap["routing"]["max_cost_usd"] == pytest.approx(max(cheap["estimated_cost_usd"], policy["confirm_over_usd"]))


@pytest.mark.parametrize(("cancel_status", "status"), [(500, "unknown"), (200, "queued")])
async def test_a_take_past_its_routes_limit_fails_over_only_once_it_is_stopped(client, http, mp4_bytes, cancel_status,
                                                                              status):
    """Still running when its route's time is up: sent elsewhere only if Replicate confirms the cancel; otherwise
    it may still finish and bill, so it waits for a human with its money held."""
    from datetime import UTC, datetime, timedelta

    routes = mock_replicate(http, mp4_bytes)
    routes["status"].mock(side_effect=[httpx.Response(200, json={"status": "processing"}),
                                       httpx.Response(200, json={"status": "canceled"})])
    cancel = http.post(url__regex=rf"{R}/predictions/p-\w+/cancel").respond(cancel_status)
    held_before = (await client.get("/budget")).json()["reserved_usd"]
    gen = (await client.post("/generations", json={"model_id": "seedance-2.5", "mode": "t2v", "prompt": "a calm sea",
                                                   "params": {"duration": 4}})).json()
    await run_submit(uuid.UUID(gen["id"]))
    async with session_scope() as s:
        attempt = (await s.execute(select(GenerationAttempt).where(
            GenerationAttempt.generation_id == uuid.UUID(gen["id"])))).scalar_one()
        attempt.submitted_at = datetime.now(UTC) - timedelta(days=1)
    await run_poll(attempt.id)
    done = (await client.get(f"/generations/{gen['id']}")).json()
    assert done["status"] == status and cancel.call_count == 1
    held = (await client.get("/budget")).json()["reserved_usd"] - held_before
    if status == "unknown":
        assert "may still finish and bill" in done["error_message"] and held > 0
    else:
        assert done["routing"]["tried"] == ["seedance-2.5@replicate"] and held == pytest.approx(0)


@pytest.mark.parametrize("then", ["succeeded", "canceled"])
async def test_a_take_just_past_its_limit_is_still_watched(client, http, mp4_bytes, then):
    """Replicate won't confirm the cancel: the take is neither sent elsewhere nor given up on. If it finishes after
    all, its result is kept; if it stops later, it goes elsewhere then, like any take that timed out."""
    from datetime import UTC, datetime, timedelta

    from studio.registry.loader import get_registry
    from studio.worker.tasks.generation import PAST_LIMIT

    routes = mock_replicate(http, mp4_bytes)
    later = {"status": then, "output": DELIVERY, "metrics": {"predict_time": 38.0}}
    routes["status"].mock(side_effect=[httpx.Response(200, json={"status": "processing"}), httpx.Response(200, json=later)])
    cancel = http.post(url__regex=rf"{R}/predictions/p-\w+/cancel").respond(500)
    gen = (await client.post("/generations", json={"model_id": "seedance-2.5", "mode": "t2v", "prompt": "a calm sea",
                                                   "params": {"duration": 4}})).json()
    await run_submit(uuid.UUID(gen["id"]))
    limit = get_registry().model("seedance-2.5").route("seedance-2.5@replicate").timeout_s
    async with session_scope() as s:
        attempt = (await s.execute(select(GenerationAttempt).where(
            GenerationAttempt.generation_id == uuid.UUID(gen["id"])))).scalar_one()
        attempt.submitted_at = datetime.now(UTC) - timedelta(seconds=limit * 1.5)
    await run_poll(attempt.id)
    assert (await client.get(f"/generations/{gen['id']}")).json()["status"] == "in_progress"
    async with session_scope() as s:
        watched = await s.get(GenerationAttempt, attempt.id)
        assert watched.error_message.startswith(PAST_LIMIT) and watched.next_poll_at is not None

    await run_poll(attempt.id)
    if then == "succeeded":
        await run_ingest(attempt.id)
    done = (await client.get(f"/generations/{gen['id']}")).json()
    assert cancel.call_count == 1  # asked once
    if then == "succeeded":
        assert done["status"] == "succeeded" and routes["predict"].call_count == 1
    else:
        assert done["status"] == "queued" and done["routing"]["tried"] == ["seedance-2.5@replicate"]


async def test_failovers_never_take_a_run_past_its_cap(client, http, mp4_bytes):
    """Each take was approved for what was left of the run when it was made; one that fails over to a dearer route
    must still fit what the run's other takes have left since."""
    from studio.db.models import Generation, PipelineRun

    routes = mock_replicate(http, mp4_bytes)
    async with session_scope() as s:
        run = PipelineRun(user_id=get_settings().owner_user_id, recipe="sequence", title="two shots",
                          cap_usd=Decimal("0.80"))
        s.add(run)
        await s.flush()
        run_id = run.id
    body = {"model_id": "seedance-2.5", "mode": "t2v", "prompt": "a calm sea",
            "params": {"duration": 4, "resolution": "480p"}, "group_id": str(run_id)}
    first = (await client.post("/generations", json=body)).json()
    second = (await client.post("/generations", json=body)).json()
    async with session_scope() as s:  # as if priced at $0.30 on a route that then refused it, with $0.80 left
        g = await s.get(Generation, uuid.UUID(second["id"]))
        g.estimated_cost_usd = Decimal("0.30")
        g.routing = {**g.routing, "max_cost_usd": 0.80, "tried": ["seedance-2.5@pika"]}
    await run_submit(uuid.UUID(first["id"]))  # $0.41 of the $0.80
    await run_submit(uuid.UUID(second["id"]))  # $0.41 more on Replicate would make $0.82
    done = (await client.get(f"/generations/{second['id']}")).json()
    assert done["status"] == "failed" and "$0.39 left in the run's $0.80 cap" in done["error_message"]
    assert routes["predict"].call_count == 1


async def test_a_fal_take_still_running_past_its_limit_never_goes_elsewhere(client, http, monkeypatch):
    """fal answers the cancel of a running request with 202 (asked, not done) and its SDK returns as for a 200: the
    take waits for a human instead of paying a second provider, and the route's breaker still counts each timeout."""
    from datetime import UTC, datetime, timedelta

    import fal_client

    from studio.domain.enums import BreakerState
    from studio.providers.fal import FalAdapter

    class StillRunning:
        async def status(self, app, request_id, with_logs=False):
            return fal_client.InProgress(logs=[])

        async def cancel(self, app, request_id):
            pass

    monkeypatch.setattr(get_settings(), "fal_key", "test-key")
    monkeypatch.setattr(get_settings(), "replicate_api_token", "")
    monkeypatch.setattr(FalAdapter, "client", property(lambda self: StillRunning()))
    submit = http.post("https://queue.fal.run/bytedance/seedance-2.5/text-to-video").mock(
        side_effect=lambda req: httpx.Response(200, json={"request_id": uuid.uuid4().hex}))
    for _ in range(3):
        gen = (await client.post("/generations", json={"model_id": "seedance-2.5", "mode": "t2v", "prompt": "a calm sea",
                                                       "params": {"duration": 4, "resolution": "480p"}})).json()
        await run_submit(uuid.UUID(gen["id"]))
        async with session_scope() as s:
            attempt = (await s.execute(select(GenerationAttempt).where(
                GenerationAttempt.generation_id == uuid.UUID(gen["id"])))).scalar_one()
            attempt.submitted_at = datetime.now(UTC) - timedelta(days=1)
        await run_poll(attempt.id)
        done = (await client.get(f"/generations/{gen['id']}")).json()
        assert done["status"] == "unknown" and "may still finish and bill" in done["error_message"]
    assert submit.call_count == 3
    async with session_scope() as s:
        assert (await s.get(ProviderRouteHealth, "seedance-2.5@fal")).breaker_state == BreakerState.open
