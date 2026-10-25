"""Pika Seedance's local quote ceiling; stripped before sending its strict request schema."""

from studio.registry.pricing import estimate


def seedance(*, spec, route, req, payload):
    for kind in ("video", "audio"):
        refs = [m for m in req.media if m.kind == kind]
        if any(m.duration_ms is None for m in refs):
            raise ValueError(f"Pika needs known {kind} durations before pricing")
        if any(not 2000 <= m.duration_ms <= 30000 for m in refs):
            raise ValueError(f"Pika {kind} references must be 2 to 30 seconds")
        if sum(m.duration_ms for m in refs) > 30000:
            raise ValueError(f"Pika {kind} references must total at most 30 seconds")
    payload["_studio_max_cost_usd"] = str(estimate(
        route.pricing, req.params, req.prompt, req.media, req.mode).usd)
    return payload
