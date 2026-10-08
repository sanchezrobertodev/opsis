"""The wan-core worker's result packing (infra/runpod/wan-core/handler.py): inline base64 or the job's upload slots."""

from __future__ import annotations

import base64
import importlib.util
import sys
import types
from pathlib import Path

import pytest
import requests

HANDLER = Path(__file__).resolve().parents[4] / "infra" / "runpod" / "wan-core" / "handler.py"


@pytest.fixture
def handler(monkeypatch):
    monkeypatch.setitem(sys.modules, "runpod", types.ModuleType("runpod"))  # the worker SDK isn't installed here
    spec = importlib.util.spec_from_file_location("wan_core_handler", HANDLER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def found(tmp_path: Path, name: str, size: int) -> dict:
    path = tmp_path / name
    path.write_bytes(b"v" * size)
    return {"node": "9", "filename": name, "kind": "video", "mime": "video/mp4", "path": path}


SLOT = {"url": "https://v3b.fal.media/files/up/slot?signature=s", "file_url": "https://v3b.fal.media/files/up/o.mp4"}


def test_small_results_stay_inline_even_with_a_slot(handler, tmp_path, monkeypatch):
    monkeypatch.setattr(handler.requests, "put", lambda *a, **k: pytest.fail("small outputs aren't uploaded"))
    [out] = handler.encode_outputs([found(tmp_path, "a.mp4", 1000)], [SLOT])
    assert base64.b64decode(out["data"]) == b"v" * 1000 and "url" not in out and out["bytes"] == 1000


def test_big_results_go_to_the_slot(handler, tmp_path, monkeypatch):
    puts = []

    class Ok:
        def raise_for_status(self):
            pass

    def put(url, data, headers, timeout):
        puts.append((url, len(data.read()), headers["Content-Type"]))
        return Ok()

    monkeypatch.setattr(handler, "INLINE_MB", 0.001)  # 1 KB
    monkeypatch.setattr(handler, "MAX_OUTPUT_MB", 0.003)
    monkeypatch.setattr(handler.requests, "put", put)
    big, small = found(tmp_path, "big.mp4", 5000), found(tmp_path, "small.mp4", 500)
    out = handler.encode_outputs([small, big], [SLOT])
    assert puts == [(SLOT["url"], 5000, "video/mp4")]
    assert out[1]["url"] == SLOT["file_url"] and "data" not in out[1] and out[1]["bytes"] == 5000
    assert "data" in out[0] and "url" not in out[0]  # the small one still comes back inline


def test_a_failed_upload_falls_back_inline_when_it_fits(handler, tmp_path, monkeypatch):
    def put(*a, **k):
        raise requests.HTTPError("403 link expired")

    monkeypatch.setattr(handler, "INLINE_MB", 0.001)
    monkeypatch.setattr(handler.requests, "put", put)
    [out] = handler.encode_outputs([found(tmp_path, "a.mp4", 5000)], [SLOT])
    assert "data" in out and "url" not in out

    monkeypatch.setattr(handler, "MAX_OUTPUT_MB", 0.002)
    with pytest.raises(ValueError, match="its upload failed"):
        handler.encode_outputs([found(tmp_path, "b.mp4", 5000)], [SLOT])


def test_without_a_slot_the_inline_limit_still_holds(handler, tmp_path, monkeypatch):
    monkeypatch.setattr(handler, "MAX_OUTPUT_MB", 0.002)
    with pytest.raises(ValueError, match="render a shorter or smaller clip"):
        handler.encode_outputs([found(tmp_path, "a.mp4", 5000)], None)
