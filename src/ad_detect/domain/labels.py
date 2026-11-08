"""Append-only human label revisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .models import Classification


class BoundaryKind(StrEnum):
    NONE = "none"
    START = "start"
    END = "end"


@dataclass(frozen=True, slots=True)
class LabelRevision:
    revision_id: str
    session_id: str
    start_ns: int
    end_ns: int
    label: Classification
    boundary: BoundaryKind
    confidence: float
    author: str
    created_at_ns: int
    supersedes_id: str | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if self.end_ns <= self.start_ns:
            raise ValueError("label end must follow start")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("label confidence must be in [0, 1]")
        if not self.author.strip():
            raise ValueError("label author is required")
