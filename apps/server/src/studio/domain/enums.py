from __future__ import annotations

from enum import StrEnum


class GenStatus(StrEnum):
    draft = "draft"
    awaiting_confirmation = "awaiting_confirmation"
    blocked_budget = "blocked_budget"
    queued = "queued"
    submitting = "submitting"
    in_progress = "in_progress"
    ingesting = "ingesting"
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"
    unknown = "unknown"

    @property
    def terminal(self) -> bool:
        return self in TERMINAL_STATUSES


TERMINAL_STATUSES = frozenset({GenStatus.succeeded, GenStatus.failed, GenStatus.canceled})
# `unknown` is not terminal: a human must resolve it, so it stays visible in the active list.
ACTIVE_STATUSES = frozenset(set(GenStatus) - TERMINAL_STATUSES - {GenStatus.draft})


class AttemptStatus(StrEnum):
    submitting = "submitting"
    submitted = "submitted"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"
    unknown = "unknown"


class GenKind(StrEnum):
    generate = "generate"
    operation = "operation"
    training = "training"
    tool = "tool"


class AssetKind(StrEnum):
    video = "video"
    image = "image"
    audio = "audio"
    mask = "mask"
    mask_video = "mask_video"
    depth = "depth"
    pose = "pose"
    model3d = "model3d"
    splat = "splat"
    panorama = "panorama"
    lora = "lora"
    subtitle = "subtitle"
    document = "document"
    archive = "archive"  # a zip, e.g. the pictures a LoRA trainer learns from


class AssetStatus(StrEnum):
    pending_upload = "pending_upload"
    processing = "processing"
    ready = "ready"
    failed = "failed"


class AssetSource(StrEnum):
    upload = "upload"
    generation = "generation"
    derived = "derived"
    pipeline = "pipeline"
    import_ = "import"
    render = "render"


class ErrorClass(StrEnum):
    transient = "transient"
    route_unavailable = "route_unavailable"
    invalid_request = "invalid_request"
    moderation = "moderation"
    timeout = "timeout"
    ingest_error = "ingest_error"
    internal = "internal"


class LedgerEntry(StrEnum):
    reservation = "reservation"
    release = "release"
    charge = "charge"
    refund = "refund"
    adjustment = "adjustment"


# What counts as spend wherever it's summed: charges, and the corrections booked against them once a provider's bill
# is read (studio/budget/reconcile.py).
SPENT = (LedgerEntry.charge, LedgerEntry.adjustment)


class BreakerState(StrEnum):
    closed = "closed"
    open = "open"
    half_open = "half_open"
