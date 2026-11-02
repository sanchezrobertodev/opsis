"""Topview Platform's text content envelope; all model controls stay in the registry."""


def text_content(*, spec, route, req, payload):
    payload["content"] = [{"type": "text", "text": payload.pop("prompt")}]
    return payload
