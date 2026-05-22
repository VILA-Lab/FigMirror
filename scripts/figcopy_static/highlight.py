"""Server-side Python syntax highlighter — minimal, dep-free.

We only emit one language (Python), so a 50KB highlight.js download is
overkill. This module is ~80 lines: tokenize → classify → wrap each
classified token in ``<span class="py-X">``. CSS for the classes lives
in ``style.css``.

Public API:

    highlight_python(source: str) -> str

Returns an HTML fragment of the form ``<pre class="py"><code>...</code></pre>``.
On tokenize errors (malformed source) returns the same wrapper with
plain-escaped content so the modal still shows something readable.

Token mapping:
- KEYWORD (def / class / if / ...)            → .py-kw
- BUILTIN names + None / True / False / self  → .py-builtin
- STRING / FSTRING_*                          → .py-str
- NUMBER                                      → .py-num
- COMMENT                                     → .py-comment
- @ as the decorator marker                   → .py-decorator
- Everything else (NAME, OP, INDENT, NEWLINE) → unstyled
"""

from __future__ import annotations

import io
import keyword
import tokenize
from html import escape

_BUILTINS = frozenset({
    # Most-frequent built-ins. Intentionally small — we want the
    # highlighter to surface keywords + literals without overpainting
    # every plain function call.
    "abs", "all", "any", "bool", "bytes", "callable", "dict",
    "enumerate", "filter", "float", "frozenset", "getattr", "hasattr",
    "id", "int", "isinstance", "issubclass", "iter", "len", "list",
    "map", "max", "min", "next", "object", "open", "ord", "print",
    "range", "repr", "reversed", "round", "set", "setattr", "sorted",
    "str", "sum", "super", "tuple", "type", "zip",
    # Common identifiers we treat like keywords for visual purposes.
    "None", "True", "False", "self", "cls",
})

# Tokens whose `string` should NOT be wrapped (whitespace-only tokens
# that have no visual presence we want to highlight).
_SKIP_TYPES = {tokenize.NEWLINE, tokenize.NL, tokenize.INDENT,
               tokenize.DEDENT, tokenize.ENCODING, tokenize.ENDMARKER}


def _classify(tok: tokenize.TokenInfo) -> str | None:
    """Return CSS class for a token, or None for unstyled."""
    t = tok.type
    s = tok.string
    if t == tokenize.STRING:
        return "py-str"
    # FSTRING_* exists in 3.12+; fall back gracefully on older Pythons.
    if hasattr(tokenize, "FSTRING_START") and t in (
        getattr(tokenize, "FSTRING_START"),
        getattr(tokenize, "FSTRING_MIDDLE"),
        getattr(tokenize, "FSTRING_END"),
    ):
        return "py-str"
    if t == tokenize.NUMBER:
        return "py-num"
    if t == tokenize.COMMENT:
        return "py-comment"
    if t == tokenize.NAME:
        if s in keyword.kwlist:
            return "py-kw"
        # `match` / `case` are soft keywords in 3.10+; surface them.
        if s in ("match", "case"):
            return "py-kw"
        if s in _BUILTINS:
            return "py-builtin"
        return None
    if t == tokenize.OP and s == "@":
        return "py-decorator"
    return None


def highlight_python(source: str) -> str:
    """Highlight Python source. See module docstring for shape contract."""
    # Build absolute-offset spans so we can interleave raw whitespace
    # between tokens without losing it (tokenize doesn't emit whitespace
    # tokens for us to wrap).
    spans: list[tuple[int, int, str]] = []

    # Pre-compute line-start offsets for (row, col) → absolute offset.
    line_starts = [0]
    for line in source.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(line))

    def offset(row: int, col: int) -> int:
        # tokenize uses 1-indexed rows; row may be one past end.
        if row - 1 >= len(line_starts):
            return len(source)
        return line_starts[row - 1] + col

    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type in _SKIP_TYPES:
                continue
            cls = _classify(tok)
            if not cls:
                continue
            start = offset(tok.start[0], tok.start[1])
            end = offset(tok.end[0], tok.end[1])
            if end > start:
                spans.append((start, end, cls))
    except (tokenize.TokenizeError, IndentationError, SyntaxError):
        # Malformed source — fall back to plain text inside the same
        # wrapper so the modal still renders something useful.
        return f'<pre class="py py-fallback"><code>{escape(source)}</code></pre>'

    spans.sort()  # stable; tokenize already returns sequentially
    out: list[str] = []
    cursor = 0
    for start, end, cls in spans:
        if start < cursor:
            # Nested / overlapping spans shouldn't happen with tokenize,
            # but if they ever do, bail to plain output to avoid corrupted
            # HTML.
            return f'<pre class="py py-fallback"><code>{escape(source)}</code></pre>'
        if start > cursor:
            out.append(escape(source[cursor:start]))
        out.append(f'<span class="{cls}">{escape(source[start:end])}</span>')
        cursor = end
    if cursor < len(source):
        out.append(escape(source[cursor:]))

    return f'<pre class="py"><code>{"".join(out)}</code></pre>'
