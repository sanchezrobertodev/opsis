"""GPT Image through the Codex CLI, with a stand-in `codex` that behaves like the real one: `--json` events on stdout,
the drawn picture in `$CODEX_HOME/generated_images/<session id>/`, the last answer in the `-o` file."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from PIL import Image

from studio.registry.loader import get_registry
from studio.registry.routing import RoutingEnv, candidates
from studio.registry.validate import normalize

FAKE = '''
import json, os, sys
from pathlib import Path
from PIL import Image
args = sys.argv[1:]
Path(os.environ["FAKE_ARGS"]).write_text(json.dumps(args))
Path(os.environ["FAKE_ARGS"] + ".env").write_text(json.dumps(sorted(os.environ)))
answer = Path(args[args.index("-o") + 1])
print(json.dumps({"type": "thread.started", "thread_id": "01a1-test-session"}))
if os.environ.get("FAKE_LIMIT"):
    answer.write_text("You've hit your usage limit. Try again later.")
    sys.exit(1)
folder = Path(os.environ["CODEX_HOME"]) / "generated_images" / "01a1-test-session"
folder.mkdir(parents=True, exist_ok=True)
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


async def finish(adapter, route, job_id: str):
    for _ in range(200):
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
    assert args[args.index("-i") + 1] == str(ref) and "a red apple" in args[-1] and "9:16" in args[-1]
    assert not (tmp_path / "codex-home" / "generated_images" / "01a1-test-session").exists()


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
