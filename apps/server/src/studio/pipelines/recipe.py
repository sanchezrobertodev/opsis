"""The recipe contract.

A recipe is a pure planner. Given its input and the outputs of the steps that have settled so far, `plan()`
returns every step it can define right now. The engine diffs that against the stored steps: new keys are
added, steps whose input changed are redone, steps that left the plan are skipped. So a recipe never
mutates state, map-style fan-out is just a loop, and editing a run's input only reruns what it touches.

Step outputs:
- generate: {"generation_id", "asset_id", "asset_ids", "duration_ms"} (duration of the first output, if timed)
- local / llm: whatever the op returns (assets as "asset_id")
- checkpoint: {"choice": <option value>}
A failed `optional` step settles with {"failed": "<reason>"} instead of failing the run.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel

from studio.db.models import StepKind
from studio.registry.loader import Registry


@dataclass(frozen=True)
class StepSpec:
    key: str
    kind: StepKind
    label: str
    input: dict[str, Any]
    depends_on: tuple[str, ...] = ()
    optional: bool = False

    def digest(self) -> str:
        raw = json.dumps([self.kind.value, self.input, sorted(self.depends_on), self.optional],
                         sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class PlannedJob:
    """A generation the run will make, for estimating its cost before it starts."""

    model_id: str
    mode: str
    params: dict[str, Any] = field(default_factory=dict)
    roles: tuple[str, ...] = ()
    prompt_chars: int = 0  # for routes priced by text length (narration)
    media: tuple[tuple[str, str], ...] = ()  # (role, asset_id) of inputs that already exist, priced at their length
    # (role, seconds) for inputs a run makes before this job, priced at that guess (lipsync on speech from a script)
    planned_seconds: tuple[tuple[str, float], ...] = ()


Settled = dict[str, dict[str, Any]]  # step key -> output (succeeded, or optional-and-failed)


class Recipe(ABC):
    id: ClassVar[str]
    name: ClassVar[str]
    summary: ClassVar[str]
    Input: ClassVar[type[BaseModel]]

    def title(self, inp: Any) -> str:
        return self.name

    def validate(self, inp: Any, reg: Registry) -> list[str]:
        """Problems that would stop the run, checked before it starts."""
        return []

    @abstractmethod
    def jobs(self, inp: Any, reg: Registry) -> list[PlannedJob]:
        """Every generation the run will make (for the up-front estimate)."""

    @abstractmethod
    def plan(self, inp: Any, settled: Settled, reg: Registry) -> list[StepSpec]: ...

    @abstractmethod
    def result(self, inp: Any, settled: Settled) -> dict[str, Any] | None:
        """The run's output once it is complete, else None."""

    def failure(self, inp: Any, settled: Settled) -> str | None:
        """Why the run failed even though no required step did (e.g. every optional take failed), else None.
        Checked once every step has finished."""
        return None


def ok(settled: Settled, key: str) -> dict[str, Any] | None:
    """A settled step's output if it succeeded."""
    out = settled.get(key)
    return out if out is not None and "failed" not in out else None
