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
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "server" / "src"))

from studio.config import get_settings  # noqa: E402
from studio.providers.local_setup import (  # noqa: E402
    Comfy,
    LocalModel,
    comfy_at,
    install_pack,
    local_models,
    python_version,
)
from studio.registry.loader import load_registry  # noqa: E402

_spec = importlib.util.spec_from_file_location("fetch_models", ROOT / "infra" / "runpod" / "wan-core" / "fetch_models.py")
fetch_models = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch_models)


def gb(files) -> float:
    return sum(f.get("bytes", 0) for f in files) / 1e9


def existing(path: Path) -> Path:
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def describe(m: LocalModel, comfy: Comfy) -> tuple[str, str]:
    """(state, what it still needs)."""
    if not comfy.fits(m):
        return "too big", f"needs a GPU with {m.min_vram_gb:g} GB"
    needs = [f"the {p.name} node pack" for p in m.packs if not comfy.has_pack(p)]
    missing = [f for f in m.files if not comfy.has_file(f)]
    if missing:
        needs.append(f"{len(missing)} model files, {gb(missing):.1f} GB")
    return ("ready", "") if not needs else ("needs", ", ".join(needs))


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

    packs = list({p.url: p for m in chosen for p in m.packs if not comfy.has_pack(p)}.values())
    files = list({(f["folder"], f["name"]): f for m in chosen for f in m.files if not comfy.has_file(f)}.values())
    if not packs and not files:
        print("\nAlready set up: nothing to do.")
        return 0
    python = args.python or comfy.python()
    if packs:
        if python is None:
            print("\nCan't find ComfyUI's Python to install the node packs into: pass --python.")
            return 2
        if comfy.python_version and python_version(python) != comfy.python_version:
            print(f"\n{python} isn't the Python ComfyUI runs on ({comfy.python_version.split()[0]}): pass --python.")
            return 2
    jobs = [(f, comfy.paths(f["folder"])[0]) for f in files]
    print("\nThis will:")
    for p in packs:
        at = f" at commit {p.ref[:8]}" if p.ref else ""
        print(f"  - clone the {p.name} node pack ({p.url}){at} into {comfy.custom_nodes},\n"
              f"    and install its Python packages with {python}")
    if files:
        homes = sorted({str(d) for _, d in jobs})
        free = min(shutil.disk_usage(existing(Path(h))).free for h in homes) / 1e9
        print(f"  - download {len(files)} model files, {gb(files):.1f} GB, into {', '.join(homes)} ({free:.0f} GB free)")
        if free < gb(files) + 1:
            print("Not enough free space for them.")
            return 2
    if not args.yes and input("Go ahead? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Nothing changed.")
        return 1

    failed = []
    for p in packs:
        print(f"\nInstalling {p.name}...")
        try:
            install_pack(p, comfy, python)
        except subprocess.CalledProcessError as exc:
            failed.append(f"{p.name}: {' '.join(map(str, exc.cmd[:4]))}... exited with {exc.returncode}")
        except OSError as exc:  # no git
            failed.append(f"{p.name}: {exc}")
    if jobs:
        print(f"\nDownloading {len(jobs)} files...")
        # each into the folder ComfyUI reads it from
        _, bad = fetch_models.fetch([({**f, "folder": d.name}, d.parent) for f, d in jobs])
        failed += bad
    if failed:
        print("\nSome steps failed:\n  " + "\n  ".join(failed))
        if any(code in msg for msg in failed for code in ("401", "403")) and not os.environ.get("HF_TOKEN"):
            print("A gated file: accept its license on Hugging Face, set HF_TOKEN, and run this again.")
        return 1
    print("\nDone." + (" Restart ComfyUI to load the new node packs." if packs else
                       " ComfyUI sees new model files within a minute."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
