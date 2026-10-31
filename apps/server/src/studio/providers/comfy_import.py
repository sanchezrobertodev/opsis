"""Draft a model from your own ComfyUI workflow (scripts/import_comfy_workflow.py, docs/comfy-workflows.md).

Reads an API-format graph and guesses which node inputs are the prompt, negative prompt, seed, steps, size, length
and media, then drafts the `.map.yaml` and a model YAML with one `comfy_local` route. The guesses go by input names
and links, never by model, so they are a starting point to review: the notes list what was guessed and what's left.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import yaml

TEXT_INPUTS = ("text", "prompt")
SEED_INPUTS = ("seed", "noise_seed")
LENGTH_INPUTS = ("length", "frames_number", "num_frames", "video_length")
FPS_INPUTS = ("fps", "frame_rate")
LORA_LOADERS = {"LoraLoaderModelOnly": ["model"], "LoraLoader": ["model", "clip"]}  # what each output passes on
# loader class -> (the input that names the file, what it loads)
LOADERS = {
    "LoadImage": ("image", "image"),
    "LoadVideo": ("file", "video"),
    "VHS_LoadVideo": ("video", "video"),
    "LoadAudio": ("audio", "audio"),
    "VHS_LoadAudioUpload": ("audio", "audio"),
}
SAVERS = {
    "SaveImage": "image",
    "SaveVideo": "video",
    "VHS_VideoCombine": "video",
    "SaveWEBM": "video",
    "SaveAnimatedWEBP": "video",
    "SaveAudio": "audio",
    "SaveAudioMP3": "audio",
}
# media roles a consumer's input name gives away, else the order to hand them out in
NAMED_ROLES = {"start_image", "end_image", "style_image", "character_image", "replacement_image", "control_video",
               "driving_video", "input_video", "input_audio"}
ORDER = {
    ("video", "image"): ["start_image", "end_image", "image_references", "style_image"],
    ("image", "image"): ["image_references", "style_image"],
    ("audio", "image"): ["image_references"],
    ("video", "video"): ["input_video", "control_video", "driving_video"],
    ("image", "video"): ["input_video"],
    ("audio", "video"): ["input_video"],
}
LABELS = {"start_image": "Start frame", "end_image": "Last frame", "image_references": "Image",
          "style_image": "Style image", "input_video": "Clip", "control_video": "Control clip",
          "driving_video": "Motion clip", "input_audio": "Audio", "mask": "Mask"}


class NotAWorkflow(ValueError):
    """The file isn't an API-format ComfyUI graph."""


@dataclass
class Draft:
    graph: dict[str, Any]
    inputs: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    outputs: list[dict[str, str]] = field(default_factory=list)
    slots: list[dict[str, Any]] = field(default_factory=list)
    defaults: dict[str, Any] = field(default_factory=dict)
    optional: dict[str, dict[str, Any]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    output_type: str = "video"

    def add(self, key: str, node: str, input_: str, mode: str = "set") -> None:
        target = {"node": node, "input": input_} | ({"mode": mode} if mode != "set" else {})
        if target not in self.inputs.setdefault(key, []):
            self.inputs[key].append(target)


def read_graph(text: str) -> dict[str, Any]:
    """The graph from ComfyUI's Export (API) file; the regular workflow file is refused with how to get the other."""
    data = json.loads(text)
    if isinstance(data, dict) and isinstance(data.get("prompt"), dict):
        data = data["prompt"]
    if isinstance(data, dict) and "nodes" in data and "links" in data:
        raise NotAWorkflow("this is ComfyUI's editor format; save it with Workflow > Export (API) instead")
    if not isinstance(data, dict) or not data or not all(
        isinstance(n, dict) and "class_type" in n and isinstance(n.get("inputs"), dict) for n in data.values()
    ):
        raise NotAWorkflow("not an API-format ComfyUI graph (expected node ids mapping to class_type and inputs)")
    return data


def _num(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _sort(ids: Any) -> list[str]:
    return sorted(ids, key=lambda k: (0, int(k)) if str(k).isdigit() else (1, str(k)))


def _consumers(graph: dict[str, Any], node_id: str) -> list[tuple[str, str]]:
    """(node, input) pairs that take an output of `node_id`."""
    out = []
    for nid in _sort(graph):
        for name, v in graph[nid]["inputs"].items():
            if isinstance(v, list) and len(v) == 2 and str(v[0]) == node_id:
                out.append((nid, name))
    return out


def analyze(graph: dict[str, Any]) -> Draft:
    d = Draft(graph=graph)
    nodes = {nid: graph[nid] for nid in _sort(graph)}

    # outputs, and what the model makes
    for nid, n in nodes.items():
        kind = SAVERS.get(n["class_type"])
        if kind:
            d.outputs.append({"node": nid, "kind": kind})
            if "filename_prefix" in n["inputs"]:
                d.add("filename_prefix", nid, "filename_prefix")
    if not d.outputs:
        raise NotAWorkflow("no save node (SaveImage, SaveVideo, VHS_VideoCombine, SaveAudio...): add one to the graph")
    kinds = {o["kind"] for o in d.outputs}
    d.output_type = "video" if "video" in kinds else next(iter(kinds))

    # prompts: text fed into a positive or negative input
    for nid, n in nodes.items():
        for name in TEXT_INPUTS:
            if not isinstance(n["inputs"].get(name), str):
                continue
            uses = {inp for _, inp in _consumers(graph, nid)}
            if "positive" in uses:
                d.add("prompt", nid, name)
            elif "negative" in uses:
                # keep a negative the graph already has, and add the user's to it
                d.add("negative_prompt", nid, name, "append" if n["inputs"][name].strip() else "set")
    if "prompt" not in d.inputs:
        d.notes.append("no text input feeds a positive input, so the mode takes no prompt")

    # seed and steps
    for nid, n in nodes.items():
        ins = n["inputs"]
        for name in SEED_INPUTS:
            if isinstance(ins.get(name), int):
                d.add("seed", nid, name)
        if isinstance(ins.get("steps"), int):
            if "start_at_step" in ins or "end_at_step" in ins:
                d.notes.append(f"node {nid} ({n['class_type']}) samples part of the steps, so steps stay fixed")
            else:
                d.add("steps", nid, "steps")
                d.defaults.setdefault("steps", ins["steps"])

    # size, length and frame rate: the latent's values, wherever else the graph repeats them (resizes, schedulers)
    latent = next((n["inputs"] for n in nodes.values() if "Latent" in n["class_type"]
                   and _num(n["inputs"].get("width")) and _num(n["inputs"].get("height"))), None)
    if latent is None:
        latent = next((n["inputs"] for n in nodes.values()
                       if _num(n["inputs"].get("width")) and _num(n["inputs"].get("height"))), None)
    if latent:
        w, h = latent["width"], latent["height"]
        d.defaults |= {"width": int(w), "height": int(h)}
        length = next((latent[k] for k in LENGTH_INPUTS if _num(latent.get(k))), None)
        if length is not None:
            d.defaults["length"] = int(length)
        for nid, n in nodes.items():
            ins = n["inputs"]
            if ins.get("width") == w and ins.get("height") == h:
                d.add("width", nid, "width")
                d.add("height", nid, "height")
            for name in LENGTH_INPUTS:
                if length is not None and ins.get(name) == length:
                    d.add("length", nid, name)
            if "Latent" in n["class_type"] and isinstance(ins.get("batch_size"), int) and d.output_type == "image":
                d.add("num_images", nid, "batch_size")
    fps = next((n["inputs"][k] for n in nodes.values() if n["class_type"] in ("CreateVideo", "VHS_VideoCombine")
                for k in FPS_INPUTS if _num(n["inputs"].get(k))), None)
    if fps is not None:
        d.defaults["fps"] = fps
        for nid, n in nodes.items():
            for name in FPS_INPUTS:
                if _num(n["inputs"].get(name)) and float(n["inputs"][name]) == float(fps):
                    d.add("fps", nid, name)

    # media: one slot per loader
    taken: set[str] = set()
    for nid, n in nodes.items():
        loader = LOADERS.get(n["class_type"])
        if not loader:
            continue
        field_, kind = loader
        value = str(n["inputs"].get(field_, ""))
        hint = next((inp for _, inp in _consumers(graph, nid)), "")
        if "mask" in value.lower() or "mask" in hint:
            role, kinds = "mask", ["mask" if kind == "image" else "mask_video"]
        elif kind == "audio":
            role, kinds = "input_audio", ["audio"]
        else:
            named = "image_references" if hint == "reference_image" else hint
            choices = [named] if named in NAMED_ROLES | {"image_references"} else []
            role = next((r for r in choices + ORDER[(d.output_type, kind)] if r not in taken), "")
            kinds = [kind]
            if not choices:
                d.notes.append(f"node {nid} ({n['class_type']}, {value!r}) guessed as {role or 'nothing'} by order")
        if not role or role in taken:
            d.notes.append(f"node {nid} ({n['class_type']}) has no free media role; map it by hand")
            continue
        taken.add(role)
        d.add(role, nid, field_)
        d.slots.append({"role": role, "kinds": kinds, "label": LABELS.get(role, role), "required": True})

    # one LoRA loader: pick its file from your ComfyUI's list, or none (the loader then drops out of the chain)
    loras = [nid for nid, n in nodes.items() if n["class_type"] in LORA_LOADERS]
    passes = LORA_LOADERS[nodes[loras[0]]["class_type"]] if loras else []
    if len(loras) == 1 and all(isinstance(nodes[loras[0]]["inputs"].get(i), list) for i in passes):
        nid, ins = loras[0], nodes[loras[0]]["inputs"]
        d.add("lora", nid, "lora_name")
        for name in ("strength_model", "strength_clip"):
            if name in ins:
                d.add("lora_strength", nid, name)
        d.optional["lora"] = {"node": nid, "bypass": passes}
        d.defaults |= {"lora": ins.get("lora_name") or None, "lora_strength": ins.get("strength_model", 1.0),
                       "lora_node": nodes[nid]["class_type"]}
    elif loras:
        d.notes.append(f"LoRA loaders {', '.join(loras)} stay as saved; with one, it would be a choice in the form")
    return d


def _mode(d: Draft) -> tuple[str, str, list[str]]:
    roles = {s["role"] for s in d.slots}
    if d.output_type == "video":
        if roles & {"input_video", "control_video", "driving_video"}:
            return "v2v", "Video to video", ["restyle"]
        if {"start_image", "end_image"} <= roles:
            return "flf2v", "First and last frame", ["flf2v"]
        if "start_image" in roles:
            return "i2v", "Image to video", ["i2v"]
        return "t2v", "Text to video", ["t2v"]
    if d.output_type == "image":
        return ("i2i", "Edit an image", ["i2i"]) if roles else ("t2i", "Text to image", ["t2i"])
    return "generate", "Generate", []


def map_yaml(d: Draft, name: str) -> str:
    head = [f"# Drafted from a ComfyUI workflow by scripts/import_comfy_workflow.py on {date.today().isoformat()}.",
            "# Review it: each key below fills these node inputs (docs/comfy-workflows.md)."]
    head += [f"# - {note}" for note in d.notes]
    body = {"description": f"{name} (your own ComfyUI workflow)", "inputs": d.inputs,
            **({"optional": d.optional} if d.optional else {}), "outputs": d.outputs}
    return "\n".join(head) + "\n" + yaml.safe_dump(body, sort_keys=False, default_flow_style=None, allow_unicode=True,
                                                   width=118)


def model_yaml(d: Draft, model_id: str, name: str, template: str, min_vram_gb: float | None = None) -> str:
    mode_id, label, ops = _mode(d)
    params: list[dict[str, Any]] = []
    route_params: dict[str, Any] = {}
    spec: dict[str, Any] = {}
    fps, length = d.defaults.get("fps"), d.defaults.get("length")
    if d.output_type == "video" and "length" in d.inputs and fps:
        seconds = max(1, round((length - 1) / fps)) if length else 3
        top = max(seconds * 2, 5)
        spec["duration"] = {"min": 1, "max": top, "default": seconds}
        params.append({"name": "duration", "label": "Duration (s)", "type": "int", "min": 1, "max": top,
                       "default": seconds, "ui": "slider"})
        route_params["duration"] = {"to": "length", "transform": "seconds_to_frames", "args": {"fps": fps, "plus": 1}}
    elif "length" in d.inputs and length:
        params.append({"name": "length", "label": "Frames", "type": "int", "min": 1, "max": max(length * 2, 2),
                       "default": length, "group": "advanced"})
    for side in ("width", "height"):
        if side in d.inputs:
            params.append({"name": side, "type": "int", "min": 256, "max": 2048, "step": 16,
                           "default": min(max(d.defaults[side], 256), 2048), "group": "advanced"})
    if "num_images" in d.inputs:
        params.append({"name": "num_images", "label": "Images", "type": "int", "min": 1, "max": 4, "default": 1})
    if "steps" in d.inputs:
        steps = d.defaults["steps"]
        params.append({"name": "steps", "type": "int", "min": 1, "max": max(50, steps * 2), "default": steps,
                       "group": "advanced"})
    if "seed" in d.inputs:
        params.append({"name": "seed", "type": "seed", "group": "advanced"})
    if "lora" in d.inputs:
        params.append({"name": "lora", "label": "LoRA", "type": "enum",
                       "comfy_choices": f"{d.defaults['lora_node']}.lora_name", "default": d.defaults["lora"],
                       "group": "advanced", "help": "A LoRA file from your ComfyUI's loras folder."})
        params.append({"name": "lora_strength", "label": "LoRA strength", "type": "float", "min": 0, "max": 2,
                       "step": 0.05, "default": float(d.defaults["lora_strength"]), "group": "advanced"})

    has_prompt = "prompt" in d.inputs
    mode: dict[str, Any] = {"id": mode_id, "label": label, "ops": ops}
    if not has_prompt:
        mode["prompt"] = "none"
    if d.slots:
        mode["medias"] = d.slots
    route: dict[str, Any] = {"id": f"{model_id}@comfy_local", "provider": "comfy_local",
                             "modes": {mode_id: {"endpoint": template}}}
    if min_vram_gb:
        route["min_vram_gb"] = min_vram_gb
    if route_params:
        route["map"] = {"params": route_params}
    route |= {
        "pricing": {"basis": "per_request", "rates": [{"usd": 0}], "verified": date.today(), "source": "your own GPU"},
        "eta": "300" if d.output_type == "video" else "60",
        "timeout_s": 3600,
        "output": {"path": "outputs", "kind": d.output_type} | ({"multiple": True} if "num_images" in d.inputs else {}),
    }
    fixtures = {"image": "fixture:images/keyframe.png", "video": "fixture:videos/clip_3s.mp4",
                "audio": "fixture:audio/line.mp3"}
    example: dict[str, Any] = {"name": "smoke", "mode": mode_id, "smoke": True}
    if has_prompt:
        example["prompt"] = "A small red sailboat drifts on a calm alpine lake at sunrise, gentle ripples."
    media = {s["role"]: fixtures[s["kinds"][0]] for s in d.slots if s["kinds"][0] in fixtures}
    if media:
        example["media"] = media
    example["expect"] = {"cost_usd_range": [0, 0]}

    model: dict[str, Any] = {
        "id": model_id, "name": name, "family": "custom", "vendor": "custom",
        "description": "Your own ComfyUI workflow, run free on your GPU.",
        "output_type": d.output_type, "status": "preview", "license_tier": "open_restricted",
        "tags": ["local", "free", "custom"],
        "prompt": {"max_chars": 2000, "dialect": "default", "supports_negative": "negative_prompt" in d.inputs},
        **spec, "parameters": params, "modes": [mode], "routes": [route], "examples": [example],
    }
    head = ["# yaml-language-server: $schema=../../schema/model.schema.json",
            f"# Drafted from workflows/comfy/{template}.json by scripts/import_comfy_workflow.py. Check license_tier",
            "# against the weights' license, time a run to set eta (seconds), and fill in the description."]
    return "\n".join(head) + "\n" + yaml.safe_dump(model, sort_keys=False, default_flow_style=None,
                                                   allow_unicode=True, width=118)


def template_name(model_id: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", model_id.lower()).strip("_")
