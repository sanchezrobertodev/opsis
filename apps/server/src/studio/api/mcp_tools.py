"""The studio's MCP server at `/mcp` (Streamable HTTP), so the Claude apps can estimate, start and follow runs.

The tools call the API's own routes in-process, so every check applies as it does over HTTP: validation, the run
cap, the daily and monthly caps, and confirmation over the threshold. No tool changes the budget, the mode switch
or keys. There is no login of its own: like the API, it's for your tailnet only.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal
from urllib.parse import quote, urlsplit

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from studio.config import get_settings

INSTRUCTIONS = """Opsis is the user's own AI video studio. Runs make videos, pictures and sound from a recipe or a
template (a recipe with most choices made). Start from catalog(kind="templates").

Money: always call estimate_run first and tell the user what it costs and with which models. When the estimate says
billed (generation in "mixed" or "live", or Claude "live"; claude_usd is Claude's writing within it), the run may
spend real money: start it only after the user agrees. When it says needs_confirmation, pass its confirm_cost_usd to
start_run. With both modes "mock" nothing is billed and results are stand-ins.

Runs take minutes. Check them with get_run; a run waiting_for_you needs an answer (answer_review): show the user the
question, the media it's about and the options first. Media inputs are asset ids (from upload or find_media) or a saved character's
@handle. Links point at the studio, which opens only on the user's tailnet."""

server = MCPServer(name="opsis", title="Opsis studio", instructions=INSTRUCTIONS)


# ---------------------------------------------------------------- the API, in-process


async def _api(method: str, path: str, **kw: Any) -> Any:
    from studio.api.main import app  # imported here: main mounts this module

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://opsis") as client:
        r = await client.request(method, path, **kw)
    if r.status_code >= 400:
        raise ToolError(_problem(r))
    return r.json() if r.content else None


def _problem(r: httpx.Response) -> str:
    """The API's own words for a refusal."""
    try:
        body = r.json()
    except ValueError:
        return f"HTTP {r.status_code}: {r.text[:300]}"
    if not isinstance(body, dict):
        return f"HTTP {r.status_code}"
    if "threshold_usd" in body:  # ConfirmationProblem
        est = body["estimate_usd"]
        return (f"{body['message']} Show the user the estimate (${est:.2f}); if they agree, call start_run again with "
                f"confirm_cost_usd={est}.")
    if body.get("errors"):
        return "; ".join(str(e) for e in body["errors"])
    detail = body.get("message") or body.get("detail")
    return detail if isinstance(detail, str) else json.dumps(detail)[:800] if detail else f"HTTP {r.status_code}"


def _base(ctx: Context) -> str:
    """Where the caller reached the API, so links work from its side (empty in-process)."""
    try:
        request = ctx.request_context.request
    except (LookupError, ValueError, AttributeError):
        return ""
    if request is None:
        return ""
    # behind `tailscale serve` the API sees plain http; the proxy says what the caller used (only its own links)
    proto = request.headers.get("x-forwarded-proto")
    url = request.base_url.replace(scheme=proto) if proto in ("http", "https") else request.base_url
    return str(url).rstrip("/")


def _media(asset: dict[str, Any] | None, base: str) -> dict[str, Any] | None:
    if not asset:
        return None
    link = lambda key: base + asset[key] if asset.get(key) else None  # noqa: E731
    return {"id": asset["id"], "kind": asset["kind"], "title": asset.get("title"), "url": link("url"),
            "thumb_url": link("thumb_url") or link("poster_url"), "width": asset.get("width"),
            "height": asset.get("height"),
            "seconds": round(asset["duration_ms"] / 1000, 1) if asset.get("duration_ms") else None}


def _run(r: dict[str, Any], base: str) -> dict[str, Any]:
    steps = r["steps"]
    by_key = {s["key"]: s for s in steps}

    def review(s: dict[str, Any]) -> dict[str, Any]:
        deps = [by_key[d] for d in s["depends_on"] if d in by_key]
        shown = {o["asset"]["id"] for o in s["options"] if o.get("asset")}
        return {
            "key": s["key"], "question": s["label"],
            "options": [{"value": o["value"], "label": o.get("label"), "media": _media(o.get("asset"), base)}
                        for o in s["options"]],
            # what to read or look at before answering: the script Claude wrote, the tracked mask
            "context": {d["key"]: d["output"] for d in deps if d["kind"] == "llm"},
            "media": [_media(a, base) for d in deps for a in d.get("assets") or [] if a["id"] not in shown],
        }

    waiting = [review(s) for s in steps if s["kind"] == "checkpoint" and s["status"] == "waiting"]
    return {
        "id": r["id"], "title": r["title"], "recipe": r["recipe_name"], "status": r["status"], "error": r["error"],
        "spent_usd": r["spent_usd"], "estimate_usd": r["estimate_usd"], "cap_usd": r["cap_usd"],
        "steps_done": r["steps_done"], "steps_total": r["steps_total"],
        "result": _media(r.get("output_asset"), base),
        "waiting_for_you": waiting,
        "working_on": [s["label"] for s in steps if s["status"] == "running"],
        "failed_steps": [{"key": s["key"], "label": s["label"], "error": s["error"]}
                         for s in steps if s["status"] == "failed"],
    }


async def _run_body(template_id: str | None, fields: dict[str, str] | None, inputs: dict[str, str] | None,
                    recipe: str | None, recipe_input: dict[str, Any] | None, title: str | None,
                    cap_usd: float | None) -> dict[str, Any]:
    if template_id:
        body = await _api("POST", f"/templates/{quote(template_id, safe='')}/render",
                          json={"fields": fields or {}, "media": inputs or {}})
    elif recipe:
        body = {"recipe": recipe, "input": recipe_input or {}}
    else:
        raise ToolError("Name a template_id (see catalog), or a recipe with its input.")
    if title:
        body["title"] = title
    if cap_usd is not None:
        body["cap_usd"] = cap_usd
    return body


# ---------------------------------------------------------------- tools


@server.tool()
async def catalog(kind: Literal["templates", "recipes", "models"] = "templates") -> list[dict[str, Any]]:
    """What the studio can make.

    templates: the easy way in, a recipe with most choices made. Each asks for `fields` (answers; a field with
    `choices` takes one of them) and `inputs` (media by key: an asset id, or a character's @handle for kind
    character; `optional` ones may be left out, and of `alternative` ones give exactly one).
    recipes: the run types, with the JSON schema of their input, for runs no template covers.
    models: what runs render with (id, what it makes, its modes).
    """
    if kind == "templates":
        return [{k: t[k] for k in ("id", "name", "summary", "makes", "fields", "inputs")}
                for t in await _api("GET", "/templates")]
    if kind == "recipes":
        return await _api("GET", "/pipelines/recipes")
    reg = await _api("GET", "/registry")
    return [{"id": m["id"], "name": m["name"], "makes": m["output_type"], "status": m["status"],
             "runnable": m["runnable"], "from_price_usd": m["from_price_usd"],
             "modes": [mode["id"] for mode in m["modes"]]} for m in reg["models"]]


@server.tool()
async def estimate_run(template_id: str | None = None, fields: dict[str, str] | None = None,
                       inputs: dict[str, str] | None = None, recipe: str | None = None,
                       recipe_input: dict[str, Any] | None = None, title: str | None = None,
                       cap_usd: float | None = None) -> dict[str, Any]:
    """What a run would cost, without starting it (free). Give a template_id with its fields and inputs, or a recipe
    with its recipe_input. cap_usd is the most the run may spend (defaults to the user's run cap).

    Returns the estimate in the mode in effect (mock costs nothing), what it would cost live, the modes, whether it
    may be billed (then ask the user first), whether start_run needs confirm_cost_usd, and what's left of today's and
    this month's caps.
    """
    body = await _run_body(template_id, fields, inputs, recipe, recipe_input, title, cap_usd)
    est = await _api("POST", "/pipelines/estimate", json=body)
    budget = await _api("GET", "/budget")
    needs = est["estimate_usd"] > budget["confirm_over_usd"]
    return {
        "estimate_usd": est["estimate_usd"], "live_estimate_usd": est["live_estimate_usd"],
        "mode": budget["provider_mode"], "claude_mode": budget["llm_mode"],
        "billed": budget["provider_mode"] != "mock" or budget["llm_mode"] != "mock",
        "generations": est["generations"], "models": est["models"], "claude_usd": est["claude_usd"],
        "cap_usd": est["cap_usd"], "needs_confirmation": needs,
        "confirm_cost_usd": est["estimate_usd"] if needs else None,
        "left_today_usd": round(budget["daily_cap_usd"] - budget["today_usd"] - budget["reserved_usd"], 2),
        "left_this_month_usd": round(budget["monthly_cap_usd"] - budget["month_usd"] - budget["reserved_usd"], 2),
    }


@server.tool()
async def start_run(ctx: Context, template_id: str | None = None, fields: dict[str, str] | None = None,
                    inputs: dict[str, str] | None = None, recipe: str | None = None,
                    recipe_input: dict[str, Any] | None = None, title: str | None = None,
                    cap_usd: float | None = None, confirm_cost_usd: float | None = None) -> dict[str, Any]:
    """Start a run (the same arguments as estimate_run). Only after the user agreed to the estimate when it says
    billed. Over the confirmation threshold it's refused unless confirm_cost_usd covers the current estimate."""
    body = await _run_body(template_id, fields, inputs, recipe, recipe_input, title, cap_usd)
    if confirm_cost_usd is not None:
        body["confirm_cost_usd"] = confirm_cost_usd
    return _run(await _api("POST", "/pipelines", json=body), _base(ctx))


@server.tool()
async def get_run(ctx: Context, run_id: str) -> dict[str, Any]:
    """A run's status: steps done, what it's working on, what failed, its result, and any review waiting for the
    user (waiting_for_you: answer with answer_review)."""
    return _run(await _api("GET", f"/pipelines/{quote(run_id, safe='')}"), _base(ctx))


@server.tool()
async def list_runs(ctx: Context, limit: int = 10,
                    status: Literal["running", "waiting", "succeeded", "failed", "canceled"] | None = None
                    ) -> list[dict[str, Any]]:
    """The latest runs, newest first."""
    params: dict[str, Any] = {"limit": max(1, min(limit, 50))}
    if status:
        params["status"] = status
    base = _base(ctx)
    return [{"id": r["id"], "title": r["title"], "recipe": r["recipe_name"], "status": r["status"],
             "spent_usd": r["spent_usd"], "created_at": r["created_at"], "result": _media(r.get("output_asset"), base)}
            for r in await _api("GET", "/pipelines", params=params)]


@server.tool()
async def answer_review(ctx: Context, run_id: str, key: str, choice: str) -> dict[str, Any]:
    """Answer a run's waiting review: `key` from waiting_for_you, `choice` one of its options' values (approve, or
    the picked take's id). Ask the user first; then the run goes on."""
    path = f"/pipelines/{quote(run_id, safe='')}/checkpoints/{quote(key, safe='')}"
    return _run(await _api("POST", path, json={"choice": choice}), _base(ctx))


@server.tool()
async def find_media(ctx: Context, kind: Literal["video", "image", "audio", "model3d", "splat"] | None = None,
                     source: Literal["upload", "generation"] | None = None, favorite: bool | None = None,
                     limit: int = 20) -> list[dict[str, Any]]:
    """The library, newest first: results and uploads with their links and ids (usable as run inputs)."""
    params: dict[str, Any] = {"limit": max(1, min(limit, 100))}
    params |= {k: v for k, v in {"kind": kind, "source": source}.items() if v}
    if favorite is not None:
        params["favorite"] = str(favorite).lower()
    page = await _api("GET", "/assets", params=params)
    base = _base(ctx)
    return [{**(_media(a, base) or {}), "created_at": a["created_at"]} for a in page["items"]]


@server.tool()
async def spend() -> dict[str, Any]:
    """Today's and this month's spend against the caps, the confirmation threshold, and the modes in effect."""
    b = await _api("GET", "/budget")
    keys = ("today_usd", "month_usd", "reserved_usd", "daily_cap_usd", "monthly_cap_usd", "confirm_over_usd")
    return {**{k: b[k] for k in keys}, "mode": b["provider_mode"], "claude_mode": b["llm_mode"]}


@server.tool()
async def upload(ctx: Context, filename: str, content_base64: str) -> dict[str, Any]:
    """Add a picture, clip or sound to the library (to use as a run input). The same size and type rules as the
    studio's own uploads apply."""
    try:
        data = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ToolError("content_base64 isn't valid base64") from exc
    asset = await _api("POST", "/uploads", files={"file": (filename, data)})
    return _media(asset, _base(ctx)) or {}


# ---------------------------------------------------------------- mounting


def _security() -> TransportSecuritySettings:
    """Host and Origin checks against DNS rebinding: this machine, and the host the web app is served from (on a
    server, its tailnet name)."""
    hosts = {"127.0.0.1", "localhost", "[::1]", urlsplit(get_settings().web_origin).hostname or "localhost"}
    return TransportSecuritySettings(
        allowed_hosts=[*hosts, *(f"{h}:*" for h in hosts)],
        allowed_origins=[f"{s}://{h}{p}" for s in ("http", "https") for h in hosts for p in ("", ":*")])


class _Endpoint:
    """`/mcp`, handled by the session manager of the running app (a new one per lifespan: each runs once)."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await server.session_manager.handle_request(scope, receive, send)


route = Route("/mcp", endpoint=_Endpoint())


@asynccontextmanager
async def running() -> AsyncIterator[None]:
    server.streamable_http_app(stateless_http=True, json_response=True, transport_security=_security())
    async with server.session_manager.run():
        yield
