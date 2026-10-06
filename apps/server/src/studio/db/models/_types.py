from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Enum


def str_enum(cls: type[StrEnum]) -> Enum:
    """Store a StrEnum as text with a CHECK constraint (adding a value is a one-line migration)."""
    return Enum(
        cls,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
        name=cls.__name__.lower(),
    )
