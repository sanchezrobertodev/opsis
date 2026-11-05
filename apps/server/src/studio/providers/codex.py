"""GPT Image through the Codex CLI, signed in with a ChatGPT plan: pictures come out of the plan's allowance instead of
per-image API billing (`CODEX_BIN`, the `codex` binary; empty turns the route off).

Each job runs `codex exec --json` read-only (the agent never writes or runs anything that changes files) with the
prompt and any reference pictures attached. Codex keeps what its image tool draws in
`$CODEX_HOME/generated_images/<session id>/`, the id coming from the `thread.started` event; those pictures are
cropped to the asked aspect ratio (Codex picks the size) into the job's folder as `output-N.png`, and the session's
folder is cleared. A run that saves no picture (a refusal, the plan's usage limit) fails as `route_unavailable`,
so the take moves on to the next route (fal). Slow: an agent run of a minute or two a picture.

With `CODEX_RUNNER` on, the worker never runs Codex. Submit parks the job in its folder (`job.json`, with the
reference pictures copied in beside it) for the runner in its own container (`codex_runner.py`, infra/server's
`opsis-codex`), which sees only these folders and its own Codex home, never the studio's keys, media or database.
The runner runs the job as the worker would without it (the `running` heartbeat, the `cancel` file, the crop,
`meta.json`), so poll, cancel and download don't change and a worker restarted mid-draw still gets the picture.
`runner.json` beside the jobs is the runner's heartbeat with its sign-in.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import anyio

from studio.config import Settings, get_settings
from studio.domain.enums import ErrorClass
from studio.media.ffmpeg import ffmpeg
from studio.providers.base import ProviderError, ResolvedMedia, SubmitResult
from studio.providers.common import open_plain, touch_flag, write_json
from studio.providers.local_tools import HEARTBEAT_S, STALE_S, BackgroundJobAdapter
from studio.registry.schema import Route

log = logging.getLogger(__name__)

TIMEOUT_S = 900  # a job past this is stopped (several pictures, a slow plan)
PICTURES = (".png", ".jpg", ".jpeg", ".webp")
SECRETS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIALS")  # env names never passed to Codex
# only its image tool is wanted: turning off its shell, browser, connectors, plugins, hooks, sub-agents, memories,
# web search and notify command leaves a prompt (or a picture) fewer ways to make it read a file such as `.env` or
# reach the network. `-c`, not `--disable`, so a Codex without one of these features ignores it rather than refusing
# to start. shortcut: Codex 0.160 kept `unified_exec` on whatever it was told (0.163 drops the shell), so without
# CODEX_RUNNER a Codex like that can still read what the worker can (the Windows dev setup); the runner is the fix.
NO_TOOLS = (*(a for f in ("shell_tool", "unified_exec", "browser_use", "in_app_browser", "computer_use", "apps",
                          "plugins", "hooks", "multi_agent", "memories")
              for a in ("-c", f"features.{f}=false")),
            "-c", 'web_search="disabled"', "-c", "notify=[]")

ASK = """Generate {count} with your image generation tool. Aspect ratio {aspect}{quality}.{refs} Follow the prompt
exactly; don't add text that it doesn't ask for. Don't run commands, write files or ask questions: if you can't make
it, say why in one sentence.

Prompt:
{prompt}"""


class CodexAdapter(BackgroundJobAdapter):
    key = "codex"
    output_suffixes = (".png",)

    def command(self) -> list[str]:
        return [get_settings().codex_bin]

    async def reachable(self) -> bool:
        """In runner mode, whether the runner is up and signed in with ChatGPT, so routing skips Codex (and the
        estimate shows the route that will really draw) while it isn't."""
        return not get_settings().codex_runner or (runner_state(self.store.root) or {}).get("login") == "chatgpt"

    async def stage_inputs(self, route: Route, media: list[ResolvedMedia]) -> dict[str, Any]:
        staged: dict[str, Any] = {}
        for m in media:
            staged.setdefault(m.role, []).append(str(m.path))
        return staged

    async def submit(self, route: Route, endpoint: str, payload: dict[str, Any], *, client_ref: str) -> SubmitResult:
        if not get_settings().codex_bin:
            raise ProviderError(ErrorClass.route_unavailable, "Codex isn't configured (CODEX_BIN)")
        if not get_settings().codex_runner:
            return self.start(payload, {"mode": endpoint})
        if not await self.reachable():
            raise ProviderError(ErrorClass.route_unavailable, "the Codex runner isn't running or isn't signed in with "
                                                              "ChatGPT (systemctl --user status opsis-codex)")
        return self.park(payload, {"mode": endpoint})

    def park(self, payload: dict[str, Any], raw: dict[str, Any]) -> SubmitResult:
        """Leave the job for the runner: the reference pictures copied into its folder (the runner sees no media),
        then `job.json` last, so the runner never takes half a job. Every file is created new (`x`, O_EXCL): the
        runner can write here, so a link it left in the folder is refused, never written through."""
        job_id = None
        try:
            job_id, folder = self.store.new_job()
            refs = []
            for i, ref in enumerate(payload.get("images") or [], 1):
                refs.append(str(folder / f"ref-{i}{Path(ref).suffix.lower() or '.png'}"))
                with open(ref, "rb") as src, open(refs[-1], "xb") as dst:
                    shutil.copyfileobj(src, dst)
            (folder / "running").touch(exist_ok=False)  # the runner keeps it fresh; gone stale, the take moves on
            write_json(folder / "job.json", {**payload, "images": refs})
        except OSError as exc:  # nothing reached the runner: the next route, not an `unknown` take
            if job_id:
                self.store.discard(job_id)
            raise ProviderError(ErrorClass.route_unavailable, f"couldn't hand the job to the Codex runner: {exc}") from exc
        return SubmitResult(job_id=job_id, raw=raw)

    async def work(self, job_id: str, workdir: Path, payload: dict[str, Any]) -> None:
        refs = [str(p) for p in payload.get("images") or []]
        n = max(1, int(payload.get("num_images") or 1))
        aspect = str(payload.get("aspect_ratio") or "1:1")
        quality = payload.get("quality")
        ask = ASK.format(
            count="one picture" if n == 1 else f"{n} separate pictures",
            aspect=aspect, quality=f", {quality} quality" if quality else "",
            refs=(f" Use the {len(refs)} attached pictures as references, in order (Image 1 is the first)." if refs else ""),
            prompt=payload.get("prompt", ""))
        cmd = [*self.command(), "exec", "--json", "--skip-git-repo-check", "-s", "read-only", *NO_TOOLS,
               "-C", str(workdir), "-o", str(workdir / "answer.txt"), *(a for r in refs for a in ("-i", r)),
               "--", ask]  # --image takes several files: without "--" it swallows the prompt
        session = None
        try:
            code = await anyio.to_thread.run_sync(self._exec, cmd, workdir)
            session = session_id(workdir / "codex.log")
            if code == -1:
                raise ProviderError(ErrorClass.route_unavailable, "canceled")  # its folder is cleared after this
            folder = codex_home() / "generated_images" / session if session else None
            drawn = sorted((p for p in folder.iterdir() if p.suffix.lower() in PICTURES),
                           key=lambda p: p.stat().st_mtime)[:n] if folder and folder.is_dir() else []
            if not drawn:
                answer = (workdir / "answer.txt").read_text(encoding="utf-8", errors="replace").strip() \
                    if (workdir / "answer.txt").exists() else ""
                # quoted: what Codex says is shaped by the prompt, so an agent reading the take sees data, not orders
                said = f'; it said: "{answer[:300]}"' if answer else " and gave no answer"
                raise ProviderError(ErrorClass.route_unavailable, f"Codex saved no picture (exit {code}){said}")
            w, h = (float(x) for x in aspect.split(":"))
            for i, src in enumerate(drawn, 1):  # Codex picks the size: crop to the asked shape, centred
                await ffmpeg("-i", str(src), "-vf", f"crop='min(iw,ih*{w}/{h})':'min(ih,iw*{h}/{w})'", "-frames:v",
                             "1", str(workdir / f"output-{i}.png"))
        finally:  # this job's session only: its pictures are copied out by now (a timeout stops Codex before raising)
            clear_session(session or session_id(workdir / "codex.log"))

    async def _heartbeat(self, job_id: str) -> None:
        """Beats until the job is canceled, but never cancels its task: `_exec` sees the `cancel` file and stops Codex
        itself, where canceling the task would clear the folder (and the file) with Codex still drawing."""
        running, cancel = self._flag(job_id, "running"), self._flag(job_id, "cancel")
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            if cancel.exists():
                return
            running.touch()

    async def cancel(self, route: Route, job_id: str) -> bool:
        """Always through the `cancel` file: the job's own heartbeat stops its task and `_exec` stops Codex, then the
        job clears its folder (a thread can't be interrupted, so the task can't simply be canceled here)."""
        if self._flag(job_id, "running").exists():
            touch_flag(self._flag(job_id, "cancel"))
        else:
            self.store.discard(job_id)
        return True

    def _exec(self, cmd: list[str], workdir: Path) -> int:
        """Run Codex, stopping it when the job is canceled (the `cancel` file) or runs too long."""
        started = time.monotonic()
        with open(workdir / "codex.log", "wb") as out:
            proc = subprocess.Popen(
                cmd, cwd=workdir, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=codex_env()
            )
            while True:
                try:
                    return proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    if (workdir / "cancel").exists() or time.monotonic() - started > TIMEOUT_S:
                        proc.kill()
                        proc.wait()
                        if (workdir / "cancel").exists():
                            return -1
                        raise ProviderError(ErrorClass.timeout, f"Codex took longer than {TIMEOUT_S // 60} minutes") from None


def codex_env() -> dict[str, str]:
    """The worker's environment without the studio's settings and anything named like a secret, so the agent never
    starts with a key or the database password (it keeps PATH, home, temp and proxy settings)."""
    studio = {name.upper() for name in Settings.model_fields}
    return {
        k: v
        for k, v in os.environ.items()
        if k.upper() not in studio and not k.upper().startswith("PG") and not k.upper().endswith(SECRETS)
    }


def login_state(said: str) -> str:
    """What `codex login status` printed, as `chatgpt`, `api_key` or `signed_out` (never echo the line: with an API
    key it shows part of the key)."""
    if "ChatGPT" in said:
        return "chatgpt"
    return "api_key" if "API key" in said else "signed_out"


def runner_state(root: Path) -> dict[str, Any] | None:
    """The runner's last heartbeat in the Codex job folder (`{"login": ...}`), or None when it's stale or missing."""
    f = root / "runner.json"
    try:
        if time.time() - f.lstat().st_mtime > STALE_S:
            return None
        with open_plain(f) as fh:
            data = json.loads(fh.read(64_000))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def clear_session(session: str | None) -> None:
    """What a job's Codex session leaves in its home: the pictures its image tool saved (copied out by now) and its
    transcript, which holds every picture again as base64 (megabytes each)."""
    if not session:
        return
    shutil.rmtree(codex_home() / "generated_images" / session, ignore_errors=True)
    for f in codex_home().glob(f"sessions/*/*/*/rollout-*{session}.jsonl*"):
        f.unlink(missing_ok=True)


def session_id(events: Path) -> str | None:
    """The session id from Codex's `--json` events (the folder its image tool saves into)."""
    if not events.exists():
        return None
    for line in events.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "thread.started" and event.get("thread_id"):
            tid = str(event["thread_id"])
            return tid if tid.replace("-", "").isalnum() else None  # a folder name, never a path
    return None
