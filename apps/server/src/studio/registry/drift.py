"""Provider-schema drift: does what each route sends still match what the provider publishes?

Each provider with a published request schema has a `Source` that knows where the schema lives and how to find one
endpoint's input in it (fal per-endpoint OpenAPI, Runway's single OpenAPI with a variant per model, ElevenLabs'
OpenAPI, Replicate model versions and World Labs' OpenAPI, the last two needing a key). A schema is boiled down to a
`Summary` (input fields with type, enum, range, default; required fields; output fields), which is what the
baselines in `registry/provider-schemas/` keep. Checks:
- route vs schema: fields a route sends that the endpoint doesn't take, required fields it never sends, and enum
  values it can send that the endpoint rejects (works offline against a baseline too, see the contract tests);
- live vs baseline: fields added, removed or changed since the baseline was taken;
- endpoints that are gone (404, or a model missing from a provider's list) or marked deprecated.
Only schema documents are read; nothing here submits a job or costs anything.
"""

from __future__ import annotations

import itertools
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from studio.registry.loader import Registry
from studio.registry.mapping import TRANSFORMS, build_payload, endpoint_for
from studio.registry.schema import EnumParam, FloatParam, IntParam, Mode, ModelSpec, ParamMapEntry, Route
from studio.registry.validate import MediaRef, RequestError, normalize

PLACEHOLDER = re.compile(r"{(\w+)}")
KEEP = ("type", "enum", "default", "minimum", "maximum", "minItems", "maxItems", "deprecated")
MAX_STEPS = 60  # an int param with at most this many values is checked value by value against a listed choice

# ---------------------------------------------------------------- summaries


@dataclass
class Summary:
    """What an endpoint takes and returns, without the prose (so a baseline only changes when the contract does)."""

    input: dict[str, dict[str, Any]]
    required: list[str]
    output: list[str] = field(default_factory=list)
    deprecated: bool = False

    def to_json(self) -> dict[str, Any]:
        return {"input": self.input, "required": self.required, "output": self.output, "deprecated": self.deprecated}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Summary:
        return cls(data["input"], data["required"], data.get("output", []), data.get("deprecated", False))


def _lookup(doc: dict[str, Any], ref: str) -> dict[str, Any]:
    node: Any = doc
    for part in ref.lstrip("#/").split("/"):
        node = node.get(part, {}) if isinstance(node, dict) else {}
    return node if isinstance(node, dict) else {}


def _resolve(node: Any, doc: dict[str, Any]) -> dict[str, Any]:
    seen = 0
    while isinstance(node, dict) and "$ref" in node and seen < 20:
        node = {**_lookup(doc, node["$ref"]), **{k: v for k, v in node.items() if k != "$ref"}}
        seen += 1
    if isinstance(node, dict) and node.get("allOf"):
        merged: dict[str, Any] = {k: v for k, v in node.items() if k != "allOf"}
        for part in node["allOf"]:
            merged = {**_resolve(part, doc), **merged}
        node = merged
    return node if isinstance(node, dict) else {}


def _variants(node: dict[str, Any]) -> list[Any]:
    return list(node.get("anyOf") or node.get("oneOf") or [])


def _type(node: Any, doc: dict[str, Any], depth: int = 0) -> str:
    n = _resolve(node, doc)
    if depth > 6:
        return "any"
    if vs := _variants(n):
        return "|".join(sorted({_type(v, doc, depth + 1) for v in vs}))
    if "const" in n:
        return type(n["const"]).__name__
    t = n.get("type")
    if isinstance(t, list):
        return "|".join(sorted(str(x) for x in t))
    if t == "array":
        return f"array<{_type(n.get('items', {}), doc, depth + 1)}>"
    if t == "object" or "properties" in n:
        return f"object<{n['title']}>" if n.get("title") else "object"
    return str(t or "any")


def _enum(node: Any, doc: dict[str, Any]) -> list[Any] | None:
    n = _resolve(node, doc)
    if "enum" in n:
        return [v for v in n["enum"] if v is not None]
    if "const" in n:
        return [n["const"]]
    values = [e for v in _variants(n) for e in (_enum(v, doc) or [])]
    return values or None


def _field(node: Any, doc: dict[str, Any]) -> dict[str, Any]:
    n = _resolve(node, doc)
    out: dict[str, Any] = {"type": _type(n, doc)}
    if (values := _enum(n, doc)) is not None:
        out["enum"] = values
    plain = next((_resolve(v, doc) for v in _variants(n) if _resolve(v, doc).get("type") not in (None, "null")), {})
    for key in KEEP[2:]:
        value = n.get(key, plain.get(key))
        if value is not None and value is not False:
            out[key] = value
    return out


def summarize(schema: Any, doc: dict[str, Any], output: Any = None, deprecated: bool = False) -> Summary:
    """Boil a JSON schema (input object, optional output object) down to a Summary."""
    s = _resolve(schema, doc)
    props = s.get("properties") or {}
    out = _resolve(output, doc) if output is not None else {}
    return Summary(
        input={name: _field(prop, doc) for name, prop in sorted(props.items())},
        required=sorted(s.get("required") or []),
        output=sorted((out.get("properties") or {}).keys()),
        deprecated=deprecated or bool(s.get("deprecated")),
    )


# ---------------------------------------------------------------- where each provider publishes its schemas

Fetch = Callable[[str, dict[str, str]], Awaitable[tuple[int, Any]]]  # url, headers -> (status, parsed JSON or None)


@dataclass
class Target:
    """One endpoint (and, where a provider serves several models from one path, one model) to check."""

    provider: str
    endpoint: str
    variant: str | None = None

    @property
    def slug(self) -> str:
        base = re.sub(r"[^A-Za-z0-9._-]+", "__", self.endpoint).strip("_")
        return f"{base}@{self.variant}" if self.variant else base


class Gone(Exception):
    """The provider no longer serves this endpoint or model."""


class Unavailable(Exception):
    """The schema couldn't be read this time (network, a key we don't have)."""


class Source:
    keys: tuple[str, ...] = ()  # settings needed to read the schema
    expand_templates = True  # `{param}` in an endpoint is a param value (fal) rather than a path parameter
    checks_output = False

    def variant(self, route: Route, mode_id: str) -> str | None:
        return None

    async def summary(self, target: Target, fetch: Fetch, settings: Any) -> Summary:
        raise NotImplementedError


class FalSource(Source):
    """fal publishes a queue OpenAPI per endpoint; anyone can read it."""

    checks_output = True

    async def summary(self, target: Target, fetch: Fetch, settings: Any) -> Summary:
        url = f"https://fal.ai/api/openapi/queue/openapi.json?endpoint_id={target.endpoint}"
        status, doc = await fetch(url, {})
        if status == 404 or (status == 200 and not doc):
            raise Gone(f"fal no longer serves {target.endpoint}")
        if status != 200 or not isinstance(doc, dict):
            raise Unavailable(f"fal schema returned {status}")
        paths = doc.get("paths") or {}
        post = (paths.get(f"/{target.endpoint}") or {}).get("post") or {}
        body = (((post.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema")
        if body is None:
            raise Unavailable(f"no request schema for {target.endpoint}")
        result = next((p.get("get") for k, p in paths.items() if k.endswith("/requests/{request_id}")), None) or {}
        out = ((((result.get("responses") or {}).get("200") or {}).get("content") or {})
               .get("application/json") or {}).get("schema")
        return summarize(body, doc, out, deprecated=bool(post.get("deprecated")))


def _body(doc: dict[str, Any], path: str) -> tuple[Any, bool]:
    op = ((doc.get("paths") or {}).get(path) or {}).get("post")
    if op is None:
        raise Gone(f"{path} is no longer in the API")
    content = ((op.get("requestBody") or {}).get("content") or {})
    schema = (content.get("application/json") or next(iter(content.values()), {})).get("schema")
    return schema, bool(op.get("deprecated"))


class RunwaySource(Source):
    """Runway's one OpenAPI document: each path's body is a choice of models (`model` names the variant)."""

    URL = "https://docs.dev.runwayml.com/openapi.json"

    def variant(self, route: Route, mode_id: str) -> str | None:
        return str(route.modes[mode_id].fixed.get("model") or "") or None

    async def summary(self, target: Target, fetch: Fetch, settings: Any) -> Summary:
        status, doc = await fetch(self.URL, {})
        if status != 200 or not isinstance(doc, dict):
            raise Unavailable(f"Runway OpenAPI returned {status}")
        schema, deprecated = _body(doc, f"/v1/{target.endpoint.lstrip('/')}")
        options = _variants(_resolve(schema, doc)) or [schema]
        for option in options:
            o = _resolve(option, doc)
            model = _resolve((o.get("properties") or {}).get("model", {}), doc)
            names = {o.get("title"), model.get("const"), *(model.get("enum") or [])}
            if target.variant is None or target.variant in names:
                return summarize(o, doc, deprecated=deprecated)
        raise Gone(f"Runway no longer offers {target.variant} on {target.endpoint}")


class ElevenLabsSource(Source):
    """ElevenLabs' OpenAPI; `{voice_id}` in an endpoint is a path parameter."""

    URL = "https://api.elevenlabs.io/openapi.json"
    expand_templates = False

    async def summary(self, target: Target, fetch: Fetch, settings: Any) -> Summary:
        status, doc = await fetch(self.URL, {})
        if status != 200 or not isinstance(doc, dict):
            raise Unavailable(f"ElevenLabs OpenAPI returned {status}")
        schema, deprecated = _body(doc, f"/v1/{target.endpoint.lstrip('/')}")
        return summarize(schema, doc, deprecated=deprecated)


class ReplicateSource(Source):
    """A Replicate model's latest version carries its input schema (reading it needs a token, and is free)."""

    keys = ("replicate_api_token",)

    async def summary(self, target: Target, fetch: Fetch, settings: Any) -> Summary:
        status, doc = await fetch(f"https://api.replicate.com/v1/models/{target.endpoint}",
                                  {"Authorization": f"Bearer {settings.replicate_api_token}"})
        if status == 404:
            raise Gone(f"Replicate no longer has {target.endpoint}")
        if status != 200 or not isinstance(doc, dict):
            raise Unavailable(f"Replicate returned {status}")
        schema = ((doc.get("latest_version") or {}).get("openapi_schema") or {})
        return summarize({"$ref": "#/components/schemas/Input"}, schema)


class WorldLabsSource(Source):
    """World Labs' OpenAPI sits behind the API key (reading it is free)."""

    keys = ("worldlabs_api_key",)

    async def summary(self, target: Target, fetch: Fetch, settings: Any) -> Summary:
        status, doc = await fetch("https://api.worldlabs.ai/marble/v1/openapi.json",
                                  {"WLT-Api-Key": settings.worldlabs_api_key})
        if status != 200 or not isinstance(doc, dict):
            raise Unavailable(f"World Labs OpenAPI returned {status}")
        schema, deprecated = _body(doc, f"/{target.endpoint.lstrip('/')}")
        return summarize(schema, doc, deprecated=deprecated)


SOURCES: dict[str, Source] = {"fal": FalSource(), "runway": RunwaySource(), "elevenlabs": ElevenLabsSource(),
                              "replicate": ReplicateSource(), "worldlabs": WorldLabsSource()}
OWN = {"mock", "comfy_local", "local_tools", "runpod_comfy"}  # adapters running our own code or workflows


# ---------------------------------------------------------------- what a route sends


def _top(path: str) -> str:
    return path.split(".")[0].removesuffix("[]")


def _sample_media(mode: Mode) -> list[MediaRef]:
    return [MediaRef(role=s.role, asset_id=f"{s.role}-{i}", kind=s.kinds[0])
            for s in mode.medias for i in range(max(1, s.min or 0) if s.required else 0)]


def _accepted(spec: ModelSpec, mode: Mode, params: dict[str, Any]) -> bool:
    """Whether a request with these params gets past validation (the model's own rules) in this mode."""
    try:
        normalize(spec, mode.id, "a sample shot" if mode.prompt != "none" else "", params, _sample_media(mode))
    except (RequestError, ValueError):
        return False
    return True


def _values(spec: ModelSpec, mode: Mode, name: str, entry: str | ParamMapEntry) -> list[Any] | None:
    """The values a param can take on the wire in this mode, when it's a choice (None: any value). Choices the
    model's rules refuse in this mode are left out (Gen-4.5 text to video is only 16:9 or 9:16)."""
    p = spec.param(name)
    if isinstance(p, EnumParam):
        choices: list[Any] = list(p.options)
    elif isinstance(p, IntParam) and (p.max - p.min) / (p.step or 1) <= MAX_STEPS:
        choices = list(range(p.min, p.max + 1, p.step or 1))  # e.g. durations, which providers often list
    elif isinstance(p, IntParam | FloatParam):
        choices = [p.min, p.max]  # a wide range: its ends are what a provider's bounds can reject
    else:
        return None
    options = [o for o in choices if _accepted(spec, mode, {name: o})]
    if isinstance(entry, ParamMapEntry) and entry.transform:
        fn = TRANSFORMS.get(entry.transform)
        try:
            return [fn(o, entry.args) for o in options] if fn else None
        except (TypeError, ValueError):
            return None
    return options


def sent_fields(spec: ModelSpec, route: Route, mode: Mode) -> dict[str, list[Any] | None]:
    """Top-level fields a route mode can send, with the values it can send where they're a fixed set."""
    rm, m = route.modes[mode.id], route.map
    fields: dict[str, list[Any] | None] = {}
    if mode.prompt != "none" and m.prompt:
        fields[_top(m.prompt)] = None
    if spec.prompt.supports_negative and m.negative_prompt and "negative_prompt" not in rm.drop:
        fields[_top(m.negative_prompt)] = None
    for p in spec.params_for(mode.id):
        if p.name in rm.drop or p.name in route.unsupported_params:
            continue
        entry = m.params.get(p.name)
        if entry is None:
            if m.passthrough:
                fields[p.name] = _values(spec, mode, p.name, p.name)
            continue
        target = entry if isinstance(entry, str) else entry.to
        if target and "." not in target:
            fields[target] = _values(spec, mode, p.name, entry)
        elif target:
            fields.setdefault(_top(target), None)
    for slot in mode.medias:
        fields.setdefault(_top(rm.media.get(slot.role) or m.media.get(slot.role, slot.role)), None)
    for key, value in rm.fixed.items():
        fields[_top(key)] = [value] if "." not in key and not isinstance(value, dict | list) else None
    # payload hooks can rename or add fields; a sample request shows what actually goes out
    try:
        refs = _sample_media(mode)
        req = normalize(spec, mode.id, "a sample shot" if mode.prompt != "none" else "", {}, refs)
        staged = {r.role: ["https://example.test/x"] * sum(1 for x in refs if x.role == r.role) for r in refs}
        _, payload, _ = build_payload(spec, route, req, staged)
        for key in payload:
            fields.setdefault(key, None)
    except (RequestError, ValueError, KeyError, TypeError):
        pass
    return fields


# ---------------------------------------------------------------- findings


@dataclass
class Finding:
    route_id: str
    target: str
    problem: str
    attention: bool = True  # False: a change worth knowing about that doesn't break this route


def check_route(spec: ModelSpec, route: Route, mode: Mode, summary: Summary, target: Target,
                checks_output: bool = False) -> list[Finding]:
    """What would go wrong sending this route mode to an endpoint with this schema."""
    out: list[Finding] = []
    note = lambda problem, attention=True: out.append(Finding(route.id, target.slug, problem, attention))  # noqa: E731
    if summary.deprecated:
        note(f"{target.endpoint} is marked deprecated")
    fields = sent_fields(spec, route, mode)
    for name in sorted(set(fields) - set(summary.input)):
        note(f"{mode.id}: sends `{name}`, which the endpoint doesn't take")
    for name in sorted(set(summary.required) - set(fields)):
        note(f"{mode.id}: never sends `{name}`, which the endpoint requires")
    for name, values in sorted(fields.items()):
        live = summary.input.get(name, {})
        if live.get("deprecated"):
            note(f"{mode.id}: `{name}` is deprecated")
        if values is None:
            continue
        if "enum" in live:
            accepted = {str(v) for v in live["enum"]}
            if bad := [v for v in values if v not in ("", None) and str(v) not in accepted]:
                note(f"{mode.id}: `{name}` can be {', '.join(map(str, bad))}, which the endpoint rejects "
                     f"(it takes {', '.join(map(str, live['enum']))})")
        elif (low := live.get("minimum")) is not None or live.get("maximum") is not None:
            high = live.get("maximum")
            out_of_range = [v for v in values if (n := _number(v)) is not None
                            and ((low is not None and n < low) or (high is not None and n > high))]
            if out_of_range:
                note(f"{mode.id}: `{name}` can be {', '.join(map(str, out_of_range))}, outside the endpoint's "
                     f"range ({low if low is not None else '…'} to {high if high is not None else '…'})")
    if checks_output and summary.output:
        main = [] if route.output.unpublished else [route.output.path]
        for path in [*main, *(x.path for x in route.output.extras)]:
            tops = {_top(alt.strip()) for alt in path.split("|")}
            if not tops & set(summary.output):
                note(f"returns {', '.join(summary.output)}; the route reads `{path}`")
    return out


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def diff(old: Summary, new: Summary) -> list[str]:
    """How an endpoint's contract changed since the baseline."""
    changes: list[str] = []
    for name in sorted(set(new.input) - set(old.input)):
        changes.append(f"new field `{name}` ({new.input[name]['type']})")
    for name in sorted(set(old.input) - set(new.input)):
        changes.append(f"field `{name}` was removed")
    for name in sorted(set(old.input) & set(new.input)):
        a, b = old.input[name], new.input[name]
        for key in KEEP:
            if a.get(key) != b.get(key):
                if key == "enum" and a.get(key) and b.get(key):
                    added = [v for v in b["enum"] if v not in a["enum"]]
                    gone = [v for v in a["enum"] if v not in b["enum"]]
                    parts = ([f"adds {', '.join(map(str, added))}"] if added else []) + (
                        [f"drops {', '.join(map(str, gone))}"] if gone else [])
                    changes.append(f"`{name}` choices: {'; '.join(parts) or 'reordered'}")
                else:
                    changes.append(f"`{name}` {key}: {a.get(key)!r} -> {b.get(key)!r}")
    if old.required != new.required:
        changes.append(f"required fields: {old.required} -> {new.required}")
    if old.output != new.output:
        changes.append(f"output fields: {old.output} -> {new.output}")
    if new.deprecated and not old.deprecated:
        changes.append("now marked deprecated")
    return changes


# ---------------------------------------------------------------- a run over the whole registry


@dataclass
class Report:
    attention: list[Finding] = field(default_factory=list)
    changed: list[tuple[str, list[str]]] = field(default_factory=list)  # (target, changes since the baseline)
    gone: list[Finding] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)  # couldn't read the schema this time, and why
    unpublished: list[str] = field(default_factory=list)  # providers without a schema to read
    status: list[str] = field(default_factory=list)  # models whose own status needs a look
    checked: int = 0
    updated: int = 0

    @property
    def needs_a_look(self) -> bool:
        return bool(self.attention or self.changed or self.gone or self.status)


def targets_for(reg: Registry) -> dict[str, list[tuple[ModelSpec, Route, Mode, Target]]]:
    """Every endpoint the enabled routes call, keyed by provider/slug, with the route modes that call it."""
    out: dict[str, list[tuple[ModelSpec, Route, Mode, Target]]] = {}
    for spec in reg.models.values():
        for route in spec.routes:
            source = SOURCES.get(route.provider)
            if not route.enabled or source is None:
                continue
            for mode_id, rm in route.modes.items():
                mode = spec.mode(mode_id)
                if mode is None:
                    continue
                for endpoint in _expand(spec, mode, rm.endpoint, source):
                    t = Target(route.provider, endpoint, source.variant(route, mode_id))
                    out.setdefault(f"{t.provider}/{t.slug}", []).append((spec, route, mode, t))
    return out


def _expand(spec: ModelSpec, mode: Mode, template: str, source: Source) -> list[str]:
    names = PLACEHOLDER.findall(template)
    if not names or not source.expand_templates:
        return [template]
    choices = []
    for name in names:
        p = next((p for p in spec.params_for(mode.id) if p.name == name), None)
        if not isinstance(p, EnumParam):
            return [template]
        choices.append([(name, o) for o in p.options])
    return sorted({endpoint_for(template, dict(combo)) for combo in itertools.product(*choices)})


def baseline_path(root: Path, target: Target) -> Path:
    return root / target.provider / f"{target.slug}.json"


def load_baseline(root: Path, target: Target) -> Summary | None:
    path = baseline_path(root, target)
    return Summary.from_json(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else None


def save_baseline(root: Path, target: Target, summary: Summary) -> None:
    path = baseline_path(root, target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"endpoint": target.endpoint, "variant": target.variant, **summary.to_json()},
                               indent=1, sort_keys=False) + "\n", encoding="utf-8")


async def run(reg: Registry, fetch: Fetch, settings: Any, root: Path, *, providers: set[str] | None = None,
              update: bool = False) -> Report:
    report = Report()
    for spec in reg.models.values():
        if spec.status in ("deprecated", "shutdown") and any(r.enabled for r in spec.routes):
            report.status.append(f"{spec.id} is {spec.status} but still has enabled routes")
    adapter = {pid: p.adapter for pid, p in reg.providers.items()}
    seen_unpublished = sorted({r.provider for s in reg.models.values() for r in s.routes if r.enabled
                               and r.provider not in SOURCES and adapter.get(r.provider, r.provider) not in OWN})
    report.unpublished = [p for p in seen_unpublished if not providers or p in providers]
    for key, uses in sorted(targets_for(reg).items()):
        target = uses[0][3]
        if providers and target.provider not in providers:
            continue
        source = SOURCES[target.provider]
        if missing := [k for k in source.keys if not getattr(settings, k, "")]:
            report.unchecked.append(f"{key}: needs {', '.join(m.upper() for m in missing)} to read the schema")
            continue
        try:
            live = await source.summary(target, fetch, settings)
        except Gone as exc:
            for spec, route, _mode, _t in uses:
                report.gone.append(Finding(route.id, target.slug, f"{exc}; {spec.id} is still {spec.status}"))
            continue
        except Unavailable as exc:
            report.unchecked.append(f"{key}: {exc}")
            continue
        report.checked += 1
        for spec, route, mode, t in uses:
            report.attention += check_route(spec, route, mode, live, t, source.checks_output)
        old = load_baseline(root, target)
        if old is not None and (changes := diff(old, live)):
            report.changed.append((key, changes))
        if update and (old is None or old.to_json() != live.to_json()):
            save_baseline(root, target, live)
            report.updated += 1
    report.attention = _dedupe(report.attention)
    report.gone = _dedupe(report.gone)
    return report


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple[str, str, str]] = set()
    out = []
    for f in findings:
        if (k := (f.route_id, f.target, f.problem)) not in seen:
            seen.add(k)
            out.append(f)
    return out


def render(report: Report, day: str) -> str:
    lines = [f"# Provider schema check, {day}", "",
             f"Read {report.checked} endpoint schemas. " + ("Something needs a look." if report.needs_a_look
                                                            else "Nothing changed that affects a route."), ""]

    def section(title: str, rows: list[str]) -> None:
        if rows:
            lines.extend([f"## {title}", "", *[f"- {r}" for r in rows], ""])

    section("Gone or shut down", [f"`{f.route_id}` ({f.target}): {f.problem}" for f in report.gone])
    section("Routes that would break", [f"`{f.route_id}` ({f.target}): {f.problem}" for f in report.attention])
    section("Changed since the baseline", [f"{key}: {'; '.join(changes)}" for key, changes in report.changed])
    section("Model status", report.status)
    section("Couldn't check", report.unchecked)
    section("No published schema", [f"{p}: checked by its contract tests only" for p in report.unpublished])
    if report.updated:
        lines.append(f"Updated {report.updated} baselines in registry/provider-schemas/.")
    return "\n".join(lines).rstrip() + "\n"
