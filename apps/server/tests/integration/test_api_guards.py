"""What the API turns away before a route runs: other names for this machine (DNS rebinding), posts from other
websites, bodies too large to read, 3D files named as web pages, and callers without the access token."""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest

from studio.api import auth, mcp_tools
from studio.api.main import app as api
from studio.config import Settings, get_settings

KEYFRAME = Path(__file__).resolve().parents[4] / "fixtures" / "images" / "keyframe.png"


@pytest.fixture
async def client(db, in_memory_queue):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url="http://localhost:8400"
    ) as c:
        yield c


async def test_only_this_machines_names_are_answered(client):
    for host in ("localhost:8400", "127.0.0.1:8400", "[::1]:8400", "localhost"):
        assert (await client.get("/budget", headers={"host": host})).status_code == 200, host
    # a page that rebinds its own name to 127.0.0.1 is same-origin with the API: turned away by name, any port
    for host in ("evil.example", "evil.example:8400"):
        assert (await client.get("/budget", headers={"host": host})).status_code == 400, host
    # the MCP tools' in-process calls
    assert (await mcp_tools._api("GET", "/budget"))["provider_mode"] == "mock"


async def test_other_websites_cant_post_but_the_web_app_and_scripts_can(client):
    files = {"file": ("k.png", KEYFRAME.read_bytes(), "image/png")}
    for origin in ("https://evil.example", "null"):
        r = await client.post("/uploads", files=files, headers={"origin": origin})
        assert r.status_code == 403, origin
    # the web app, and the API's own /docs
    for origin in (get_settings().web_origin, "http://localhost:8400"):
        r = await client.post("/uploads", files=files, headers={"origin": origin})
        assert r.status_code == 201, (origin, r.text)
    asset = r.json()["id"]
    assert (
        await client.delete(f"/assets/{asset}", headers={"origin": "https://evil.example"})
    ).status_code == 403
    # no Origin: curl, scripts, MCP clients
    assert (await client.delete(f"/assets/{asset}")).status_code == 204


async def test_a_large_or_unsized_body_is_refused_before_it_is_read(client):
    big = {"content-length": str(64 << 20), "content-type": "text/plain"}
    assert (await client.post("/elements", content=b"{}", headers=big)).status_code == 413
    # a JSON route reads a body labelled multipart whole too: only the file routes may send more
    form = {**big, "content-type": "multipart/form-data; boundary=x"}
    for path in ("/elements", f"/assets/{uuid.uuid4()}/frames"):
        assert (await client.post(path, content=b"--x--", headers=form)).status_code == 413, path
    path = f"/projects/{uuid.uuid4()}/renders/{uuid.uuid4()}/frames?pass=beauty"
    assert (await client.post(path, content=b"--x--", headers=form)).status_code != 413

    async def chunked():
        yield b"{}"

    r = await client.post("/elements", content=chunked(), headers={"content-type": "application/json"})
    assert r.status_code == 411


async def test_3d_files_are_stored_as_what_they_are_not_by_the_name_sent(client):
    glb = b"glTF\x02\x00\x00\x00" + b"\x00" * 16
    r = await client.post("/uploads", files={"file": ("x.html", glb, "model/gltf-binary")})
    assert r.status_code == 201, r.text
    assert r.json()["url"].endswith("original.glb") and r.json()["mime"] == "model/gltf-binary"
    # a splat is one only by its .spz or .ply name, so a page with a splat's first bytes is no splat
    splat = {"file": ("x.html", b"ply\nformat binary_little_endian 1.0\n", "application/octet-stream")}
    assert (await client.post("/uploads", files=splat)).status_code == 415
    r = await client.post(
        "/uploads", files={"file": ("x.PLY", b"ply\nformat binary_little_endian 1.0\n", "text/html")}
    )
    assert r.status_code == 201 and r.json()["url"].endswith("original.ply")


async def test_with_an_access_token_every_route_asks_for_it(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "access_token", "open-sesame")
    web = {"origin": get_settings().web_origin}
    for path in ("/budget", "/assets", "/docs", "/openapi.json", "/events/stream", f"/media/u/{uuid.uuid4()}/x.png"):
        r = await client.get(path, headers=web)
        assert r.status_code == 401, path
        # CORS still answers, so the web app can read the refusal and show its sign-in
        assert r.headers.get("access-control-allow-origin") == web["origin"], path
    assert (await client.post("/elements", json={}, headers=web)).status_code == 401
    # the health check answers without it, but only that the studio is up
    assert (await client.get("/health")).json() == {"status": "ok"}
    assert (await client.get("/auth/session")).json() == {"required": True, "signed_in": False}

    # scripts and MCP clients send it as a header
    bearer = {"authorization": "Bearer open-sesame"}
    assert (await client.get("/budget", headers=bearer)).status_code == 200
    assert "provider_mode" in (await client.get("/health", headers=bearer)).json()
    assert (await client.get("/budget", headers={"authorization": "Bearer open-sesam"})).status_code == 401
    # the MCP tools' in-process calls pass it on
    assert (await mcp_tools._api("GET", "/budget"))["provider_mode"] == "mock"


async def test_a_browser_signs_in_once_and_keeps_a_cookie(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "access_token", "open-sesame")
    web = {"origin": get_settings().web_origin}
    assert (await client.post("/auth/session", json={"token": "nope"}, headers=web)).status_code == 401
    assert auth.COOKIE not in client.cookies
    r = await client.post("/auth/session", json={"token": "open-sesame"}, headers=web)
    assert r.status_code == 200 and r.json() == {"required": True, "signed_in": True}
    set_cookie = r.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=lax" in set_cookie
    # the cookie never holds the token: every service on this host name sees it
    assert "open-sesame" not in set_cookie and client.cookies[auth.COOKIE] == auth.session_value("open-sesame")
    assert (await client.get("/budget", headers=web)).status_code == 200
    assert (await client.get("/auth/session")).json()["signed_in"] is True
    # but /mcp skips the Origin check, so a cookie never opens it
    hello = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    assert (await client.post("/mcp", json=hello)).status_code == 401
    # a new token signs every browser out
    monkeypatch.setattr(get_settings(), "access_token", "new-sesame")
    assert (await client.get("/budget", headers=web)).status_code == 401
    monkeypatch.setattr(get_settings(), "access_token", "open-sesame")
    r = await client.delete("/auth/session", headers=web)
    assert r.json() == {"required": True, "signed_in": False} and auth.COOKIE not in client.cookies
    assert (await client.get("/budget", headers=web)).status_code == 401


async def test_without_an_access_token_nothing_asks_for_one(client):
    assert (await client.get("/auth/session")).json() == {"required": False, "signed_in": True}
    assert (await client.get("/budget")).status_code == 200


def test_the_token_is_read_as_the_callers_send_it():
    # a unit's EnvironmentFile keeps a trailing space; callers' tokens are stripped, so the setting is too
    assert Settings(access_token=" open-sesame	").access_token == "open-sesame"
    for bad in ("open sesame", "contraseña"):  # a header can't carry either
        with pytest.raises(ValueError):
            Settings(access_token=bad)
