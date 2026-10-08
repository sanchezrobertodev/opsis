"""wan-core RunPod worker: run one ComfyUI API workflow and return what it saved.

Replaces worker-comfyui's /handler.py (its start.sh still launches ComfyUI first, then this file). Written for
ai-media-studio's `runpod_comfy` adapter; the contract is plain JSON so anything else can call it too.

Input (job["input"]):
  action:   "run" (default) | "check" | "fetch_models"
  workflow: ComfyUI API-format graph (action=run)
  inputs:   [{"name": "start.png", "url": "https://..."} | {"name": "start.png", "data": "<base64>"}]
            written to ComfyUI's input folder under these names before the graph is queued
  uploads:  [{"url": "<one-file upload link, PUT>", "file_url": "https://..."}] (optional) places to put results
            too big to return inline: each output over WAN_CORE_INLINE_MB takes the next one
Output (action=run):
  {"outputs": [{"node": "58", "filename": "x.mp4", "kind": "video", "mime": "video/mp4",
                "data": "<base64>" | "url": "<file_url it was put at>"}],
   "gpu": "NVIDIA RTX PRO 6000 ...", "seconds": 81.2}
Errors come back as {"error": "..."} so RunPod marks the job FAILED with that message.
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import requests
import runpod

COMFY = os.environ.get("COMFY_HOST", "127.0.0.1:8188")
COMFY_DIR = Path(os.environ.get("COMFY_DIR", "/comfyui"))
INPUT_DIR = COMFY_DIR / "input"
OUTPUT_DIR = COMFY_DIR / "output"
JOB_TIMEOUT_S = int(os.environ.get("WAN_CORE_JOB_TIMEOUT_S", "3300"))  # stay under the endpoint's execution timeout
# RunPod keeps a job's result for 30 minutes; very large results are refused, so keep the base64 payload modest.
MAX_OUTPUT_MB = float(os.environ.get("WAN_CORE_MAX_OUTPUT_MB", "20"))
# Outputs bigger than this go to an upload slot when the job brought one (smaller ones stay inline: one less hop)
INLINE_MB = float(os.environ.get("WAN_CORE_INLINE_MB", "5"))
MAX_INPUT_MB = float(os.environ.get("WAN_CORE_MAX_INPUT_MB", "500"))
OUTPUT_KEYS = ("images", "gifs", "videos", "video", "audio")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,150}$")

_gpu_name: str | None = None


def log(msg: str) -> None:
    print(f"wan-core | {msg}", flush=True)


def gpu_name() -> str:
    global _gpu_name
    if _gpu_name is None:
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=10)
            _gpu_name = out.stdout.strip().splitlines()[0] if out.stdout.strip() else "unknown"
        except (OSError, subprocess.SubprocessError):
            _gpu_name = "unknown"
    return _gpu_name


# ---------------------------------------------------------------- ComfyUI


def comfy(method: str, path: str, **kwargs: Any) -> requests.Response:
    return requests.request(method, f"http://{COMFY}{path}", timeout=kwargs.pop("timeout", 30), **kwargs)


def comfy_alive() -> bool:
    """start.sh writes ComfyUI's pid here; a dead process means waiting longer won't help."""
    try:
        os.kill(int(Path("/tmp/comfyui.pid").read_text().strip()), 0)
    except FileNotFoundError:
        return True  # started some other way (local test); rely on the HTTP check
    except (OSError, ValueError):
        return False
    return True


def wait_for_comfy(limit_s: float = 300) -> None:
    deadline = time.monotonic() + limit_s
    while True:
        try:
            if comfy("GET", "/system_stats", timeout=5).status_code == 200:
                return
        except requests.RequestException:
            pass
        if not comfy_alive():
            raise RuntimeError("ComfyUI exited while starting; see the worker log")
        if time.monotonic() > deadline:
            raise RuntimeError(f"ComfyUI didn't start within {limit_s:.0f} s")
        time.sleep(0.5)


def node_errors(r: requests.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return r.text[:1500]
    parts = []
    if (body.get("error") or {}).get("message"):
        parts.append(body["error"]["message"])
    for node_id, info in (body.get("node_errors") or {}).items():
        for e in info.get("errors", []):
            parts.append(f"node {node_id} ({info.get('class_type')}): {e.get('message')} {e.get('details', '')}".strip())
    return "; ".join(parts)[:1500] or "invalid workflow"


def execution_error(entry: dict[str, Any]) -> str | None:
    status = entry.get("status") or {}
    if status.get("status_str") != "error":
        return None
    for kind, data in status.get("messages", []):
        if kind == "execution_error":
            msg = f"{data.get('node_type', 'node')}: {str(data.get('exception_message', '')).strip()}"
            return msg[:1500]
    return "ComfyUI reported an error"


# ---------------------------------------------------------------- inputs and outputs


def write_inputs(items: list[dict[str, Any]]) -> list[Path]:
    written: list[Path] = []
    for item in items:
        name = str(item.get("name", ""))
        if not SAFE_NAME.match(name):
            raise ValueError(f"bad input name {name!r}")
        dest = INPUT_DIR / name
        if item.get("url"):
            with requests.get(item["url"], stream=True, timeout=(15, 300)) as r:
                r.raise_for_status()
                size = 0
                with open(dest, "wb") as fh:
                    for chunk in r.iter_content(1 << 20):
                        size += len(chunk)
                        if size > MAX_INPUT_MB * 1e6:
                            raise ValueError(f"input {name} is larger than {MAX_INPUT_MB:.0f} MB")
                        fh.write(chunk)
        elif item.get("data"):
            data = str(item["data"])
            dest.write_bytes(base64.b64decode(data.split(",", 1)[1] if data.startswith("data:") else data))
        else:
            raise ValueError(f"input {name} needs a url or data")
        written.append(dest)
    return written


def collect_outputs(entry: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for node_id, produced in (entry.get("outputs") or {}).items():
        for key in OUTPUT_KEYS:
            for item in produced.get(key, []) or []:
                if not isinstance(item, dict) or "filename" not in item or item.get("type", "output") != "output":
                    continue
                path = OUTPUT_DIR / item.get("subfolder", "") / item["filename"]
                if not path.is_file():
                    continue
                mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                kind = mime.split("/")[0] if mime.split("/")[0] in ("video", "image", "audio") else "image"
                found.append({"node": str(node_id), "filename": path.name, "kind": kind, "mime": mime, "path": path})
    return found


def put_file(slot: dict[str, Any], path: Path, mime: str) -> str:
    with open(path, "rb") as fh:
        r = requests.put(str(slot["url"]), data=fh, headers={"Content-Type": mime}, timeout=(15, 600))
    r.raise_for_status()
    return str(slot["file_url"])


def encode_outputs(found: list[dict[str, Any]], slots: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Big outputs go to the job's upload slots (largest first); the rest return inline as base64, within the limit.
    A slot that fails (say its link lapsed while the job queued) leaves that output inline if it still fits."""
    free = [s for s in slots or [] if isinstance(s, dict) and s.get("url") and s.get("file_url")]
    for f in sorted(found, key=lambda f: f["path"].stat().st_size, reverse=True):
        size = f["path"].stat().st_size
        if size <= INLINE_MB * 1e6 or not free:
            continue
        slot = free.pop(0)
        try:
            f["url"] = put_file(slot, f["path"], f["mime"])
            log(f"put {f['filename']} ({size / 1e6:.1f} MB) in its upload slot")
        except (requests.RequestException, OSError) as exc:
            log(f"upload of {f['filename']} failed, returning it inline if it fits: {exc!r}"[:500])
    inline = sum(f["path"].stat().st_size for f in found if "url" not in f)
    if inline > MAX_OUTPUT_MB * 1e6:
        raise ValueError(f"the result is {inline / 1e6:.0f} MB, over the {MAX_OUTPUT_MB:.0f} MB this worker returns "
                         "inline" + (" and its upload failed" if slots else "") + "; render a shorter or smaller clip")
    out = []
    for f in found:
        path: Path = f.pop("path")
        f["bytes"] = path.stat().st_size
        if "url" not in f:
            f["data"] = base64.b64encode(path.read_bytes()).decode()
        out.append(f)
    return out


def clean(paths: list[Path]) -> None:
    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------- actions


def run_workflow(job_input: dict[str, Any], job_id: str) -> dict[str, Any]:
    workflow = job_input.get("workflow")
    if not isinstance(workflow, dict) or not workflow:
        return {"error": "input.workflow must be a ComfyUI API-format graph"}
    started = time.monotonic()
    wait_for_comfy()
    written: list[Path] = []
    try:
        written = write_inputs(list(job_input.get("inputs") or []))
    except (ValueError, requests.RequestException, OSError) as exc:
        clean(written)
        return {"error": f"couldn't fetch an input: {exc}"}

    r = comfy("POST", "/prompt", json={"prompt": workflow, "client_id": f"wan-core-{job_id}"})
    if r.status_code != 200:
        clean(written)
        return {"error": f"ComfyUI rejected the workflow: {node_errors(r)}"}
    prompt_id = r.json()["prompt_id"]
    log(f"job {job_id}: queued prompt {prompt_id}")

    entry: dict[str, Any] | None = None
    while time.monotonic() - started < JOB_TIMEOUT_S:
        h = comfy("GET", f"/history/{prompt_id}").json()
        if prompt_id in h:  # ComfyUI only writes history once a prompt has finished (or failed)
            entry = h[prompt_id]
            break
        time.sleep(1.0)
    if entry is None:
        comfy("POST", "/interrupt", json={"prompt_id": prompt_id})
        clean(written)
        return {"error": f"the workflow ran longer than {JOB_TIMEOUT_S} s and was stopped"}

    clean(written)
    err = execution_error(entry)
    if err:
        if "out of memory" in err.lower() or "outofmemory" in err.lower():
            err += " (the GPU ran out of memory: try a shorter clip or a lower resolution)"
        return {"error": err}
    found = collect_outputs(entry)
    if not found:
        return {"error": "the workflow finished without saving an output"}
    paths = [f["path"] for f in found]
    try:
        outputs = encode_outputs(found, list(job_input.get("uploads") or []))
    except ValueError as exc:
        return {"error": str(exc)}
    finally:
        clean(paths)
    seconds = round(time.monotonic() - started, 1)
    log(f"job {job_id}: {len(outputs)} output(s) in {seconds} s on {gpu_name()}")
    return {"outputs": outputs, "gpu": gpu_name(), "seconds": seconds}


def check() -> dict[str, Any]:
    """What this worker has: GPU, ComfyUI version, and which manifest models are on the volume."""
    from fetch_models import MANIFEST, status

    wait_for_comfy()
    stats = comfy("GET", "/system_stats").json()
    return {"gpu": gpu_name(), "comfyui": (stats.get("system") or {}).get("comfyui_version"),
            "models": status(MANIFEST, Path(os.environ.get("WAN_CORE_MODELS", "/runpod-volume/models"))),
            "disk_free_gb": round(shutil.disk_usage("/").free / 1e9, 1)}


def fetch(job_input: dict[str, Any]) -> dict[str, Any]:
    from fetch_models import MANIFEST, fetch_all

    groups = job_input.get("groups")
    return fetch_all(MANIFEST, Path(os.environ.get("WAN_CORE_MODELS", "/runpod-volume/models")), groups=groups)


async def handler(job: dict[str, Any]) -> dict[str, Any]:
    job_input = job.get("input") or {}
    action = job_input.get("action", "run")
    try:
        if action == "check":
            return await asyncio.to_thread(check)
        if action == "fetch_models":
            return await asyncio.to_thread(fetch, job_input)
        if action == "run":
            return await asyncio.to_thread(run_workflow, job_input, job.get("id") or uuid.uuid4().hex)
        return {"error": f"unknown action {action!r}"}
    except asyncio.CancelledError:
        # RunPod canceled the job: stop whatever ComfyUI is running so the worker is free for the next one.
        try:
            comfy("POST", "/interrupt", timeout=5)
            comfy("POST", "/queue", json={"clear": True}, timeout=5)
        except requests.RequestException:
            pass
        raise
    except Exception as exc:  # every failure becomes a readable job error
        log(f"job failed: {exc!r}")
        return {"error": f"{type(exc).__name__}: {exc}"[:1500]}


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
