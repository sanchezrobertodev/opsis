"""The MCP server: a template run estimated, started, reviewed and finished as a Claude app would, on the mock."""

from __future__ import annotations

import base64
import io
import json

import httpx
import pytest
from mcp import Client
from PIL import Image
from test_pipelines import drain

from studio.api import mcp_tools
from studio.api.main import app as api


@pytest.fixture
def mcp(db, in_memory_queue):
    # each test opens the client itself: anyio wants it closed in the task that opened it
    return lambda: Client(mcp_tools.server)


async def call(client: Client, tool: str, **args):
    res = await client.call_tool(tool, args)
    assert not res.is_error, res.content
    out = res.structured_content
    return out["result"] if set(out) == {"result"} else out


async def refused(client: Client, tool: str, **args) -> str:
    res = await client.call_tool(tool, args)
    assert res.is_error
    return res.content[0].text


async def test_a_template_run_from_estimate_to_result(mcp, in_memory_queue):
    async with mcp() as client:
        await _template_run(client, in_memory_queue)


async def _template_run(mcp, in_memory_queue):
    names = {t.name for t in (await mcp.list_tools()).tools}
    assert names == {"catalog", "estimate_run", "start_run", "get_run", "list_runs", "answer_review", "find_media",
                     "spend", "upload"}  # nothing that changes the budget, the mode or keys

    templates = {t["id"]: t for t in await call(mcp, "catalog")}
    assert templates["history-short"]["fields"][0]["key"] == "topic"
    assert any(r["id"] == "faceless" for r in await call(mcp, "catalog", kind="recipes"))
    assert any(m["id"] == "veo-3.1" for m in await call(mcp, "catalog", kind="models"))

    args = {"template_id": "history-short", "fields": {"topic": "the night the Titanic's band kept playing"}}
    est = await call(mcp, "estimate_run", **args)
    assert est["mode"] == "mock" and est["estimate_usd"] == 0 and est["live_estimate_usd"] > 0
    assert est["billed"] is False and est["needs_confirmation"] is False and est["models"]

    run = await call(mcp, "start_run", **args)
    await drain(in_memory_queue)
    run = await call(mcp, "get_run", run_id=run["id"])
    assert run["status"] == "waiting"
    review = run["waiting_for_you"][0]
    assert review["key"] == "review" and review["options"][0]["value"] == "approve"
    assert review["context"]["script"]["script"]  # the script to read before approving

    await call(mcp, "answer_review", run_id=run["id"], key="review", choice="approve")
    await drain(in_memory_queue)
    run = await call(mcp, "get_run", run_id=run["id"])
    thumb = run["waiting_for_you"][0]  # then a pick, each option with its picture
    assert thumb["key"] == "thumb" and thumb["options"][0]["media"]["kind"] == "image"
    assert "pick one" in await refused(mcp, "answer_review", run_id=run["id"], key="thumb", choice="approve")
    await call(mcp, "answer_review", run_id=run["id"], key="thumb", choice=thumb["options"][0]["value"])
    await drain(in_memory_queue)
    run = await call(mcp, "get_run", run_id=run["id"])
    assert run["status"] == "succeeded", (run["failed_steps"], run["waiting_for_you"])
    assert run["result"]["kind"] == "video" and run["result"]["url"].startswith("/media/")
    assert (await call(mcp, "list_runs", limit=1))[0]["id"] == run["id"]
    assert (await call(mcp, "spend"))["mode"] == "mock"


async def test_refusals_come_back_in_the_apis_words(mcp):
    async with mcp() as mcp:
        await _refusals(mcp)


async def _refusals(mcp):
    assert "is required" in await refused(mcp, "estimate_run", template_id="history-short")  # no topic
    assert "template_id" in await refused(mcp, "start_run")
    assert "base64" in await refused(mcp, "upload", filename="a.png", content_base64="not base64!")


async def test_uploads_go_through_the_studios_upload(mcp):
    async with mcp() as mcp:
        await _uploads(mcp)


async def _uploads(mcp):
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 40, 40)).save(buf, "PNG")
    asset = await call(mcp, "upload", filename="red.png", content_base64=base64.b64encode(buf.getvalue()).decode())
    assert asset["kind"] == "image" and (asset["width"], asset["height"]) == (64, 48)
    assert asset["id"] in {a["id"] for a in await call(mcp, "find_media", kind="image", source="upload")}
    svg = base64.b64encode(b"<svg xmlns='http://www.w3.org/2000/svg'/>").decode()
    assert "svg" in (await refused(mcp, "upload", filename="x.svg", content_base64=svg)).lower()


@pytest.mark.parametrize(("provider", "llm", "billed"), [
    ("mock", "mock", False), ("mixed", "mock", True), ("mock", "live", True), ("live", "live", True)])
async def test_a_run_counts_as_billed_when_either_side_can_spend(monkeypatch, provider, llm, billed):
    async def fake_api(method, path, **kw):
        if path == "/budget":
            return {"provider_mode": provider, "llm_mode": llm, "confirm_over_usd": 5.0, "daily_cap_usd": 20.0,
                    "today_usd": 0.0, "reserved_usd": 0.0, "monthly_cap_usd": 100.0, "month_usd": 0.0}
        return {"estimate_usd": 0.0, "live_estimate_usd": 1.2, "generations": 3, "models": ["Veo 3.1"], "cap_usd": 5}

    monkeypatch.setattr(mcp_tools, "_api", fake_api)
    est = await mcp_tools.estimate_run(recipe="faceless")
    assert est["billed"] is billed and est["claude_mode"] == llm


def test_a_mask_review_shows_the_tracked_mask():
    mask = {"id": "m1", "kind": "mask_video", "url": "/media/u/1/mask.mp4", "duration_ms": 5000}
    step = {"key": "track", "kind": "generate", "label": "Track", "status": "succeeded", "depends_on": [],
            "output": {"asset_id": "m1"}, "options": [], "assets": [mask], "error": None}
    review = {"key": "mask", "kind": "checkpoint", "label": "Check the tracked mask", "status": "waiting",
              "depends_on": ["track"], "output": {}, "options": [{"value": "approve", "label": "Follows"}],
              "assets": [], "error": None}
    run = {"id": "r", "title": "Swap", "recipe_name": "Object swap", "status": "waiting", "error": None,
           "spent_usd": 0, "estimate_usd": 0, "cap_usd": 5, "steps_done": 1, "steps_total": 4, "steps": [step, review]}
    shown = mcp_tools._run(run, "https://box.tail.ts.net:19444")["waiting_for_you"][0]
    assert shown["media"][0]["url"] == "https://box.tail.ts.net:19444/media/u/1/mask.mp4"
    assert shown["context"] == {}


def test_an_expensive_run_asks_for_the_estimate_back():
    r = httpx.Response(409, json={"message": "This run is estimated at $3.20.", "estimate_usd": 3.2,
                                  "route_id": "recipe:faceless", "threshold_usd": 1.0})
    assert mcp_tools._problem(r).endswith("call start_run again with confirm_cost_usd=3.2.")


async def test_the_endpoint_is_served_on_the_api_for_this_machine_only(db):
    hello = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}
    headers = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    async with mcp_tools.running(), httpx.AsyncClient(transport=httpx.ASGITransport(app=api),
                                                      base_url="http://localhost:8400") as c:
        r = await c.post("/mcp", content=json.dumps(hello), headers=headers)
        assert r.status_code == 200 and r.json()["result"]["serverInfo"]["name"] == "opsis"
        # a page that rebinds its own name to this machine is turned away
        evil = await c.post("/mcp", content=json.dumps(hello), headers={**headers, "host": "evil.example"})
        assert evil.status_code in (400, 421)


def test_links_use_the_scheme_the_caller_reached_the_proxy_with():
    from starlette.requests import Request

    def ctx(headers):
        scope = {"type": "http", "scheme": "http", "server": ("box.tail.ts.net", 19444), "path": "/mcp",
                 "headers": [(k.encode(), v.encode()) for k, v in headers.items()]}
        return type("Ctx", (), {"request_context": type("RC", (), {"request": Request(scope)})()})()

    proxied = {"host": "box.tail.ts.net:19444", "x-forwarded-proto": "https"}
    assert mcp_tools._base(ctx(proxied)) == "https://box.tail.ts.net:19444"
    assert mcp_tools._base(ctx({"host": "localhost:8400"})) == "http://localhost:8400"
