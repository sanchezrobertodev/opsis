"""Draft an Opsis model from your own ComfyUI workflow.

    uv run python scripts/import_comfy_workflow.py my_workflow_api.json --id my-wan-lora --name "My Wan LoRA"
    uv run python scripts/import_comfy_workflow.py my_workflow_api.json --id my-wan-lora --dry-run

Save the workflow in ComfyUI with Workflow > Export (API). This writes three files to review:
  workflows/comfy/<template>.json        the graph, as exported
  workflows/comfy/<template>.map.yaml    which node inputs the prompt, seed, size, length and media fill
  registry/models/<type>/<id>.yaml       the model, with one free comfy_local route
then loads the registry and prints any lint errors for it. Nothing is overwritten without --force.
docs/comfy-workflows.md explains the files and what to check.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "server" / "src"))

from studio.config import get_settings  # noqa: E402
from studio.providers import comfy_import  # noqa: E402
from studio.registry.loader import load_registry  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workflow", type=Path, help="the Export (API) JSON file")
    ap.add_argument("--id", required=True, help="the model id, e.g. my-wan-lora (lowercase, dashes)")
    ap.add_argument("--name", help="the name the model picker shows (default: the id)")
    ap.add_argument("--min-vram-gb", type=float, help="GPU memory the graph needs; smaller GPUs skip the route")
    ap.add_argument("--force", action="store_true", help="overwrite files that exist")
    ap.add_argument("--dry-run", action="store_true", help="print the drafts instead of writing them")
    args = ap.parse_args()

    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", args.id):
        print(f"--id {args.id!r}: use lowercase letters, digits, dots and dashes")
        return 2
    try:
        graph = comfy_import.read_graph(args.workflow.read_text(encoding="utf-8"))
        draft = comfy_import.analyze(graph)
    except (OSError, json.JSONDecodeError, comfy_import.NotAWorkflow) as exc:
        print(f"{args.workflow}: {exc}")
        return 2

    settings = get_settings()
    template = comfy_import.template_name(args.id)
    name = args.name or args.id
    files = {
        settings.comfyui_workflows_dir / f"{template}.json": json.dumps(graph, indent=2, ensure_ascii=False) + "\n",
        settings.comfyui_workflows_dir / f"{template}.map.yaml": comfy_import.map_yaml(draft, name),
        settings.resolve_registry_dir() / "models" / draft.output_type / f"{args.id}.yaml":
            comfy_import.model_yaml(draft, args.id, name, template, args.min_vram_gb),
    }
    if args.dry_run:
        for path, text in list(files.items())[1:]:
            print(f"--- {path.relative_to(ROOT).as_posix()}\n{text}")
        return 0
    existing = [p for p in files if p.exists()]
    if existing and not args.force:
        print("these exist (--force overwrites them):\n  " + "\n  ".join(p.relative_to(ROOT).as_posix() for p in existing))
        return 1
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(ROOT).as_posix()}")
    for note in draft.notes:
        print(f"  note: {note}")

    reg = load_registry(settings.resolve_registry_dir())
    errors = [e for e in reg.errors if args.id in e or template in e]
    if errors:
        print("registry lint:\n  " + "\n  ".join(errors))
        return 1
    print(f"\n{args.id} loads cleanly. Next: review the files, start ComfyUI, and try it in Create; then\n"
          f"  uv run pytest apps/server/tests/contract -k {args.id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
