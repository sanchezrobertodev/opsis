"""Set up the free local models on the ComfyUI running on this computer.

    uv run python scripts/setup_local.py                        what's ready, and what each model still needs
    uv run python scripts/setup_local.py --model flux-2-klein-4b --model qwen3-tts     set these up
    uv run python scripts/setup_local.py --fits                 set up every model that fits this GPU

Start ComfyUI first (COMFYUI_URL in .env, or --url): it says where it keeps models and node packs. Before changing
anything this lists what it will do and asks: it clones the node packs a model needs into custom_nodes/ (at the
commit its graph was checked against), installs their Python packages into ComfyUI's own Python (never moving its
torch, transformers or numpy), and downloads the model files (resumable, skipped when already there). Restart ComfyUI
afterwards when a node pack was added. Gated files (LTX-2.5) need HF_TOKEN and their license accepted on Hugging Face.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "server" / "src"))

from studio.config import get_settings  # noqa: E402
from studio.providers.local_setup import (  # noqa: E402
    carry_out,
    comfy_at,
    describe,
    local_models,
    plan,
    python_problem,
)
from studio.registry.loader import load_registry  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", action="append", default=[], help="a model id to set up (repeatable)")
    ap.add_argument("--fits", action="store_true", help="set up every model that fits this GPU")
    ap.add_argument("--url", help="ComfyUI's address (default: COMFYUI_URL, else http://127.0.0.1:8188)")
    ap.add_argument("--python", type=Path, help="ComfyUI's Python, when it isn't the portable build's or a venv in its folder")
    ap.add_argument("--yes", action="store_true", help="don't ask before installing and downloading")
    args = ap.parse_args()

    url = args.url or get_settings().comfyui_url or "http://127.0.0.1:8188"
    comfy = comfy_at(url)
    if comfy is None:
        print(f"ComfyUI isn't answering at {url}. Start it, then run this again.")
        return 2
    if not comfy.custom_nodes.is_dir():
        print(f"ComfyUI at {url} runs on another computer (its {comfy.custom_nodes} isn't here). Run this there.")
        return 2
    gpu = f"{comfy.gpu} ({comfy.vram_gb:.0f} GB)" if comfy.gpu and comfy.vram_gb else "no GPU found"
    print(f"ComfyUI {comfy.version or '?'} at {url}, {gpu}\n")

    models = local_models(load_registry(get_settings().resolve_registry_dir()))
    by_id = {m.id: m for m in models}
    width = max(len(m.name) for m in models)
    for m in models:
        state, needs = describe(m, comfy)
        print(f"  {state:<8} {m.name:<{width}}  {m.id:<20} {needs}")

    unknown = [i for i in args.model if i not in by_id]
    if unknown:
        print(f"\nNo local model {', '.join(unknown)}; the ids are listed above.")
        return 2
    chosen = [by_id[i] for i in args.model] + [m for m in models if args.fits and comfy.fits(m) and m.id not in args.model]
    if not chosen:
        print("\nSet one up:   uv run python scripts/setup_local.py --model <id>"
              "\nOr all that fit this GPU:   uv run python scripts/setup_local.py --fits")
        return 0
    too_big = [m for m in chosen if not comfy.fits(m)]
    if too_big:
        print(f"\n{', '.join(m.name for m in too_big)} won't run on this GPU, so they aren't set up.")
        return 2

    work = plan(chosen, comfy)
    if not work.packs and not work.jobs:
        print("\nAlready set up: nothing to do.")
        return 0
    python = args.python or comfy.python()
    if work.packs and (problem := python_problem(comfy, python)):
        print(f"\n{problem[0].upper()}{problem[1:]}: pass --python.")
        return 2
    print("\nThis will:")
    for p in work.packs:
        at = f" at commit {p.ref[:8]}" if p.ref else ""
        print(f"  - clone the {p.name} node pack ({p.url}){at} into {comfy.custom_nodes},\n"
              f"    and install its Python packages with {python}")
    if work.jobs:
        homes = ", ".join(sorted({str(d) for _, d in work.jobs}))
        print(f"  - download {len(work.jobs)} model files, {work.bytes / 1e9:.1f} GB, into {homes}"
              f" ({(work.free_bytes() or 0) / 1e9:.0f} GB free)")
        if not work.room():
            print("Not enough free space for them.")
            return 2
    if not args.yes and input("Go ahead? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Nothing changed.")
        return 1

    failed = carry_out(work, comfy, python, say=lambda step: print(f"\n{step}..."))
    if failed:
        print("\nSome steps failed:\n  " + "\n  ".join(failed))
        if any(code in msg for msg in failed for code in ("401", "403")) and not os.environ.get("HF_TOKEN"):
            print("A gated file: accept its license on Hugging Face, set HF_TOKEN, and run this again.")
        return 1
    print("\nDone." + (" Restart ComfyUI to load the new node packs." if work.packs else
                       " ComfyUI sees new model files within a minute."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
