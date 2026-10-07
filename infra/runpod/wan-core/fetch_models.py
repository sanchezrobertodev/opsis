"""Download the wan-core model files onto the network volume (models.yaml lists them).

Runs three ways, all idempotent (files already present at the right size are skipped):
  - as a worker job: {"input": {"action": "fetch_models", "groups": ["core"]}}  (bills GPU time while it downloads)
  - on a CPU pod with the volume at /workspace:  python /fetch_models.py --dest /workspace/models
  - locally, to see what would be fetched:       python fetch_models.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests
import yaml

MANIFEST = Path(os.environ.get("WAN_CORE_MANIFEST", Path(__file__).with_name("models.yaml")))
PARALLEL = int(os.environ.get("WAN_CORE_FETCH_PARALLEL", "4"))


def load(manifest: Path, groups: list[str] | None = None) -> list[dict[str, Any]]:
    files = yaml.safe_load(manifest.read_text(encoding="utf-8"))["files"]
    return [f for f in files if not groups or f.get("group", "core") in groups]


def status(manifest: Path, dest: Path, groups: list[str] | None = None) -> dict[str, Any]:
    present, missing = [], []
    for f in load(manifest, groups):
        path = dest / f["folder"] / f["name"]
        ok = path.is_file() and (not f.get("bytes") or path.stat().st_size == f["bytes"])
        (present if ok else missing).append(f"{f['folder']}/{f['name']}")
    return {"present": present, "missing": missing}


def _download(f: dict[str, Any], dest: Path) -> str:
    path = dest / f["folder"] / f["name"]
    if path.is_file() and (not f.get("bytes") or path.stat().st_size == f["bytes"]):
        return f"skip {path.name}"
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + ".part")
    auth = {}
    if os.environ.get("HF_TOKEN") and "huggingface.co" in f["url"]:
        auth["Authorization"] = f"Bearer {os.environ['HF_TOKEN']}"
    started = time.monotonic()
    for attempt in range(1, 4):
        try:
            have = part.stat().st_size if part.exists() else 0
            headers = {**auth, **({"Range": f"bytes={have}-"} if have else {})}
            with requests.get(f["url"], headers=headers, stream=True, timeout=(15, 120)) as r:
                if r.status_code == 416:  # the .part is already complete
                    break
                r.raise_for_status()
                mode = "ab" if have and r.status_code == 206 else "wb"
                with open(part, mode) as fh:
                    for chunk in r.iter_content(8 << 20):
                        fh.write(chunk)
            break
        except requests.RequestException as exc:
            if attempt == 3:
                raise RuntimeError(f"{f['name']}: {exc}") from exc
            time.sleep(5 * attempt)
    size = part.stat().st_size
    if f.get("bytes") and size != f["bytes"]:
        raise RuntimeError(f"{f['name']}: got {size} bytes, expected {f['bytes']}")
    part.rename(path)
    mb_s = size / 1e6 / max(time.monotonic() - started, 0.001)
    return f"got {path.name} ({size / 1e9:.1f} GB at {mb_s:.0f} MB/s)"


def fetch_all(manifest: Path, dest: Path, groups: list[str] | None = None) -> dict[str, Any]:
    files = load(manifest, groups)
    done, failed = [], []
    with ThreadPoolExecutor(PARALLEL) as pool:
        futures = {pool.submit(_download, f, dest): f for f in files}
        for fut in as_completed(futures):
            try:
                msg = fut.result()
                done.append(msg)
            except Exception as exc:  # report every file, keep going
                msg = f"FAILED {exc}"
                failed.append(msg)
            print(f"fetch_models | {msg}", flush=True)
    return {"done": done, "failed": failed, **status(manifest, dest, groups)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", default=os.environ.get("WAN_CORE_MODELS", "/runpod-volume/models"))
    ap.add_argument("--group", action="append", help="only these manifest groups (default: all)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.dry_run:
        files = load(MANIFEST, args.group)
        for f in files:
            print(f"{f['folder']}/{f['name']}  {f.get('bytes', 0) / 1e9:.1f} GB  [{f.get('group', 'core')}]")
        print(f"total {sum(f.get('bytes', 0) for f in files) / 1e9:.1f} GB in {len(files)} files")
        return 0
    result = fetch_all(MANIFEST, Path(args.dest), args.group)
    print(json.dumps({k: v for k, v in result.items() if k != "present"}, indent=2))
    return 1 if result["failed"] or result["missing"] else 0


if __name__ == "__main__":
    sys.exit(main())
