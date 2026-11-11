"""What each free local model needs on your own ComfyUI (GPU memory, node packs, model files), and where the ComfyUI on
this computer keeps them: what scripts/setup_local.py checks and installs."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml

from studio.providers.comfy_workflows import (
    MANIFEST,
    Pack,
    download_groups,
    load_template,
    model_files,
    workflow_dirs,
)
from studio.registry.loader import Registry

LOCAL_PROVIDER = "comfy_local"
# what a node pack's requirements may not move: ComfyUI's own torch build, and the versions its nodes were checked
# with (transformers 5.17 breaks the WanVideoWrapper's wav2vec node); pip fails instead of changing them
KEEP = ("torch", "torchvision", "torchaudio", "transformers", "numpy")


@dataclass(frozen=True)
class LocalModel:
    id: str
    name: str
    min_vram_gb: float | None
    packs: tuple[Pack, ...]
    files: tuple[dict[str, Any], ...]  # manifest entries: folder, name, bytes, url, group


def local_models(reg: Registry) -> list[LocalModel]:
    """Every model with a route on your own ComfyUI: the model files its graphs name and the node packs they need. A
    file two loaders read from different folders is taken from the download group the model's other files are in."""
    files = model_files()
    entries = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["files"] if MANIFEST.exists() else []
    out = []
    for spec in reg.models.values():
        routes = [r for r in spec.routes if r.provider == LOCAL_PROVIDER and r.enabled]
        if not routes:
            continue
        names: set[str] = set()
        packs: dict[str, Pack] = {}
        for route in routes:
            for mode in route.modes.values():
                template = load_template(workflow_dirs(reg.user_files), mode.endpoint)
                if template.map.pack:
                    packs[template.map.pack.url] = template.map.pack
                names |= {v for node in template.graph.values() for v in node.get("inputs", {}).values()
                          if isinstance(v, str) and v in files}
        groups = set(download_groups(sorted(names), files))
        picked = []
        for name in sorted(names):
            options = [e for e in entries if e["name"] == name]
            picked.append(next((e for e in options if e.get("group", "core") in groups), options[0]))
        out.append(LocalModel(spec.id, spec.name, max((r.min_vram_gb for r in routes if r.min_vram_gb), default=None),
                              tuple(packs.values()), tuple(picked)))
    return out


@dataclass
class Comfy:
    """Where the ComfyUI on this computer keeps node packs and model files, and what it runs on."""

    custom_nodes: Path
    models_dir: Path
    folders: dict[str, list[Path]] = field(default_factory=dict)  # folder name -> its paths; new files go in the first
    vram_gb: float | None = None
    gpu: str | None = None
    version: str | None = None
    python_version: str | None = None
    embedded_python: bool = False

    def paths(self, folder: str) -> list[Path]:
        # a pack registers its folder when it loads (the wrapper's wav2vec2/), so before then it's under models/
        return self.folders.get(folder) or [self.models_dir / folder]

    def has_file(self, f: dict[str, Any]) -> bool:
        return any((p / f["name"]).is_file() and (not f.get("bytes") or (p / f["name"]).stat().st_size == f["bytes"])
                   for p in self.paths(f["folder"]))

    def has_pack(self, pack: Pack) -> bool:
        return self.custom_nodes.is_dir() and any(
            d.is_dir() and d.name.lower() == pack.name.lower() for d in self.custom_nodes.iterdir())

    def fits(self, m: LocalModel) -> bool:
        """Enough GPU memory, by the same rule routing uses (cards report a little under their size)."""
        return not m.min_vram_gb or self.vram_gb is None or self.vram_gb >= m.min_vram_gb - 0.5

    def python(self) -> Path | None:
        """ComfyUI's own Python: the portable build's python_embeded/, else a venv in ComfyUI's folder."""
        root = self.custom_nodes.parent
        exe = "python.exe" if sys.platform == "win32" else "python"
        found = [root.parent / "python_embeded" / exe] if self.embedded_python else []
        found += [root / v / ("Scripts" if sys.platform == "win32" else "bin") / exe for v in ("venv", ".venv")]
        return next((p for p in found if p.is_file()), None)


def comfy_from(stats: dict[str, Any], folder_paths: dict[str, Any]) -> Comfy:
    """Comfy from ComfyUI's /system_stats and /internal/folder_paths."""
    folders = {k: [Path(p) for p in v] for k, v in folder_paths.items() if isinstance(v, list) and v}
    custom_nodes = folders.pop("custom_nodes")[0]
    system = stats.get("system") or {}
    argv = system.get("argv") or []
    # models/ beside custom_nodes/ (both under ComfyUI's base directory), unless it was started with another
    models_dir = custom_nodes.parent / "models"
    if "--models-directory" in argv[:-1]:
        models_dir = Path(argv[argv.index("--models-directory") + 1])
    device = max(stats.get("devices") or [{}], key=lambda d: d.get("vram_total") or 0)
    return Comfy(custom_nodes=custom_nodes, models_dir=models_dir, folders=folders,
                 vram_gb=device["vram_total"] / 2**30 if device.get("vram_total") else None,
                 gpu=re.sub(r"^cuda:\d+\s*", "", device.get("name") or "").split(" : ")[0] or None,
                 version=system.get("comfyui_version"), python_version=system.get("python_version"),
                 embedded_python=bool(system.get("embedded_python")))


def comfy_at(url: str) -> Comfy | None:
    """The running ComfyUI at `url`, or None when it doesn't answer."""
    try:
        with httpx.Client(base_url=url, timeout=10) as client:
            stats = client.get("/system_stats").raise_for_status().json()
            paths = client.get("/internal/folder_paths").raise_for_status().json()
    except (httpx.HTTPError, ValueError):
        return None
    return comfy_from(stats, paths)


def python_version(python: Path) -> str:
    return subprocess.run([str(python), "-c", "import sys; print(sys.version)"], capture_output=True, text=True,
                          check=True).stdout.strip()


def install_pack(pack: Pack, comfy: Comfy, python: Path) -> None:
    """Clone the pack at its checked commit and install its requirements into ComfyUI's Python without moving the
    packages in KEEP; only then is it moved into custom_nodes/, so a failed install is tried again next time."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # git's read-only objects on Windows
        clone = Path(tmp) / pack.name
        subprocess.run(["git", "clone", "--quiet", pack.url, str(clone)], check=True)
        if pack.ref:
            subprocess.run(["git", "-C", str(clone), "-c", "advice.detachedHead=false", "checkout", "--quiet",
                            pack.ref], check=True)
        requirements = clone / "requirements.txt"
        if requirements.is_file():
            have = json.loads(subprocess.run([str(python), "-c", (
                "import importlib.metadata as m, json, sys\n"
                "out = {}\n"
                "for n in sys.argv[1:]:\n"
                "    try: out[n] = m.version(n)\n"
                "    except m.PackageNotFoundError: pass\n"
                "print(json.dumps(out))"), *KEEP], capture_output=True, text=True, check=True).stdout)
            constraints = Path(tmp) / "keep.txt"
            constraints.write_text("".join(f"{n}=={v}\n" for n, v in have.items()), encoding="utf-8")
            subprocess.run([str(python), "-m", "pip", "install", "-r", str(requirements), "-c", str(constraints)],
                           check=True)
        shutil.move(clone, comfy.custom_nodes / pack.name)


def describe(m: LocalModel, comfy: Comfy) -> tuple[str, str]:
    """(ready | needs | too big, what it still needs)."""
    if not comfy.fits(m):
        return "too big", f"needs a GPU with {m.min_vram_gb:g} GB"
    work = plan([m], comfy)
    needs = [f"the {p.name} node pack" for p in work.packs]
    if work.jobs:
        needs.append(f"{len(work.jobs)} model files, {work.bytes / 1e9:.1f} GB")
    return ("ready", "") if not needs else ("needs", ", ".join(needs))


@dataclass
class Plan:
    """What setting up some models still takes on this ComfyUI: node packs to install, and files to download, each
    into the first folder ComfyUI reads it from."""

    packs: list[Pack]
    jobs: list[tuple[dict[str, Any], Path]]

    @property
    def bytes(self) -> int:
        return sum(f.get("bytes", 0) for f, _ in self.jobs)

    def got_bytes(self) -> int:
        """How much of the downloads is on disk, finished or partial (a stopped download resumes from its .part)."""
        total = 0
        for f, d in self.jobs:
            for p in (d / f["name"], d / f"{f['name']}.part"):
                if p.is_file():
                    total += p.stat().st_size
                    break
        return total

    def free_bytes(self) -> int | None:
        homes = {_existing(d) for _, d in self.jobs}
        return min(shutil.disk_usage(h).free for h in homes) if homes else None

    def room(self) -> bool:
        """Enough free disk for what's left of the downloads, with a GB to spare."""
        free = self.free_bytes()
        return free is None or free >= self.bytes - self.got_bytes() + 10**9


def plan(models: list[LocalModel], comfy: Comfy) -> Plan:
    packs = list({p.url: p for m in models for p in m.packs if not comfy.has_pack(p)}.values())
    files = list({(f["folder"], f["name"]): f for m in models for f in m.files if not comfy.has_file(f)}.values())
    return Plan(packs, [(f, comfy.paths(f["folder"])[0]) for f in files])


def python_problem(comfy: Comfy, python: Path | None) -> str | None:
    """Why node packs can't be installed with `python`, if they can't."""
    if python is None:
        return "can't find ComfyUI's Python to install node packs into"
    try:
        version = python_version(python)
    except (OSError, subprocess.CalledProcessError):
        return f"{python} doesn't run"
    if comfy.python_version and version != comfy.python_version:
        return f"{python} isn't the Python ComfyUI runs on ({comfy.python_version.split()[0]})"
    return None


def carry_out(work: Plan, comfy: Comfy, python: Path | None, say: Callable[[str], None] = print) -> list[str]:
    """Install the packs and download the files; what failed, if anything."""
    failed = []
    for p in work.packs:
        say(f"Installing the {p.name} node pack")
        try:
            install_pack(p, comfy, python)  # type: ignore[arg-type]  # python_problem() said there is one
        except subprocess.CalledProcessError as exc:
            failed.append(f"{p.name}: {' '.join(map(str, exc.cmd[:4]))}... exited with {exc.returncode}")
        except OSError as exc:  # no git
            failed.append(f"{p.name}: {exc}")
    if work.jobs:
        say(f"Downloading {len(work.jobs)} model files")
        _, bad = fetch_models().fetch([({**f, "folder": d.name}, d.parent) for f, d in work.jobs])
        failed += bad
    return failed


def fetch_models() -> ModuleType:
    """infra/runpod/wan-core/fetch_models.py: downloads the manifest's files (resumable, checked by size)."""
    spec = importlib.util.spec_from_file_location("fetch_models", MANIFEST.with_name("fetch_models.py"))
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def on_this_computer(url: str | None) -> Comfy | str:
    """The ComfyUI at `url` when its folders are on this computer, so it can be set up from here; else why not."""
    if not url:
        return "COMFYUI_URL isn't set"
    if urlparse(url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        return "ComfyUI runs on another computer"
    comfy = comfy_at(url)
    if comfy is None:
        return "ComfyUI isn't running"
    if not comfy.custom_nodes.is_dir() or not MANIFEST.with_name("fetch_models.py").exists():
        return "ComfyUI's folders aren't on the computer running Opsis"
    return comfy


def _existing(path: Path) -> Path:
    while not path.exists() and path != path.parent:
        path = path.parent
    return path
