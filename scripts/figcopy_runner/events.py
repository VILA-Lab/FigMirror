"""SessionEvent — normalized event union emitted by runners.

Per design.md §D5, both `CodexRunner` and `ClaudeRunner` translate
their CLI's stream-json output into this shared schema at the
subprocess-stdout boundary. The frontend writes one event handler per
event ``type``, not two per backend; the per-backend axis is fully
contained in the runners' parsers.

Stdlib-only (``dataclasses`` + ``typing.Literal``); no Pydantic.

Sequence numbers
----------------

Every event carries a monotonically-increasing ``seq: int`` scoped to
its session key (``(workdir, "iter" | "refine:<set_id>")``). The
``EventBus`` (event_bus.py) assigns the seq at ``publish`` time, so
runners construct events with ``seq=0`` and trust the bus to overwrite
it — the bus is the single point of truth for ordering. (We make ``seq``
mutable by relying on the dataclasses being plain non-frozen instances.)

Backwards-compat
----------------

To add a new event type later, append a new dataclass + add it to the
``SessionEvent`` Union; older subscribers that switch on ``event.type``
will ignore unknown types (their ``elif`` chain falls through), which
is what we want — adding a new event MUST NOT break old clients.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Union


# ─────────────────────────── text streaming ──────────────────────────


@dataclass
class TextEvent:
    """Assistant text delta. Multiple per turn, streaming."""
    type: Literal["text"] = "text"
    seq: int = 0
    data: dict = field(default_factory=dict)
    # data shape: {"text": str, "is_partial": bool}


# ─────────────────────────── tool calls ──────────────────────────────


@dataclass
class ToolCallStartEvent:
    type: Literal["tool_call_start"] = "tool_call_start"
    seq: int = 0
    data: dict = field(default_factory=dict)
    # data shape: {"call_id": str, "name": str, "args": dict}


@dataclass
class ToolCallEndEvent:
    type: Literal["tool_call_end"] = "tool_call_end"
    seq: int = 0
    data: dict = field(default_factory=dict)
    # data shape: {"call_id": str, "ok": bool, "result": str | None,
    #              "error": str | None}


# ─────────────────────────── turn lifecycle ──────────────────────────


@dataclass
class TurnStartEvent:
    type: Literal["turn_start"] = "turn_start"
    seq: int = 0
    data: dict = field(default_factory=dict)
    # data shape: {"set_id": str | None, "turn_index": int}


@dataclass
class TurnEndEvent:
    type: Literal["turn_end"] = "turn_end"
    seq: int = 0
    data: dict = field(default_factory=dict)
    # data shape: {"status": "completed" | "failed" | "cancelled",
    #              "set_id": str | None}


# ─────────────────────────── figcopy-specific ────────────────────────


@dataclass
class IterCompleteEvent:
    """Step-1: an iter (drawer/reviewer cycle) just completed."""
    type: Literal["iter_complete"] = "iter_complete"
    seq: int = 0
    data: dict = field(default_factory=dict)
    # data shape: {"iter": int, "img_url": str, "pdf_url": str | None}


@dataclass
class RefineCompleteEvent:
    """Step-2: the agent finished a refine turn (png+json landed)."""
    type: Literal["refine_complete"] = "refine_complete"
    seq: int = 0
    data: dict = field(default_factory=dict)
    # data shape: {"set_id": str, "image_url": str, "rcparams_delta":
    #              dict, "review": str, "refine_idx": int}


# ─────────────────────────── union ───────────────────────────────────

SessionEvent = Union[
    TextEvent,
    ToolCallStartEvent,
    ToolCallEndEvent,
    TurnStartEvent,
    TurnEndEvent,
    IterCompleteEvent,
    RefineCompleteEvent,
]


# Mapping from `type` string to dataclass, useful for client-side
# rehydration / golden-string tests.
EVENT_TYPES: dict[str, type] = {
    "text": TextEvent,
    "tool_call_start": ToolCallStartEvent,
    "tool_call_end": ToolCallEndEvent,
    "turn_start": TurnStartEvent,
    "turn_end": TurnEndEvent,
    "iter_complete": IterCompleteEvent,
    "refine_complete": RefineCompleteEvent,
}


def event_to_dict(event: SessionEvent) -> dict:
    """Serialize a SessionEvent to a plain dict (for JSON / SSE).

    Returns ``{"type": str, "seq": int, "data": dict}``. The frontend
    deserializes the same shape.
    """
    return {"type": event.type, "seq": event.seq, "data": dict(event.data)}
