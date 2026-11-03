"""GPT Image through the Codex CLI, with a stand-in `codex` that behaves like the real one: `--json` events on stdout,
the drawn picture in `$CODEX_HOME/generated_images/<session id>/`, the last answer in the `-o` file."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from PIL import Image

from studio.registry.loader import get_registry
from studio.registry.routing import RoutingEnv, candidates
from studio.registry.validate import normalize

FAKE = '''
import json, os, sys
from pathlib import Path
from PIL import Image
args = sys.argv[1:]
if args[:2] == ["login", "status"]:
    print(os.environ.get("FAKE_LOGIN", "Logged in using ChatGPT"))
    sys.exit(0)
Path(os.environ["FAKE_ARGS"]).write_text(json.dumps(args))
Path(os.environ["FAKE_ARGS"] + ".env").write_text(json.dumps(sorted(os.environ)))
answer = Path(args[args.index("-o") + 1])
print(json.dumps({"type": "thread.started", "thread_id": "01a1-test-session"}), flush=True)
if os.environ.get("FAKE_SLOW"):  # draws for a minute, saying it's alive every tenth of a second
    import time
    for _ in range(600):
        Path(os.environ["FAKE_ARGS"] + ".alive").write_text(str(time.time()))
        time.sleep(0.1)
if os.environ.get("FAKE_LIMIT"):
    answer.write_text("You've hit your usage limit. Try again later.")
    sys.exit(1)
folder = Path(os.environ["CODEX_HOME"]) / "generated_images" / "01a1-test-session"
folder.mkdir(parents=True, exist_ok=True)
rollout = Path(os.environ["CODEX_HOME"]) / "sessions" / "2026" / "11" / "02"
rollout.mkdir(parents=True, exist_ok=True)
(rollout / "rollout-2026-11-02T10-00-00-01a1-test-session.jsonl").write_text("{}")  # the transcript, pictures inside
Image.new("RGB", (1536, 1024), (200, 30, 30)).save(folder / "exec-1.png")
answer.write_text("done")
'''


def adapter_with_fake(monkeypatch, tmp_path: Path):
    from studio.config import get_settings
    from studio.providers.codex import CodexAdapter

    fake = tmp_path / "fake_codex.py"
    fake.write_text(FAKE, encoding="utf-8")
    monkeypatch.setattr(get_settings(), "media_root", tmp_path / "media")
    monkeypatch.setattr(get_settings(), "codex_bin", "codex")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("FAKE_ARGS", str(tmp_path / "args.json"))
    adapter = CodexAdapter()
    monkeypatch.setattr(adapter, "command", lambda: [sys.executable, str(fake)])
    return adapter


async def finish(adapter, route, job_id: str, tries: int = 200):
    for _ in range(tries):
        res = await adapter.poll(route, job_id)
        if res.status != "running":
            return res
        await asyncio.sleep(0.05)
    raise AssertionError("the job never finished")


async def test_codex_draws_read_only_crops_to_the_shape_and_clears_its_session(monkeypatch, tmp_path):
    adapter = adapter_with_fake(monkeypatch, tmp_path)
    route = get_registry().model("gpt-image-2.5").route("gpt-image-2.5@codex")
    ref = tmp_path / "ref.png"
    Image.new("RGB", (64, 64)).save(ref)
    job = (await adapter.submit(route, "edit", {"prompt": "a red apple", "aspect_ratio": "9:16", "quality": "high",
                                                "images": [str(ref)]}, client_ref="c")).job_id
    res = await finish(adapter, route, job)
    assert res.status == "succeeded" and len(res.outputs) == 1, res.error_message
    w, h = Image.open(res.outputs[0].ref).size
    assert abs(w / h - 9 / 16) < 0.01  # Codex drew 3:2; cropped to the asked shape
    args = json.loads((tmp_path / "args.json").read_text())
    assert args[:2] == ["exec", "--json"] and args[args.index("-s") + 1] == "read-only"  # never writes or bypasses
    assert {"features.shell_tool=false", "features.unified_exec=false"} <= set(args)  # nor reads files with a shell
    assert {"features.hooks=false", "features.plugins=false", 'web_search="disabled"'} <= set(args)  # nor runs
    # the user's own hooks and plugins, or searches the web
    assert args[args.index("-i") + 1] == str(ref) and "a red apple" in args[-1] and "9:16" in args[-1]
    assert not (tmp_path / "codex-home" / "generated_images" / "01a1-test-session").exists()
    assert not list((tmp_path / "codex-home" / "sessions").rglob("*01a1-test-session*"))  # megabytes a picture


async def test_codex_never_sees_the_studios_secrets(monkeypatch, tmp_path):
    adapter = adapter_with_fake(monkeypatch, tmp_path)
    secrets = ("FAL_KEY", "DATABASE_URL", "QUEUE_DATABASE_URL", "KEYS_SECRET", "PGPASSWORD", "GH_TOKEN")
    for name in secrets:
        monkeypatch.setenv(name, "s3cret")
    route = get_registry().model("gpt-image-2.5").route("gpt-image-2.5@codex")
    job = (await adapter.submit(route, "t2i", {"prompt": "x", "aspect_ratio": "1:1"}, client_ref="c")).job_id
    assert (await finish(adapter, route, job)).status == "succeeded"
    seen = set(json.loads((tmp_path / "args.json.env").read_text()))
    assert not seen & set(secrets)  # a prompt telling the agent to print its environment finds no key
    assert {"PATH", "CODEX_HOME"} <= {n.upper() for n in seen}  # what it needs to run and find its sign-in


async def test_codex_without_a_picture_moves_on_to_the_next_route(monkeypatch, tmp_path):
    adapter = adapter_with_fake(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_LIMIT", "1")
    route = get_registry().model("gpt-image-2.5").route("gpt-image-2.5@codex")
    job = (await adapter.submit(route, "t2i", {"prompt": "x", "aspect_ratio": "1:1"}, client_ref="c")).job_id
    res = await finish(adapter, route, job)
    assert res.status == "failed" and res.error_class.value == "route_unavailable"  # a failover, not a dead end
    assert "usage limit" in (res.error_message or "")


async def test_a_codex_that_runs_too_long_is_stopped_and_its_session_still_cleared(monkeypatch, tmp_path):
    adapter = adapter_with_fake(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_SLOW", "1")
    monkeypatch.setattr("studio.providers.codex.TIMEOUT_S", 0)  # stopped at its first check, two seconds in
    drawn = tmp_path / "codex-home" / "generated_images" / "01a1-test-session"
    drawn.mkdir(parents=True)
    Image.new("RGB", (8, 8)).save(drawn / "exec-1.png")  # what it drew before it was stopped
    route = get_registry().model("gpt-image-2.5").route("gpt-image-2.5@codex")
    job = (await adapter.submit(route, "t2i", {"prompt": "x", "aspect_ratio": "1:1"}, client_ref="c")).job_id
    res = await finish(adapter, route, job)
    assert res.status == "failed" and res.error_class.value == "timeout", res.error_message
    assert not drawn.exists()


def runner_mode(monkeypatch, tmp_path: Path):
    """The worker's adapter with CODEX_RUNNER on: it parks jobs and must never run Codex itself."""
    from studio.config import get_settings

    adapter = adapter_with_fake(monkeypatch, tmp_path)
    monkeypatch.setattr(get_settings(), "codex_runner", True)
    monkeypatch.setattr(adapter, "command", lambda: pytest.fail("the worker never runs Codex in runner mode"))
    return adapter


@asynccontextmanager
async def runner(monkeypatch, tmp_path: Path):
    """The Codex runner on the worker's job folder, with its own adapter (another process's), drawing with the
    stand-in."""
    from studio.providers.codex import CodexAdapter
    from studio.providers.codex_runner import serve

    drawer = CodexAdapter()
    monkeypatch.setattr(drawer, "command", lambda: [sys.executable, str(tmp_path / "fake_codex.py")])
    stop = asyncio.Event()
    task = asyncio.create_task(serve(drawer, stop))
    for _ in range(100):  # its first heartbeat
        if (drawer.store.root / "runner.json").exists():
            break
        await asyncio.sleep(0.05)
    try:
        yield drawer
    finally:
        stop.set()
        await asyncio.wait_for(task, 10)


async def test_in_runner_mode_codex_draws_in_the_runner_and_a_restarted_worker_still_gets_it(monkeypatch, tmp_path):
    from studio.providers.codex import CodexAdapter

    adapter = runner_mode(monkeypatch, tmp_path)
    route = get_registry().model("gpt-image-2.5").route("gpt-image-2.5@codex")
    ref = tmp_path / "ref.png"
    Image.new("RGB", (64, 64)).save(ref)
    async with runner(monkeypatch, tmp_path) as drawer:
        assert await adapter.reachable()
        job = (await adapter.submit(route, "edit", {"prompt": "a red apple", "aspect_ratio": "9:16",
                                                    "images": [str(ref)]}, client_ref="c")).job_id
        res = await finish(CodexAdapter(), route, job, tries=600)  # a worker restarted meanwhile polls the same job
    assert res.status == "succeeded" and len(res.outputs) == 1, res.error_message
    w, h = Image.open(res.outputs[0].ref).size
    assert abs(w / h - 9 / 16) < 0.01
    args = json.loads((tmp_path / "args.json").read_text())
    assert Path(args[args.index("-i") + 1]) == drawer.store.root / job / "ref-1.png"  # a copy: the runner sees no media
    assert args[args.index("-s") + 1] == "read-only"
    assert not (tmp_path / "codex-home" / "generated_images" / "01a1-test-session").exists()


async def test_with_its_runner_down_codex_steps_aside_for_the_next_route(monkeypatch, tmp_path):
    from studio.providers.base import ProviderError

    adapter = runner_mode(monkeypatch, tmp_path)
    route = get_registry().model("gpt-image-2.5").route("gpt-image-2.5@codex")
    assert not await adapter.reachable()  # routing skips it, so the estimate shows the route that will draw
    with pytest.raises(ProviderError) as refused:  # and a take that gets here anyway moves on at once
        await adapter.submit(route, "t2i", {"prompt": "x", "aspect_ratio": "1:1"}, client_ref="c")
    assert refused.value.error_class.value == "route_unavailable" and "isn't running" in refused.value.message


async def test_routing_asks_the_codex_runner_but_no_remote_api(monkeypatch, tmp_path):
    from studio.providers.local_status import local_state

    runner_mode(monkeypatch, tmp_path)
    reach, blocked = await local_state(get_registry())
    assert reach["codex"] is False  # `probe: true` in its provider YAML, and no runner heartbeat: routing skips it
    assert "fal" not in reach and not any(r.startswith("gpt-image-2.5@") for r, _ in blocked)  # SDKs stay unloaded
    async with runner(monkeypatch, tmp_path):  # its heartbeat, signed in with ChatGPT
        reach, _ = await local_state(get_registry())
    assert reach["codex"] is True


async def test_a_runner_signed_in_with_an_api_key_draws_nothing(monkeypatch, tmp_path):
    adapter = runner_mode(monkeypatch, tmp_path)
    route = get_registry().model("gpt-image-2.5").route("gpt-image-2.5@codex")
    async with runner(monkeypatch, tmp_path):
        assert await adapter.reachable()  # its first check saw ChatGPT
        # signed in with an API key within the runner's minute: only its check before each picture can see it
        monkeypatch.setenv("FAKE_LOGIN", "Logged in using an API key - sk-proj-***abcd")
        job = adapter.park({"prompt": "x", "aspect_ratio": "1:1"}, {}).job_id
        res = await finish(adapter, route, job)
        for _ in range(100):  # and its next heartbeat says so: it would bill per picture, outside the caps
            if not await adapter.reachable():
                break
            await asyncio.sleep(0.05)
        assert not await adapter.reachable()
    assert res.status == "failed" and res.error_class.value == "route_unavailable"
    assert not (tmp_path / "args.json").exists()  # Codex never drew


async def test_a_canceled_take_stops_codex_in_the_runner(monkeypatch, tmp_path):
    adapter = runner_mode(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_SLOW", "1")
    for where in ("local_tools", "codex"):  # its heartbeat sees the cancel before Codex's own check does
        monkeypatch.setattr(f"studio.providers.{where}.HEARTBEAT_S", 0.05)
    route = get_registry().model("gpt-image-2.5").route("gpt-image-2.5@codex")
    async with runner(monkeypatch, tmp_path) as drawer:
        job = (await adapter.submit(route, "t2i", {"prompt": "x", "aspect_ratio": "1:1"}, client_ref="c")).job_id
        folder = drawer.store.root / job
        for _ in range(200):  # the runner took it and Codex is drawing
            if (folder / "codex.log").exists():
                break
            await asyncio.sleep(0.05)
        assert (folder / "job.claimed").exists()
        assert await adapter.cancel(route, job)
        for _ in range(300):  # well before the stand-in's 60 s
            if not folder.exists():
                break
            await asyncio.sleep(0.05)
        assert not folder.exists()  # the runner cleared the job...
        alive = tmp_path / "args.json.alive"
        said = alive.read_text()
        await asyncio.sleep(0.5)
        assert alive.read_text() == said  # ...after Codex stopped, never with it still drawing


async def test_a_job_the_runner_was_drawing_when_it_stopped_is_drawn_again(monkeypatch, tmp_path):
    adapter = runner_mode(monkeypatch, tmp_path)
    route = get_registry().model("gpt-image-2.5").route("gpt-image-2.5@codex")
    job = adapter.park({"prompt": "x", "aspect_ratio": "1:1"}, {}).job_id
    folder = adapter.store.root / job
    os.rename(folder / "job.json", folder / "job.claimed")  # taken by a runner that then restarted
    async with runner(monkeypatch, tmp_path):
        res = await finish(adapter, route, job, tries=600)
    assert res.status == "succeeded", res.error_message  # free, so drawn again rather than paid for on fal


@pytest.mark.skipif(sys.platform == "win32", reason="no O_NOFOLLOW or FIFOs; the runner is a Linux container")
def test_the_worker_never_follows_a_link_or_waits_on_a_pipe_in_a_job_folder(monkeypatch, tmp_path):
    from studio.config import get_settings
    from studio.providers.base import ProviderError, ProviderOutput
    from studio.providers.codex import CodexAdapter, runner_state
    from studio.providers.common import ResultStore, touch_flag

    monkeypatch.setattr(get_settings(), "media_root", tmp_path / "media")
    store = ResultStore("codex")
    job, folder = store.new_job()
    secret = tmp_path / "keys.secret"
    secret.write_text('{"status": "succeeded"}')  # one of the worker's own files, which the runner can't see
    Image.new("RGB", (8, 8)).save(folder / "output-1.png")
    (folder / "output-2.png").symlink_to(secret)
    (folder / "meta.json").symlink_to(secret)
    (folder / "cancel").symlink_to(tmp_path / "planted")
    assert [Path(o.ref or "").name for o in store.outputs(job, "image")] == ["output-1.png"]
    with pytest.raises(OSError):
        store.meta(job)
    with pytest.raises(OSError):  # swapped for a link after it was listed
        store.copy_out(ProviderOutput(index=0, kind="image", ref=str(folder / "output-2.png")), tmp_path / "out.png")
    touch_flag(folder / "cancel")
    assert not (tmp_path / "planted").exists()
    os.mkfifo(store.root / "runner.json")  # a pipe would hang the worker's event loop on read
    assert runner_state(store.root) is None
    _, other = store.new_job()
    os.mkfifo(other / "meta.json")
    with pytest.raises(OSError):
        store.meta(other.name)
    parker, ref = CodexAdapter(), tmp_path / "ref.png"
    Image.new("RGB", (8, 8)).save(ref)
    _, planted = store.new_job()
    (planted / "ref-1.png").symlink_to(secret)  # left there before the worker copies its reference in
    monkeypatch.setattr(parker.store, "new_job", lambda: (planted.name, planted))
    with pytest.raises(ProviderError) as refused:  # never written through: the take moves on to fal
        parker.park({"prompt": "x", "images": [str(ref)]}, {})
    assert refused.value.error_class.value == "route_unavailable"
    assert secret.read_text() == '{"status": "succeeded"}'


def test_codex_is_the_free_first_choice_when_configured_and_never_runs_in_mock_mode():
    reg = get_registry()
    spec = reg.model("gpt-image-2.5")
    req = normalize(spec, "t2i", "a red apple", {}, [])

    def env(mode: str, codex: bool) -> RoutingEnv:
        return RoutingEnv(provider_mode=mode, live_allowlist=set(), has_adapter=lambda _a: True,  # type: ignore[arg-type]
                          has_key=lambda p: p != "codex" or codex)

    live = [c.route.id for c in candidates(reg, spec, req, env("live", True)) if c.eligible]
    assert live[:2] == ["gpt-image-2.5@codex", "gpt-image-2.5@fal"]  # free first, fal behind it for the failover
    assert next(c.route.id for c in candidates(reg, spec, req, env("live", False)) if c.eligible) == "gpt-image-2.5@fal"
    assert [c.route.id for c in candidates(reg, spec, req, env("mock", True)) if c.eligible] == ["gpt-image-2.5@mock"]
    down = env("live", True)
    down.reachable = lambda p: p != "codex"  # its runner's container is down: the estimate shows fal's price
    cands = {c.route.id: c for c in candidates(reg, spec, req, down)}
    assert "isn't ready" in (cands["gpt-image-2.5@codex"].reason or "") and cands["gpt-image-2.5@fal"].eligible
