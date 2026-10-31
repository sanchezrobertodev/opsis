"""ComfyUI workflow templates: an API-format graph plus a `.map.yaml` saying which node inputs to fill.

Shared by the local ComfyUI adapter and the RunPod worker-comfyui adapter, so the same validated graph runs in
both places. Also what both need around a template: a fresh seed per take, clips re-timed for Wan, and which model
files a graph needs (checked against a ComfyUI's /object_info, named from the wan-core download manifest).
"""

from __future__ import annotations

import copy
import json
import secrets
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

from studio.config import REPO_ROOT

# Every model file the workflows use, with its folder, exact size, download group and link. Not in the server's
# container image (infra/runpod stays out of it); messages then name the files without sizes or groups.
MANIFEST = REPO_ROOT / "infra" / "runpod" / "wan-core" / "models.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Target(_Strict):
    node: str
    input: str
    mode: Literal["set", "append"] = "set"


class Unlink(_Strict):
    node: str
    input: str


class Optional_(_Strict):
    node: str
    unlink: list[Unlink] = []
    # a node in the middle of a chain (a LoRA loader): links out of it go to what fed it, output i to input bypass[i]
    bypass: list[str] = []


class OutputTarget(_Strict):
    node: str
    kind: Literal["video", "image", "audio"]


class TemplateMap(_Strict):
    description: str = ""
    inputs: dict[str, list[Target]]
    optional: dict[str, Optional_] = {}
    outputs: list[OutputTarget]


@dataclass
class WorkflowTemplate:
    name: str
    graph: dict[str, Any]
    map: TemplateMap
    warnings: list[str] = field(default_factory=list)

    def render(self, values: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Return a ready-to-queue graph with `values` injected, plus warnings for ignored keys."""
        graph = copy.deepcopy(self.graph)
        warnings: list[str] = []
        present = {k: v for k, v in values.items() if v is not None and v != ""}

        dropped: set[str] = set()
        for key, opt in self.map.optional.items():
            if key in present:
                continue
            node = graph.pop(opt.node, None)
            dropped.add(opt.node)
            for u in opt.unlink:
                graph.get(u.node, {}).get("inputs", {}).pop(u.input, None)
            if node and opt.bypass:
                for other in graph.values():
                    for name, link in other.get("inputs", {}).items():
                        if isinstance(link, list) and len(link) == 2 and link[0] == opt.node and link[1] < len(opt.bypass):
                            other["inputs"][name] = node["inputs"][opt.bypass[link[1]]]

        for key, value in present.items():
            targets = self.map.inputs.get(key)
            if not targets:
                warnings.append(f"workflow {self.name} has no input for {key!r}")
                continue
            if all(t.node in dropped for t in targets):
                continue  # a setting of a part that's off (the LoRA's strength with no LoRA picked)
            if isinstance(value, list):
                value = value[0] if len(value) == 1 else value
            for t in targets:
                node = graph.get(t.node)
                if node is None:
                    raise KeyError(f"workflow {self.name}: node {t.node} missing for {key!r}")
                current = node["inputs"].get(t.input)
                if t.mode == "append" and isinstance(current, str) and current:
                    node["inputs"][t.input] = f"{current}, {value}"
                else:
                    node["inputs"][t.input] = value
        return graph, warnings


def _load(directory: Path, name: str, mtime_key: tuple[float, float]) -> WorkflowTemplate:
    graph = json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))
    if "prompt" in graph and isinstance(graph["prompt"], dict):
        graph = graph["prompt"]  # accept files saved straight from ComfyUI's API export
    spec = TemplateMap.model_validate(yaml.safe_load((directory / f"{name}.map.yaml").read_text(encoding="utf-8")))
    for key, targets in spec.inputs.items():
        for t in targets:
            if t.node not in graph:
                raise ValueError(f"{name}.map.yaml: input {key!r} points at missing node {t.node}")
            if t.input not in graph[t.node].get("inputs", {}):
                raise ValueError(f"{name}.map.yaml: node {t.node} has no input {t.input!r}")
    for key, opt in spec.optional.items():
        for name_ in opt.bypass:
            if not isinstance(graph.get(opt.node, {}).get("inputs", {}).get(name_), list):
                raise ValueError(f"{name}.map.yaml: optional {key!r} bypasses {name_!r}, not a link into node {opt.node}")
    for o in spec.outputs:
        if o.node not in graph:
            raise ValueError(f"{name}.map.yaml: output node {o.node} missing")
    return WorkflowTemplate(name=name, graph=graph, map=spec)


_cached = lru_cache(maxsize=64)(_load)


def load_template(directory: Path, name: str) -> WorkflowTemplate:
    """Load (and cache until either file changes) a template by name."""
    jp, mp = directory / f"{name}.json", directory / f"{name}.map.yaml"
    if not jp.exists() or not mp.exists():
        raise FileNotFoundError(f"workflow template {name!r} not found in {directory}")
    return _cached(directory, name, (jp.stat().st_mtime, mp.stat().st_mtime))


def fresh_seed(template: WorkflowTemplate, values: dict[str, Any]) -> None:
    """A new take each time unless the user pinned a seed: templates keep the fixed seed they were saved with."""
    if "seed" in template.map.inputs and values.get("seed") is None:
        values["seed"] = secrets.randbelow(2**32)


async def retime_clip(src: Path, fps: float, dest: Path) -> None:
    """`src` at `fps`, same duration (frames dropped or repeated) and silent, as an H.264 mp4 at `dest`."""
    from studio.media.ffmpeg import ffmpeg

    await ffmpeg("-y", "-i", str(src), "-vf", f"fps={fps:g}", "-an", "-c:v", "libx264", "-crf", "12",
                 "-pix_fmt", "yuv420p", str(dest))


def _manifest(path: Path, mtime: float) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for f in yaml.safe_load(path.read_text(encoding="utf-8"))["files"]:
        out.setdefault(f["name"], {**f, "groups": []})["groups"].append(f.get("group", "core"))
    return out


_manifest_cached = lru_cache(maxsize=2)(_manifest)


def model_files() -> dict[str, dict[str, Any]]:
    """File name -> its manifest entry (folder, bytes, url, `groups` listing it); empty when the manifest isn't here."""
    return _manifest_cached(MANIFEST, MANIFEST.stat().st_mtime) if MANIFEST.exists() else {}


def download_groups(names: list[str], files: dict[str, dict[str, Any]]) -> list[str]:
    """Manifest groups that fetch these files: a file several groups list (Wan's text encoder) adds a group only when
    none of the others needed already has it, and then the smallest download."""
    size: dict[str, int] = {}
    for f in files.values():
        for g in f["groups"]:
            size[g] = size.get(g, 0) + f.get("bytes", 0)
    known = [files[n]["groups"] for n in names if n in files]
    groups = {g[0] for g in known if len(g) == 1}
    for g in known:
        if not groups.intersection(g):
            groups.add(min(g, key=lambda name: size[name]))
    return sorted(groups)


def combo_options(spec: Any) -> list[Any] | None:
    """The options of a choice input in ComfyUI's /object_info, in either of its formats."""
    if not isinstance(spec, list) or not spec:
        return None
    if isinstance(spec[0], list):
        return spec[0]
    if spec[0] == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
        return spec[1].get("options")
    return None


def unavailable_choices(template: WorkflowTemplate, object_info: dict[str, Any],
                        files: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(node classes this ComfyUI lacks, fixed choices it doesn't offer): the second is mostly model files it hasn't
    got. Inputs the map fills are skipped, unless their saved value is a model file a parameter can switch on (LTX's
    audio VAE)."""
    filled = {(t.node, t.input) for targets in template.map.inputs.values() for t in targets}
    classes: set[str] = set()
    values: list[str] = []
    for node_id, node in template.graph.items():
        info = object_info.get(node.get("class_type", ""))
        if info is None:
            classes.add(node.get("class_type", ""))
            continue
        specs = {**info.get("input", {}).get("required", {}), **info.get("input", {}).get("optional", {})}
        for name, value in node.get("inputs", {}).items():
            if not isinstance(value, str) or ((node_id, name) in filled and value not in files):
                continue
            options = combo_options(specs.get(name))
            if options is not None and value not in options and value not in values:
                values.append(value)
    return sorted(classes), values
