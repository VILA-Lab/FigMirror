"""adjustments_to_prose — translate structured rcParams adjustments
into natural-language fragments suitable for an agent prompt.

Per design.md §D8. The frontend's "direct-control" steppers POST
``adjustments: {param: value}`` to ``/api/refine``; the server-side
runner combines this with the user's prose ``message`` (if any) into
a single natural-language message that goes to the agent. The agent
only ever sees prose — it has one input modality, not two.

Examples
--------

>>> to_prose({"font.size": 15}, message=None)
'Adjust: font.size = 15.'

>>> to_prose({"font.size": 15, "axes.labelsize": 14}, message=None)
'Adjust: axes.labelsize = 14, font.size = 15.'

>>> to_prose({"font.size": 15}, message="blue please")
'Adjust: font.size = 15. blue please'

>>> to_prose({}, message="just blue")
'just blue'

>>> to_prose(None, message="just blue")
'just blue'

>>> to_prose({}, message=None)
''
"""

from __future__ import annotations

from typing import Optional


def _format_value(value: object) -> str:
    """Render an rcParam value tightly for prose.

    Integers stay as integers (no trailing ``.0``). Floats keep their
    natural representation. Strings get wrapped in double quotes so
    e.g. palette names round-trip unambiguously.
    """
    if isinstance(value, bool):
        # bool is a subclass of int; check first.
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return f'"{value}"'
    # Lists / dicts / None / etc: fall through to repr.
    return repr(value)


def to_prose(adjustments: Optional[dict], message: Optional[str]) -> str:
    """Combine structured adjustments + free-text message into one prose
    string to feed the agent as the turn's user message.

    Behavior
    --------

    - If ``adjustments`` is empty/None: return ``message`` (or empty
      string if message is also missing).
    - If ``adjustments`` is non-empty:
        - Build ``Adjust: key1 = v1, key2 = v2.`` (keys sorted for
          determinism — repeated calls with the same dict produce
          identical output).
        - Append ``message`` after a space if message is non-empty.
    - Always returns a stripped string.

    The output is deterministic for a given ``(adjustments, message)``
    pair so the runner's prompt construction stays testable.
    """
    msg = (message or "").strip()
    adj = adjustments or {}

    if not adj:
        return msg

    # Sort by key for determinism.
    pairs = ", ".join(
        f"{k} = {_format_value(adj[k])}"
        for k in sorted(adj.keys())
    )
    prose = f"Adjust: {pairs}."
    if msg:
        return f"{prose} {msg}"
    return prose
