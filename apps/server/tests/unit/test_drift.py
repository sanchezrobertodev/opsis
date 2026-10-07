"""The schema drift check on small hand-made schemas: summaries, route checks, diffs and a whole run."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from studio.registry import drift
from studio.registry.loader import Registry
from studio.registry.schema import ModelSpec

SPEC = ModelSpec.model_validate({
    "id": "toy", "name": "Toy", "family": "toy", "vendor": "toy", "output_type": "video",
    "prompt": {"max_chars": 500},
    "parameters": [
        {"name": "duration", "type": "int", "min": 4, "max": 8, "step": 2, "default": 4},
        {"name": "resolution", "type": "enum", "options": ["480p", "720p", "1080p"], "default": "720p"},
    ],
    "rules": [{"when": {"mode": "t2v", "resolution": "1080p"}, "require": {"resolution": ["480p", "720p"]},
               "message": "no 1080p from text"}],
    "modes": [{"id": "t2v", "label": "Text", "ops": ["t2v"]},
              {"id": "i2v", "label": "Image", "ops": ["i2v"],
               "medias": [{"role": "start_image", "kinds": ["image"], "required": True}]}],
    "routes": [{"id": "toy@fal", "provider": "fal",
                "modes": {"t2v": {"endpoint": "toy/t2v"}, "i2v": {"endpoint": "toy/i2v"}},
                "map": {"params": {"duration": {"to": "duration", "transform": "str"}},
                        "media": {"start_image": "image_url"}},
                "pricing": {"basis": "per_request", "rates": [{"usd": 0.1}], "verified": "2026-10-07", "source": "x"},
                "output": {"path": "video.url", "kind": "video"}}],
})


def openapi(endpoint: str, props: dict[str, Any], required: list[str], out: dict[str, Any] | None = None):
    return {
        "paths": {
            f"/{endpoint}": {"post": {"requestBody": {"content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/In"}}}}}},
            f"/{endpoint}/requests/{{request_id}}": {"get": {"responses": {"200": {"content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/Out"}}}}}}},
        },
        "components": {"schemas": {
            "In": {"type": "object", "properties": props, "required": required},
            "Out": {"type": "object", "properties": out or {"video": {"$ref": "#/components/schemas/File"}}},
            "File": {"type": "object", "title": "File", "properties": {"url": {"type": "string"}}},
            "Res": {"type": "string", "enum": ["480p", "720p", "1080p"]},
        }},
    }


GOOD = {"prompt": {"type": "string"}, "duration": {"enum": ["4", "6", "8"], "type": "string"},
        "resolution": {"allOf": [{"$ref": "#/components/schemas/Res"}], "default": "720p"},
        "image_url": {"anyOf": [{"type": "string"}, {"type": "null"}]}, "seed": {"type": "integer"}}


def summary(props=GOOD, required=("prompt",), **kw) -> drift.Summary:
    doc = openapi("toy/t2v", props, list(required), **kw)
    return drift.summarize({"$ref": "#/components/schemas/In"}, doc, {"$ref": "#/components/schemas/Out"})


def test_summaries_resolve_refs_and_keep_only_the_contract():
    s = summary()
    assert s.input["resolution"] == {"type": "string", "enum": ["480p", "720p", "1080p"], "default": "720p"}
    assert s.input["image_url"]["type"] == "null|string"
    assert s.required == ["prompt"] and s.output == ["video"]


def test_a_matching_schema_finds_nothing_and_rules_narrow_the_choices_checked():
    t2v = SPEC.mode("t2v")
    # the endpoint takes no 1080p, but the model's rule never sends it in t2v
    no_1080 = {**GOOD, "resolution": {"type": "string", "enum": ["480p", "720p"]}}
    assert drift.check_route(SPEC, SPEC.routes[0], t2v, summary(no_1080), drift.Target("fal", "toy/t2v"), True) == []


def test_route_problems_are_reported():
    i2v = SPEC.mode("i2v")
    props = {k: v for k, v in GOOD.items() if k != "image_url"}
    props["duration"] = {"type": "string", "enum": ["4", "8"]}
    props["fps"] = {"type": "integer"}
    found = drift.check_route(SPEC, SPEC.routes[0], i2v, summary(props, ("prompt", "fps"), out={"clip": {}}),
                              drift.Target("fal", "toy/i2v"), True)
    problems = " | ".join(f.problem for f in found)
    assert "sends `image_url`, which the endpoint doesn't take" in problems
    assert "never sends `fps`, which the endpoint requires" in problems
    assert "`duration` can be 6, which the endpoint rejects" in problems
    assert "the route reads `video.url`" in problems


def test_diff_lists_what_changed():
    old = summary()
    new_props = {**GOOD, "duration": {"type": "string", "enum": ["4", "6", "8", "10"]}, "loop": {"type": "boolean"}}
    del new_props["seed"]
    changes = drift.diff(old, summary(new_props, ("prompt", "duration")))
    assert "new field `loop` (boolean)" in changes
    assert "field `seed` was removed" in changes
    assert "`duration` choices: adds 10" in changes
    assert any(c.startswith("required fields:") for c in changes)
    assert drift.diff(old, summary()) == []


async def test_a_run_reports_gone_endpoints_drift_and_needed_keys(tmp_path):
    docs = {"toy/t2v": openapi("toy/t2v", GOOD, ["prompt"]), "toy/i2v": None}

    async def fetch(url: str, headers: dict[str, str]):
        endpoint = url.split("endpoint_id=")[-1]
        return (200, docs[endpoint]) if docs.get(endpoint) else (404, None)

    reg = Registry(models={"toy": SPEC}, providers={}, hash="test")
    settings = SimpleNamespace()
    report = await drift.run(reg, fetch, settings, tmp_path, update=True)
    assert [f.route_id for f in report.gone] == ["toy@fal"] and "no longer serves toy/i2v" in report.gone[0].problem
    assert report.checked == 1 and report.updated == 1 and not report.attention and not report.changed
    assert (tmp_path / "fal" / "toy__t2v.json").exists()

    docs["toy/t2v"] = openapi("toy/t2v", {**GOOD, "loop": {"type": "boolean"}}, ["prompt"])
    again = await drift.run(reg, fetch, settings, tmp_path)
    assert again.changed == [("fal/toy__t2v", ["new field `loop` (boolean)"])]
    text = drift.render(again, "2026-10-07")
    assert "## Gone or shut down" in text and "## Changed since the baseline" in text


async def test_runway_picks_the_model_variant_and_reports_a_dropped_model():
    doc = {"paths": {"/v1/text_to_video": {"post": {"requestBody": {"content": {"application/json": {"schema": {
        "oneOf": [{"title": "gen4.5", "type": "object", "required": ["model"],
                   "properties": {"model": {"const": "gen4.5"}, "ratio": {"enum": ["1280:720"]}}}]}}}}}}}}

    async def fetch(url, headers):
        return 200, doc

    source = drift.RunwaySource()
    s = await source.summary(drift.Target("runway", "text_to_video", "gen4.5"), fetch, None)
    assert s.input["ratio"]["enum"] == ["1280:720"]
    try:
        await source.summary(drift.Target("runway", "text_to_video", "gen3"), fetch, None)
    except drift.Gone as exc:
        assert "no longer offers gen3" in str(exc)
    else:
        raise AssertionError("a model missing from the list is gone")
