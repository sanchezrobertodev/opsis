"""GPT Image through the Codex CLI, signed in with a ChatGPT plan: pictures come out of the plan's allowance instead of
per-image API billing (`CODEX_BIN`, the `codex` binary; empty turns the route off).

Each job runs `codex exec --json` read-only (the agent never writes or runs anything that changes files) with the
prompt and any reference pictures attached. Codex keeps what its image tool draws in
`$CODEX_HOME/generated_images/<session id>/`, the id coming from the `thread.started` event; those pictures are
cropped to the asked aspect ratio (Codex picks the size) into the job's folder as `output-N.png`, and the session's
folder is cleared. A run that saves no picture (a refusal, the plan's usage limit) fails as `route_unavailable`,
so the take moves on to the next route (fal). Slow: an agent run of a minute or two a picture.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import anyio

from studio.config import get_settings
from studio.domain.enums import ErrorClass
from studio.media.ffmpeg import ffmpeg
from studio.providers.base import ProviderError, ResolvedMedia, SubmitResult
from studio.providers.local_tools import BackgroundJobAdapter
from studio.registry.schema import Route

log = logging.getLogger(__name__)

TIMEOUT_S = 900  # a job past this is stopped (several pictures, a slow plan)
PICTURES = (".png", ".jpg", ".jpeg", ".webp")

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

    async def stage_inputs(self, route: Route, media: list[ResolvedMedia]) -> dict[str, Any]:
        staged: dict[str, Any] = {}
        for m in media:
            staged.setdefault(m.role, []).append(str(m.path))
        return staged

    async def submit(self, route: Route, endpoint: str, payload: dict[str, Any], *, client_ref: str) -> SubmitResult:
        if not get_settings().codex_bin:
            raise ProviderError(ErrorClass.route_unavailable, "Codex isn't configured (CODEX_BIN)")
        return self.start(payload, {"mode": endpoint})

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
        cmd = [*self.command(), "exec", "--json", "--skip-git-repo-check", "-s", "read-only", "-C", str(workdir),
               "-o", str(workdir / "answer.txt"), *(a for r in refs for a in ("-i", r)), ask]
        code = await anyio.to_thread.run_sync(self._exec, cmd, workdir)
        session = _session(workdir / "codex.log")
        folder = codex_home() / "generated_images" / session if session else None
        drawn = sorted((p for p in folder.iterdir() if p.suffix.lower() in PICTURES),
                       key=lambda p: p.stat().st_mtime)[:n] if folder and folder.is_dir() else []
        if not drawn:
            answer = (workdir / "answer.txt").read_text(encoding="utf-8", errors="replace").strip() \
                if (workdir / "answer.txt").exists() else ""
            raise ProviderError(ErrorClass.route_unavailable,
                                f"Codex saved no picture (exit {code}): {answer[:300] or 'no answer'}")
        w, h = (float(x) for x in aspect.split(":"))
        for i, src in enumerate(drawn, 1):  # Codex picks the size: crop to the asked shape, centred
            await ffmpeg("-i", str(src), "-vf", f"crop='min(iw,ih*{w}/{h})':'min(ih,iw*{h}/{w})'", "-frames:v", "1",
                         str(workdir / f"output-{i}.png"))
        if folder:
            shutil.rmtree(folder, ignore_errors=True)  # this job's session only: its pictures are copied out

    async def cancel(self, route: Route, job_id: str) -> bool:
        """Always through the `cancel` file: the job's own heartbeat stops its task and `_exec` stops Codex, then the
        job clears its folder (a thread can't be interrupted, so the task can't simply be canceled here)."""
        if self._flag(job_id, "running").exists():
            self._flag(job_id, "cancel").touch()
        else:
            self.store.discard(job_id)
        return True

    def _exec(self, cmd: list[str], workdir: Path) -> int:
        """Run Codex, stopping it when the job is canceled (the `cancel` file) or runs too long."""
        started = time.monotonic()
        with open(workdir / "codex.log", "wb") as out:
            proc = subprocess.Popen(cmd, cwd=workdir, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
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


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def _session(events: Path) -> str | None:
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
