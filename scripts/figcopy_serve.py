#!/usr/bin/env python3
"""FigMirror serve — render the reference-driven figure loop's workdir as a static
HTML page so the user can browse all iters at once instead of waiting for the
final.

Usage:
    python3 scripts/figcopy_serve.py <workdir>                  # local server :8765, auto-refresh while loop runs
    python3 scripts/figcopy_serve.py <workdir> --no-serve       # just write HTML
    python3 scripts/figcopy_serve.py <workdir> --port 8765
    python3 scripts/figcopy_serve.py --workspace <dir>          # multi-run web UI; can stage new runs
    python3 scripts/figcopy_serve.py <workdir> --upload         # push to HF Space, return shareable URL
    python3 scripts/figcopy_serve.py <workdir> --upload --space user/repo
    python3 scripts/figcopy_serve.py <workdir> --no-watch       # disable live refresh + lightbox

Workdir layout it expects (per .codex/skills/figmirror/references/
drawer.md & orchestrator-codex.md):

    <workdir>/
        inputs/
            reference_raw.png          ← user-supplied
            reference_clean.png        ← Stage-0 cleaned crop
            data.txt                   ← user-supplied
        figure_iter0.py
        img_iter0.png
        notes_iter0.md
        audit_iter0.json
        ...
        figure.png                     ← final
        figure.pdf                     ← final
        selection.md                   ← which iter was chosen

Missing artifacts are tolerated — the page just shows what's there. Single-run
mode is view-only except for writing a sibling figcopy_serve.html file in
`--no-serve` / startup render paths. Workspace mode can stage new run
directories via POST /api/run.

Zero-dependency: stdlib only (no jinja, no flask). Single file.
"""
from __future__ import annotations

import argparse
import http.server
import json
import re
import shutil
import socketserver
import sys
import webbrowser
from collections.abc import Callable
from html import escape
from pathlib import Path
from urllib.parse import quote, unquote

# Status sidecar reader. Imported from the runner package's interface
# module directly (rather than through `figcopy_runner.__init__`) so
# single-run mode doesn't pay the cost of importing MockRunner /
# PIL — it only needs the lightweight reader.
from figcopy_runner.interface import (
    TERMINAL_RUN_STATUSES,
    read_status_sidecar,
    reviewer_protocol_failure_reason,
)

PAGE_NAME = "figcopy_serve.html"
DEFAULT_HOST = "127.0.0.1"
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MIN_MAX_ITERS = 1
MAX_MAX_ITERS = 20
KNOWN_BACKENDS = ("codex", "claude", "mock")

# Run statuses where the persisted disk state is the source of truth and
# the live runner-availability override must NOT touch `status` / `reason`.
# A run that already shipped, failed, or was cancelled has terminal disk
# state — its figure / partial outputs / authored failure reason are still
# valid even if the backend CLI is later uninstalled. Browsing the existing
# output does not need the backend; only runner-dependent actions
# (refine, cancel, restart) should be blocked. Without this guard,
# `runner_for` raising `BackendUnavailable` clobbers a successful
# "shipped" pill into a misleading red "failed" with the wrong reason.
#
# This invariant is shared with the runner package: ``chat_log``
# helpers refuse to mutate ``chat.jsonl`` when the run is in any of
# these states. Promoted to ``figcopy_runner.interface`` (as
# ``TERMINAL_RUN_STATUSES``) and re-aliased here so existing in-module
# callers keep working without churn.
_TERMINAL_RUN_STATUSES = TERMINAL_RUN_STATUSES

# Where the shipped CSS / JS for the webui lives. Stack-reorg moved
# inline f-string CSS / JS out of this file into static assets, served at
# /static-ui/<file>.
STATIC_UI_DIR = Path(__file__).resolve().parent / "figcopy_static"


class BackendUnavailable(RuntimeError):
    """Requested runner backend is not available in this server process."""


def _backend_runtime_available(
    name: str, *, which=None
) -> bool:
    """Return True if backend ``name``'s subprocess launch path is
    satisfiable on this host.

    - ``mock``: always True (in-process, no external CLI).
    - ``codex`` / ``claude``: requires BOTH the agent CLI AND ``uv``,
      because ``CodexRunner`` / ``ClaudeRunner`` wrap every subprocess
      launch with ``uv run --project <repo>`` (see ``_uv_cmd``). If
      ``uv`` is missing the first Stage-0 ``Popen`` would raise
      ``FileNotFoundError`` AFTER ``status.json`` flipped to
      ``running``, leaving the UI permanently spinning — so we must
      refuse to advertise these backends in the availability probe.
    - Unknown backends: False.

    The injected ``which`` is for tests; production callers leave it
    as ``None`` and we fall back to ``shutil.which``.
    """
    if which is None:
        import shutil as _shutil
        which = _shutil.which
    if name == "mock":
        return True
    if name in ("codex", "claude"):
        return which(name) is not None and which("uv") is not None
    return False


def should_replay_sse(*, replay_query: bool, last_event_id: int) -> bool:
    """Decide whether an SSE handler should send the historical replay pass.

    Two inputs combine into one decision:

    - ``replay_query`` — the parsed ``?replay=`` query param. ``True`` (the
      default) means the caller wants the standard initial replay; ``False``
      means the caller hydrated chat from REST and is opting OUT of
      re-streaming the historical events.
    - ``last_event_id`` — the integer ``Last-Event-ID`` header the
      EventSource sends on a reconnect. ``0`` (or absent) means
      "first-time connection, no resume cursor". Any positive value means
      "I was previously streaming and saw events up through this seq —
      resume from here."

    Contract (PR #25 round-1 finding #5): a non-zero ``Last-Event-ID``
    is a caller-supplied resume cursor that overrides ``?replay=0``. A
    browser auto-reconnect after a transient drop reuses the original
    request URL (so ``replay=0`` sticks), but sends the
    ``Last-Event-ID`` header — without this override, every event
    published while the connection was down would be silently lost.
    The post-hydration optimization only applies on the INITIAL
    connection where ``Last-Event-ID`` is absent.

    Pure function so it is unit-testable without standing up an HTTP
    server. Callers are responsible for parsing the query string and
    the header into the typed inputs.
    """
    return replay_query or last_event_id > 0

class _ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


class ThreadingReusableTCPServer(socketserver.ThreadingMixIn, _ReusableTCPServer):
    """TCP server with SO_REUSEADDR + per-request threading.

    Threading matters because the trajectory page issues a GET for the HTML,
    a follow-up GET for `_state.json` every ~3s, and additional GETs for
    each iter image — a single-threaded server would serialize these and
    stall the live-refresh loop while a large PNG is being read.
    """
    daemon_threads = True


def parse_audit(path: Path) -> dict:
    """Parse a reviewer audit JSON. Tolerates malformed input."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"_parse_error": path.read_text()[:500]}


def render_iter_pdf(workdir: Path, iter_n: int, *,
                    timeout_s: float = 60.0) -> Path:
    """Generate ``img_iter<N>.pdf`` as a TRUE vector PDF.

    The agent's iter scripts already contain dual PNG+PDF output
    logic — but the branch that emits ``figure.pdf`` only triggers
    when ``Path(__file__).name == 'figure.py'``. As-named
    ``figure_iter<N>.py`` only writes PNG.

    Easiest fix: copy the iter script to a temp dir as ``figure.py``
    and run it. The agent's own ``output_paths()`` often matches that
    name and emits both ``figure.png`` and ``figure.pdf``; for scripts
    that only save PNGs, a tiny wrapper falls back to saving the live
    matplotlib ``fig`` object as ``figure.pdf``.

    This avoids monkeypatching matplotlib and uses the exact same
    savefig path the agent uses for the shipped figure. (Why not
    just prompt the agent to write per-iter PDFs from the start?
    That's a separate change to the skill — done independently;
    this helper handles legacy / mid-loop renders correctly without
    needing the agent to re-run.)

    Cache: if ``img_iter<N>.pdf`` exists and is newer than the
    ``.py``, return it without re-rendering. Raises
    ``FileNotFoundError`` if the source ``.py`` is missing;
    ``RuntimeError`` with a stderr excerpt on render failure.
    """
    workdir = workdir.resolve()
    pdf_path = workdir / f"img_iter{iter_n}.pdf"
    py_path = workdir / f"figure_iter{iter_n}.py"

    if pdf_path.exists() and py_path.exists():
        try:
            if pdf_path.stat().st_mtime >= py_path.stat().st_mtime:
                return pdf_path
        except OSError:
            pass

    if not py_path.is_file():
        raise FileNotFoundError(
            f"figure_iter{iter_n}.py not found in {workdir}"
        )

    import subprocess as _sp
    import tempfile as _tf
    import shutil as _shutil

    with _tf.TemporaryDirectory(prefix="figcopy-pdf-") as td:
        td_path = Path(td)
        figure_py = td_path / "figure.py"
        runner_py = td_path / "_render_pdf_wrapper.py"
        _shutil.copyfile(py_path, figure_py)
        runner_py.write_text(
            """
from pathlib import Path
import runpy

source = Path("figure.py")
try:
    ns = runpy.run_path(str(source), run_name="__main__")
except SystemExit as exc:
    code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    if code:
        raise
    ns = {}

pdf = Path("figure.pdf")
if not pdf.is_file():
    import matplotlib
    matplotlib.rcParams["pdf.fonttype"] = 42
    import matplotlib.pyplot as plt

    fig = ns.get("fig") if isinstance(ns, dict) else None
    if fig is None:
        fig = plt.gcf()
    fig.savefig(pdf, format="pdf", bbox_inches=None)
""".lstrip(),
            encoding="utf-8",
        )
        # The agent's scripts are self-contained (data baked in as
        # Python literals), so the temp dir doesn't need any of the
        # workdir's input files. We just need matplotlib + numpy in
        # the venv — which the dev group already provides.
        proc = _sp.run(
            [sys.executable, str(runner_py)],
            cwd=str(td_path),
            capture_output=True,
            timeout=timeout_s,
        )
        produced_pdf = td_path / "figure.pdf"
        if not produced_pdf.is_file():
            err = proc.stderr.decode("utf-8", errors="replace")[:1200]
            raise RuntimeError(
                f"running figure_iter{iter_n}.py as figure.py did not "
                f"emit figure.pdf (exit {proc.returncode}). stderr "
                f"tail:\n{err}"
            )
        # Atomic move into the workdir under the iter-numbered name.
        tmp_target = pdf_path.with_name(pdf_path.name + ".tmp")
        _shutil.copyfile(produced_pdf, tmp_target)
        tmp_target.replace(pdf_path)
        return pdf_path


def discover_iters(workdir: Path) -> list[int]:
    """Find all iter numbers that have at least one of the expected artifacts."""
    seen: set[int] = set()
    for f in workdir.iterdir():
        for pat in (
            r"^img_iter(\d+)\.png$",
            r"^figure_iter(\d+)\.py$",
            r"^notes_iter(\d+)\.md$",
            r"^audit_iter(\d+)\.json$",
        ):
            m = re.match(pat, f.name)
            if m:
                seen.add(int(m.group(1)))
    return sorted(seen)


def build_run_state(
    workdir: Path,
    *,
    runner_for: Callable[[Path], object] | None = None,
) -> dict:
    """Per-run state polled by trajectory.js every 3s.

    Includes runner status (``running`` / ``shipped`` / ``failed`` /
    ``idle``) + ``current_iter`` so the trajectory page can render a
    live progress banner. Status is sourced from
    the workdir's ``status.json`` sidecar when present; otherwise
    inferred from disk (figure.png exists → shipped, else idle).

    When ``runner_for`` is supplied (workspace mode wires the closure
    through), the run is also resolved against the active backend
    table. A run staged for a backend whose CLI is missing on this
    host (``BackendUnavailable`` raised by ``runner_for``) is reported
    as ``failed`` with the exception's text in ``reason`` — without
    this, such runs render as a permanently-spinning card with no
    explanation, since every POST against them 503s on the same
    exception.
    """
    iters = discover_iters(workdir)
    status, current_iter, _thumb, reason = _run_state(workdir, iters)
    if runner_for is not None and status not in _TERMINAL_RUN_STATUSES:
        # Only annotate non-terminal runs. A `shipped` / `failed` /
        # `cancelled` run has its outcome on disk already; browsing it
        # doesn't need the backend, so an unavailable CLI must not
        # clobber the disk-derived status or the authored reason.
        try:
            runner_for(workdir)
        except BackendUnavailable as e:
            status = "failed"
            reason = str(e)
    return {
        "n_iters": len(iters),
        "final": (workdir / "figure.png").exists(),
        "selection": (workdir / "selection.md").exists(),
        "status": status,
        "current_iter": current_iter,
        "reason": reason,
        "iters": [
            {"i": i,
             "img": (workdir / f"img_iter{i}.png").exists(),
             "audit": (workdir / f"audit_iter{i}.json").exists()}
            for i in iters
        ],
    }


def path_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def direct_child_dir(path: Path, root: Path) -> bool:
    return path.parent == root and path.is_dir()


def _resolve_run(handler, workspace: Path, name: str) -> Path | None:
    """Resolve ``<workspace>/<name>`` if it's a direct child dir of the
    workspace. Sends a 404 to the client and returns ``None`` on miss;
    otherwise returns the resolved Path.

    Centralizes the path-traversal + direct-child guard repeated across
    every run-scoped route (`/r/<name>`, `/r/<name>/_state.json`,
    `/api/runs/<name>/code/...`, `/api/runs/<name>/pdf`, `/api/refine`,
    `/static/<name>/...`). 404 (not 403) on miss so we don't leak which
    names exist outside the workspace.
    """
    run_dir = (workspace / name).resolve()
    if not (path_inside(run_dir, workspace) and direct_child_dir(run_dir, workspace)):
        handler.send_error(404, f"run not found: {name}")
        return None
    return run_dir


_STATIC_UI_ALLOWED_EXT = {
    ".css", ".js", ".mjs", ".map",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
    ".woff", ".woff2", ".ttf", ".otf",
    ".json",  # if we ever ship a manifest
}


def _serve_static_ui(handler, route: str) -> bool:
    """If route matches `/static-ui/<file>`, serve the file from STATIC_UI_DIR.

    Returns True if the request was handled (response sent), False otherwise.
    Mirrors the path-traversal guard pattern used by `/static/<run>/<file>`.

    Filters by extension allowlist so .py files (this package's own
    ``__init__.py`` / ``highlight.py`` / ``mock_iters/*/code.py``) are
    never reachable via HTTP — those exist only for in-process import or
    runner-internal copy.
    """
    prefix = "/static-ui/"
    if not route.startswith(prefix):
        return False
    rel = unquote(route[len(prefix):])
    fp = (STATIC_UI_DIR / rel).resolve()
    if not path_inside(fp, STATIC_UI_DIR):
        handler.send_error(403)
        return True
    if fp.suffix.lower() not in _STATIC_UI_ALLOWED_EXT:
        # 404 (not 403) so we don't leak which files exist.
        handler.send_error(404)
        return True
    if not fp.is_file():
        handler.send_error(404)
        return True
    ctype = handler.guess_type(str(fp))
    data = fp.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", ctype)
    # no-store keeps dev iteration friction-free — page reload picks up edits
    # to .css / .js immediately. Adjust if/when this becomes a hot path.
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)
    return True


def relpath(p: Path, base: Path) -> str:
    try:
        return str(p.relative_to(base))
    except ValueError:
        return str(p)


def _pill(cls: str, text: str, *, title: str | None = None) -> str:
    """Render a status pill. `text` is escaped; `cls` is trusted (caller-controlled)."""
    title_attr = f' title="{escape(title)}"' if title else ''
    return f'<span class="pill {cls}"{title_attr}>{escape(text)}</span>'


def _details(summary_html: str, body_html: str, *, open: bool = False) -> str:
    """Render a <details> block. Both summary_html and body_html are HTML;
    callers are responsible for escaping any user-controlled substrings before
    composing them into these fragments."""
    open_attr = " open" if open else ""
    return f"<details{open_attr}><summary>{summary_html}</summary>{body_html}</details>"


# Reviewer-verdict → pill CSS class. Used by fmt_audit_pill (header
# row) and _verdict_class (compact thumbs). One source of truth so the
# two stay in sync if a new verdict word is added.
_VERDICT_PILL_CLASS = {"ship": "good", "close": "neutral", "off": "bad"}


def fmt_audit_pill(audit: dict) -> str:
    """Compact 1-line summary for the iter card header."""
    if not audit:
        # No audit on disk — could be: (a) reviewer not run yet (Stage 1
        # still in progress), or (b) codex exited early before reviewer
        # got to this iter. Neither is a "bad" state for the user; show
        # a muted "pending" pill instead of the alarming red one.
        return _pill("mute", "no audit yet")
    if audit.get("_parse_error"):
        return _pill("bad", "audit unreadable")
    fid = audit.get("fidelity", {}) if isinstance(audit, dict) else {}
    qf = audit.get("quality_floor", {}) if isinstance(audit, dict) else {}
    verdict = fid.get("verdict", "?")
    floor_ok = bool(qf.get("passed"))
    themes = audit.get("focus_themes") or []
    themes_str = ", ".join(t if isinstance(t, str) else t.get("name", "") for t in themes[:3])
    verdict_color = _VERDICT_PILL_CLASS.get(verdict, "neutral")
    parts_ = [
        _pill(verdict_color, verdict),
        _pill("good" if floor_ok else "bad", f"floor {'✓' if floor_ok else '✕'}"),
    ]
    if themes_str:
        parts_.append(_pill("mute", themes_str, title="focus themes"))
    return "".join(parts_)


def _img_url(p: Path, workdir: Path, url_prefix: str) -> str:
    """Build the URL we put in <img src='…'>.

    - In single-workdir mode the server's CWD is workdir.parent and url_prefix
      is empty, so we emit '<workdir-name>/<rel>' (relative URL).
    - In workspace mode the handler routes /static/<name>/<rest>, so we emit
      '<url_prefix><rel>' as an absolute URL.

    Each path component is URL-encoded so filenames containing characters
    that are unsafe in URLs (or that would break our HTML attribute / inline
    JS literal — e.g. a single quote) round-trip correctly. The caller is
    responsible for passing an already-encoded `url_prefix`.
    """
    if p.parent == workdir:
        rel = quote(p.name, safe="")
    else:
        rel = f"{quote(p.parent.name, safe='')}/{quote(p.name, safe='')}"
    if url_prefix:
        return f"{url_prefix}{rel}"
    return f"{quote(workdir.name, safe='')}/{rel}"


# Audit anchors come in as strings like "[L1] palette ...". Parse the
# bracket prefix into a structured badge instead of leaving it as raw
# text in the bullet — the badge style makes the L1/L2 hierarchy
# visible at a glance.
_ANCHOR_LEVEL_RE = re.compile(r"^\s*\[L(\d+)\]\s*(.*)$", re.DOTALL)


def _render_anchor_li(text: str) -> str:
    """Render one anchor / focus-theme bullet. If the text starts with
    ``[L<n>]``, emit a chip-styled level badge + the remainder; else
    emit the text unadorned. Caller passes already-stringified input."""
    m = _ANCHOR_LEVEL_RE.match(text)
    if not m:
        return f"<li>{escape(text)}</li>"
    level, rest = m.group(1), m.group(2)
    return (
        f"<li class='has-anchor-tag'>"
        f"<span class='anchor-tag tag-l{escape(level)}' "
        f"aria-label='level {escape(level)} anchor'>L{escape(level)}</span>"
        f"<span class='anchor-text'>{escape(rest)}</span>"
        f"</li>"
    )


def _verdict_class(audit: dict) -> str:
    """Map audit verdict → pill CSS class for the thumb sticker."""
    if not audit:
        # No audit yet — muted "pending" pill, not a "bad" pill.
        return "mute"
    if audit.get("_parse_error"):
        return "bad"
    verdict = audit.get("fidelity", {}).get("verdict", "?")
    return _VERDICT_PILL_CLASS.get(verdict, "neutral")


def _render_iter_thumb(workdir: Path, n: int, *, selected: bool,
                       url_prefix: str) -> str:
    """Render one iter as a compact button in the horizontal strip.

    The strip's mousemove handler scales each thumb via a CSS variable
    (--scale) so the cursor "magnifies" nearby thumbs Dock-style. Click
    sets the URL hash to ``#iter-N`` which JS observes to expand the
    matching ``.iter-expanded`` panel below the strip.
    """
    img_path = workdir / f"img_iter{n}.png"
    audit = parse_audit(workdir / f"audit_iter{n}.json")
    if audit:
        verdict = audit.get("fidelity", {}).get("verdict", "?")
    else:
        # No audit yet — render as "—" instead of "?" (a question mark
        # reads as "something is wrong"; an em-dash reads as "pending").
        verdict = "—"
    classes = ["iter-thumb"]
    if selected:
        classes.append("is-selected")
    aria = f"iter {n}, verdict {verdict}"
    img_html = (
        f"<img src='{_img_url(img_path, workdir, url_prefix)}' "
        f"alt='' loading='lazy'>"
        if img_path.exists()
        else "<span class='iter-thumb-empty' aria-hidden='true'></span>"
    )
    sel_badge = (
        "<span class='iter-thumb-selected' aria-label='selected'>★</span>"
        if selected else ""
    )
    # aria-selected + aria-controls complete the tablist contract; JS
    # syncs aria-selected on hash changes so screen-reader users track
    # which iter's panel is currently expanded. Initial value matches
    # the panel that's unhidden on first paint (none, until JS runs
    # syncFromHash() against location.hash).
    return (
        f"<button type='button' class='{' '.join(classes)}' "
        f"role='tab' aria-selected='false' "
        f"aria-controls='iter-{n}-panel' "
        f"data-iter='{n}' aria-label='{escape(aria)}'>"
        f"<span class='iter-thumb-frame'>{img_html}</span>"
        f"<span class='iter-thumb-meta'>"
        f"<span class='iter-thumb-num'>{n}</span>"
        f"<span class='pill {_verdict_class(audit)} iter-thumb-pill'>"
        f"{escape(verdict)}</span>"
        f"</span>"
        f"{sel_badge}"
        f"</button>"
    )


def _render_iter_expanded(workdir: Path, n: int, *, selected: bool,
                          url_prefix: str,
                          run_status: str | None = None,
                          start_hidden: bool = True) -> str:
    """Render the detail view for one iter; one of these panels is
    visible at a time (hash-routed). Pre-rendered for all iters and
    hidden by default — the max_iters cap (20) means the total HTML
    stays manageable.

    Per-iter PDF download URL is built inline from ``url_prefix`` +
    iter index — different per iter, so no point hoisting (was
    previously a `pdf_url` kwarg back when only the shipped
    figure.pdf was offered).
    """
    img_path = workdir / f"img_iter{n}.png"
    ref_path = workdir / "inputs" / "reference_clean.png"
    notes_path = workdir / f"notes_iter{n}.md"
    code_path = workdir / f"figure_iter{n}.py"
    audit_path = workdir / f"audit_iter{n}.json"
    audit = parse_audit(audit_path)

    classes = "iter-expanded"
    if selected:
        classes += " is-selected"
    sel_badge = '<span class="sel-badge">selected</span>' if selected else ''

    hidden_attr = " hidden" if start_hidden else ""
    parts = [
        f'<section class="{classes}" data-iter="{n}" '
        f'id="iter-{n}-panel"{hidden_attr}>',
        f'<header class="iter-head">'
        f'<h2>Iter {n}{sel_badge}</h2>'
        f'<div class="pills">{fmt_audit_pill(audit)}</div>'
        f'</header>',
        # Two-column expanded layout: image left, metadata + actions right.
        '<div class="iter-expanded-grid">',
        '<div class="iter-expanded-image">',
    ]
    if img_path.exists():
        parts.append(
            f"<figure><img src='{_img_url(img_path, workdir, url_prefix)}' "
            f"alt='iter {n} render'>"
            f"<figcaption>Generated · iter {n}</figcaption></figure>"
        )
    elif run_status == "running":
        parts.append(
            "<div class='iter-render-pending'>"
            "<span class='iter-render-pending-spinner' aria-hidden='true'></span>"
            "<span class='iter-render-pending-copy'>"
            f"Rendering img_iter{n}.png · floor self-check in progress"
            "</span>"
            "</div>"
        )
    else:
        parts.append(f"<div class='missing'>img_iter{n}.png missing</div>")
    if ref_path.exists():
        parts.append(
            f"<figure class='iter-expanded-ref'>"
            f"<img src='{_img_url(ref_path, workdir, url_prefix)}' "
            f"alt='reference'>"
            f"<figcaption>Reference</figcaption></figure>"
        )
    parts.append('</div>')  # /.iter-expanded-image
    # right column: actions + audit metadata
    parts.append('<aside class="iter-expanded-aside">')
    # Actions row. "Select as Template" routes to Step 2 anchored on
    # this iter (workspace mode only — single-run mode has no
    # /api/refine to back Step 2 against).
    actions: list[str] = []
    if url_prefix:
        actions.append(
            f"<a class='iter-action iter-action-primary' "
            f"href='?step=2&amp;template={n}'>"
            f"<span aria-hidden='true'>★</span> "
            f"Select as template</a>"
        )
    # Export code only renders in workspace mode — the modal fetches
    # /api/runs/<name>/code/<iter>, which the single-run handler
    # doesn't expose. Without this gate the button silently no-ops.
    if code_path.exists() and url_prefix:
        actions.append(
            f"<button type='button' class='iter-action' "
            f"data-action='code' data-iter='{n}'>Export code</button>"
        )
    # Per-iter Export PDF — every iter that has an image gets a PDF
    # download converted on-demand from img_iter<N>.png. Lets users
    # ship any iter from the expanded view without waiting for the
    # final-shipped state. Workspace-mode-only because the conversion
    # endpoint /api/runs/<name>/iter/<N>/pdf doesn't exist in single-
    # run mode.
    if url_prefix and img_path.exists():
        iter_pdf_url = (
            f"/api/runs/{quote(workdir.name, safe='')}"
            f"/iter/{n}/pdf"
        )
        actions.append(
            f"<a class='iter-action' "
            f"href='{escape(iter_pdf_url)}' download>Export PDF</a>"
        )
    if actions:
        parts.append(f"<div class='iter-actions'>{''.join(actions)}</div>")

    if audit:
        anchor = audit.get("anchor", {})
        if isinstance(anchor, dict):
            wir = anchor.get("what_is_right") or []
            if wir:
                items = "".join(_render_anchor_li(str(x)) for x in wir[:30])
                parts.append(_details(
                    f'What\'s right <span class="muted">· {len(wir)} anchors</span>',
                    f'<ul class="anchor-list">{items}</ul>',
                    open=True,
                ))
        themes = audit.get("focus_themes") or []
        if themes:
            theme_items = "".join(
                _render_anchor_li(t if isinstance(t, str) else t.get("name", ""))
                for t in themes[:30]
            )
            parts.append(_details(
                f'Focus themes <span class="muted">· {len(themes)}</span>',
                f'<ul class="anchor-list">{theme_items}</ul>',
            ))
    if notes_path.exists():
        parts.append(_details(
            "Drawer notes",
            f"<pre>{escape(notes_path.read_text(errors='replace'))}</pre>",
        ))
    if audit:
        parts.append(_details(
            "Full audit JSON",
            f"<pre>{escape(json.dumps(audit, indent=2))}</pre>",
        ))
    parts.append('</aside>')  # /.iter-expanded-aside
    parts.append('</div>')    # /.iter-expanded-grid
    parts.append('</section>')
    return "\n".join(parts)


def detect_selected_iter(workdir: Path) -> int | None:
    """Best-effort parse of selection.md to find the picked iter number."""
    sel = workdir / "selection.md"
    if not sel.exists():
        return None
    txt = sel.read_text(errors="replace")
    # match 'iter N' where N is the first number near 'Selected'
    m = re.search(r"[Ss]elected[^\n]*?iter\s*(\d+)", txt)
    if m:
        return int(m.group(1))
    m = re.search(r"\biter\s*(\d+)\b", txt)
    return int(m.group(1)) if m else None


def render_html(workdir: Path, interactive: bool = False,
                url_prefix: str = "",
                breadcrumb_url: str | None = None,
                step: int = 1,
                template_iter: int | None = None,
                baseline_iters: list[int] | None = None,
                inline_assets: bool = False,
                runner_for: Callable[[Path], object] | None = None) -> str:
    """Render the trajectory page.

    `interactive=True` adds a live-refresh loop, a click-to-enlarge lightbox,
    and a reference-overlay toggle for each iter image. Used in --watch mode
    (the default for `--no-serve`-not-set local server run).

    `url_prefix` is prepended to every image URL — empty in single-workdir
    mode (we use relative URLs), `/static/<name>/` in workspace mode.

    `breadcrumb_url` adds a "← Workspace" link in the page header. Set to
    "/" by the workspace handler; left None in single-run mode (where there
    is no workspace to go back to).

    ``step`` ∈ {1, 2} selects the trajectory view:

    - ``step=1`` (default): iter strip + expanded panel. The browse-and-
      pick view that surfaces every iter the loop emitted.
    - ``step=2``: chat refinement view, anchored on a single template
      iter (``template_iter``). Sends NL prompts to /api/refine; surfaces
      structured rcParams controls as the AI mentions them.

    Both views share the same header / breadcrumb / status chrome;
    Stage E adds a tab-style toggle that switches between them via
    query string (``?step=2&template=N``).

    When ``runner_for`` is supplied (workspace mode wires the closure
    through), the first-paint status is also resolved against the active
    backend table — a run staged for a backend whose CLI is missing on
    this host renders as ``failed`` with the remediation text in
    ``status-text`` immediately, instead of a stale "Waiting for first
    iter…" banner that trajectory.js can never recover from (the first
    poll returns ``failed`` but JS treats it as the polling baseline and
    stops, without updating the banner).
    """
    iters = discover_iters(workdir)
    final_png = workdir / "figure.png"
    final_pdf = workdir / "figure.pdf"
    selection = workdir / "selection.md"
    ref = workdir / "inputs" / "reference_clean.png"
    data = workdir / "inputs" / "data.txt"
    selected_iter = detect_selected_iter(workdir)

    # Surface the on-disk status as a structured data-attr so the
    # client's poll-init guard can read it (skip polling when run is
    # already in a terminal state on first paint).
    _sidecar = read_status_sidecar(workdir) or {}
    status_attr = (_sidecar.get("state")
                   or ("shipped" if final_png.exists() else "idle"))

    if final_png.exists():
        status_text = "Final figure ready"
    elif iters:
        status_text = f"{len(iters)} iter{'s' if len(iters) != 1 else ''} so far · waiting for next"
    else:
        status_text = "Waiting for first iter…"

    # Backend-unavailable override: resolve the configured backend now so
    # the first paint matches what `/r/<name>/_state.json` will return.
    # Gated on non-terminal status — a `shipped` / `failed` / `cancelled`
    # run reads its outcome from disk and the user is browsing artifacts
    # that don't depend on the backend (see `_TERMINAL_RUN_STATUSES`).
    # Without this, a directly-loaded `/r/<name>` page for a run whose
    # backend CLI is missing renders as "Waiting for first iter…", the
    # first poll returns `failed`, but trajectory.js treats the first
    # response as its polling baseline and stops — banner is never
    # updated and the user has no actionable text.
    if (
        runner_for is not None
        and status_attr not in _TERMINAL_RUN_STATUSES
    ):
        try:
            runner_for(workdir)
        except BackendUnavailable as e:
            status_attr = "failed"
            status_text = str(e)

    # CSS: when serving via the workspace handler the browser fetches
    # /static-ui/style.css; for `--no-serve` and `--upload` paths there
    # is no handler, so we inline the file contents instead. Avoids a
    # styled page silently rendering unstyled when written to disk or
    # uploaded to a static host.
    if inline_assets:
        try:
            css_text = (STATIC_UI_DIR / "style.css").read_text(encoding="utf-8")
            css_block = f"<style>{css_text}</style>"
        except OSError:
            css_block = "<link rel='stylesheet' href='/static-ui/style.css'>"
    else:
        # Cache-bust via mtime so iterative CSS edits don't get
        # overridden by browser cache. ?v=<mtime-secs> means the URL
        # changes whenever style.css is edited → forced fresh fetch
        # on next page load. Static-ui handler ignores the query.
        try:
            _css_mtime = int((STATIC_UI_DIR / "style.css").stat().st_mtime)
        except OSError:
            _css_mtime = 0
        css_block = (
            f"<link rel='stylesheet' "
            f"href='/static-ui/style.css?v={_css_mtime}'>"
        )
    # Body class drives the workbench layout. Step 2 needs a
    # viewport-height pane with independent inner scrolling (per UX
    # feedback: chat history and left rail must scroll independently;
    # the chat input box stays anchored at the bottom). Step 1
    # remains natural-flow.
    body_class = "is-step2" if step == 2 else "is-step1"
    # Render the header *compact by default*. Most page-load → page-load
    # navigations the user wants the slim chrome (more vertical space for
    # iters / chat). The fold-button JS reads localStorage and removes
    # `is-compact` if the user explicitly chose "expanded" — without a
    # CSS transition firing, so neither the typical "stay compact" case
    # nor the "they prefer expanded" case shows a slide on page load.
    parts: list[str] = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<title>{escape(workdir.name)} · FigMirror</title>",
        css_block,
        f"</head><body class='{body_class}'>",
        # lightbox
        ("<div class='lightbox' id='lb' role='dialog' aria-label='enlarged image' onclick='closeLB()'>"
         "  <img id='lb-img' alt='' onclick='event.stopPropagation()'>"
         "  <div class='controls' onclick='event.stopPropagation()'>"
         "    <span class='hint'>"
         "      <kbd>scroll</kbd> zoom &nbsp; "
         "      <kbd>drag</kbd> pan &nbsp; "
         "      <kbd>dbl-click</kbd> reset &nbsp; "
         "      <kbd>T</kbd> compare reference &nbsp; "
         "      <kbd>Esc</kbd> close"
         "    </span>"
         "    <button onclick='toggleRef()'>compare reference</button>"
         "    <button onclick='closeLB()'>close</button>"
         "  </div>"
         "</div>"),
        # Export Code modal — populated by JS on click of an iter's
        # `Export code` button. Body fetched from
        # /api/runs/<name>/code/<iter> (server-side highlighter).
        ("<div class='modal' id='code-modal' hidden role='dialog' "
         "aria-label='exported code' aria-modal='true'>"
         "  <div class='modal-overlay' data-modal-close></div>"
         "  <div class='modal-card'>"
         "    <header class='modal-head'>"
         "      <h3 class='modal-title'>Code "
         "        <span class='muted modal-title-meta' data-modal-meta></span>"
         "      </h3>"
         "      <div class='modal-actions'>"
         "        <button type='button' class='modal-copy' "
         "                data-modal-copy>Copy</button>"
         "        <button type='button' class='modal-close' "
         "                data-modal-close aria-label='close'>×</button>"
         "      </div>"
         "    </header>"
         "    <div class='modal-body' data-modal-body></div>"
         "  </div>"
         "</div>"),
        # Default state = compact (see body-class comment above).
        # `no-transition` is stripped by JS after the initial state is
        # reconciled with localStorage so the click toggle still
        # animates smoothly.
        "<header class='top is-compact no-transition'><div class='container'>",
    ]
    # "Header chrome": the navigation cluster on the LEFT. Wraps the
    # Workspace breadcrumb + the manual fold/expand button so they
    # group visually and stay inline in compact mode. The fold button
    # used to be position:absolute in the top-right; that caused it to
    # overlap the step-tabs when the header flex-collapses. Per UX
    # feedback: chrome on the LEFT, content in the middle, tabs on the
    # right — no overlap, no awkward whitespace.
    chrome_parts: list[str] = ["<div class='header-chrome'>"]
    if breadcrumb_url:
        chrome_parts.append(
            "<nav class='breadcrumb' aria-label='breadcrumb'>"
            f"<a href='{escape(breadcrumb_url)}'>"
            "<span aria-hidden='true'>←</span> Workspace"
            "</a>"
            "</nav>"
        )
    chrome_parts.append(
        "<button type='button' id='header-fold' class='header-fold' "
        "aria-pressed='false' title='Collapse header' "
        "aria-label='Toggle compact header'>"
        "<span class='header-fold-icon' aria-hidden='true'></span>"
        "</button>"
    )
    chrome_parts.append("</div>")
    parts.append("".join(chrome_parts))
    parts += [
        "<div class='brand'>FigMirror · trajectory</div>",
        f"<div class='run'>{escape(workdir.name)}</div>",
        f"<div class='run-path'>{escape(str(workdir))}</div>",
        f"<div class='status' id='status' role='status' "
        f"aria-live='polite' data-status='{escape(status_attr)}'>",
        "  <span class='dot'></span>",
        f"  <span class='status-text'>{escape(status_text)}</span>",
        "</div>",
    ]
    # Delete-run button — workspace-mode only. Lives at the right
    # edge of the header chrome so it doesn't compete visually with
    # the run-name / status; trajectory.js wires the click handler
    # which confirms, sends DELETE /api/runs/<name>, and navigates
    # back to the workspace landing on success. Single-run mode
    # (--no-workspace) has no DELETE endpoint to back this — skip.
    if url_prefix:
        parts.append(
            f"<button type='button' class='run-delete' id='run-delete-btn' "
            f"data-name='{escape(workdir.name)}' "
            f"title='Delete this run' "
            f"aria-label='Delete this run'>"
            f"Delete run</button>"
        )
    # Step 1 / Step 2 toggle. Workspace-mode only — single-run pages
    # have no /api/refine endpoint to back Step 2 against. We default
    # template= to selection's iter, falling back to the most recent.
    if url_prefix and iters:
        # Resolve a default template iter for the Step 2 link if the
        # caller didn't pass one. Selected iter > most-recent iter.
        default_template = (template_iter
                            if template_iter is not None
                            else (selected_iter
                                  if selected_iter is not None
                                  else iters[-1]))
        s1_cls = "step-tab" + (" is-active" if step != 2 else "")
        s2_cls = "step-tab" + (" is-active" if step == 2 else "")
        parts.append(
            "<nav class='step-tabs' role='tablist' aria-label='trajectory step'>"
            f"<a href='?step=1' class='{s1_cls}' role='tab' "
            f"aria-selected='{'true' if step != 2 else 'false'}'>"
            "<span class='step-tabs-num'>01</span>"
            "<span class='step-tabs-label'>Browse iterations</span>"
            "</a>"
            f"<a href='?step=2&amp;template={default_template}' class='{s2_cls}' "
            f"role='tab' aria-selected='{'true' if step == 2 else 'false'}'>"
            "<span class='step-tabs-num'>02</span>"
            "<span class='step-tabs-label'>Refine via chat</span>"
            "</a>"
            "</nav>"
        )
    parts += [
        "</div></header>",
        "<div class='container'>",
    ]

    # Inputs — collapsed by default per spec; reference image + data
    # fingerprint are useful but secondary to the iter stream.
    #
    # Step 2 (Refine via chat) intentionally HIDES this section: the
    # user is now iterating against the already-selected baseline
    # figures shown in the working panel, not re-validating their
    # original Step-1 inputs. Per user direction (2026-05-12): "这个
    # 地方的input，再stage 02 的时候就可以去掉了，没有必要还留着"
    # — keeping it would just take vertical space that the workbench
    # needs for the chat column.
    if step != 2:
        inputs_body: list[str] = []
        if ref.exists():
            inputs_body.append(
                f"<figure class='inputs-ref'>"
                f"<img src='{_img_url(ref, workdir, url_prefix)}' "
                f"alt='reference figure'>"
                f"<figcaption>Reference</figcaption></figure>"
            )
        else:
            inputs_body.append(
                "<div class='missing'>inputs/reference_clean.png missing</div>"
            )
        if data.exists():
            snippet = data.read_text(errors="replace")
            truncated = snippet[:2000] + ("..." if len(snippet) > 2000 else "")
            inputs_body.append(
                f"<div class='inputs-data'>"
                f"<div class='inputs-data-meta'>data · "
                f"{len(snippet.splitlines())} lines · {len(snippet)} chars</div>"
                f"<pre>{escape(truncated)}</pre>"
                f"</div>"
            )
        parts.append(
            f"<section class='inputs-section' id='inputs'>"
            f"{_details('Inputs', ''.join(inputs_body), open=False)}"
            f"</section>"
        )

    if step != 2:
        # ─── Step 1: iter strip + expanded panels + final ───
        # When the run is mid-loop but the first iter PNG hasn't landed
        # yet (codex is reading the skill / inspecting the reference /
        # staging python), show a spinner placeholder. Without this
        # the page looks empty / broken between submit and the first
        # iter (~2-4 min with the default agent profile). Per user direction
        # 2026-05-12: "需要及时的显示进度（点进去之前和之后都需要）".
        if not iters and status_attr == "running":
            parts.append(
                "<section class='iter-strip-pending' "
                "aria-label='rendering first iteration'>"
                "<span class='iter-strip-pending-spinner' "
                "aria-hidden='true'></span>"
                "<div class='iter-strip-pending-body'>"
                "<span class='iter-strip-pending-headline'>"
                "Rendering iter 1…</span>"
                "<span class='iter-strip-pending-hint'>"
                "The agent is reading the skill, inspecting your "
                "reference, and preparing the first plot. First iter "
                "usually lands within ~2-4 min."
                "</span></div>"
                "</section>"
            )
        # Iter strip — horizontal scroll, dock-magnification on hover.
        if iters:
            parts.append(
                "<section class='iter-strip-section' aria-label='iterations'>"
                "<header class='iter-strip-head'>"
                f"<h2>Iterations <span class='muted'>· {len(iters)}</span></h2>"
                "<span class='iter-strip-hint'>"
                "click to expand · ← → keys to navigate · "
                "tick boxes below to refine multiple as one set"
                "</span></header>"
                "<nav class='iter-strip' role='tablist' aria-label='iter strip'>"
            )
            for n in iters:
                parts.append(_render_iter_thumb(
                    workdir, n, selected=(n == selected_iter),
                    url_prefix=url_prefix,
                ))
            parts.append("</nav>")
            # Phase 3: multi-select baselines + "Refine these N as a set"
            # CTA. The checkboxes drive a floating action bar that links
            # to `?step=2&set=...`. Server still also exposes
            # `?step=2&template=N` for the single-template legacy path
            # (the per-iter "Select as Template" links above the strip).
            parts.append(
                "<div class='baseline-multiselect' "
                "id='baseline-multiselect' aria-label='multi-select baselines'>"
                "<span class='baseline-ms-label muted' "
                "id='baseline-ms-status'>"
                "Pick baselines for Step 2 "
                "<span class='muted' id='baseline-ms-count'>(0)</span>:"
                "</span>"
            )
            for n in iters:
                parts.append(
                    f"<label class='baseline-ms-item'>"
                    f"<input type='checkbox' class='baseline-ms-cb' "
                    f"value='{n}' aria-label='include iter {n}'>"
                    f"<span>iter {n}</span></label>"
                )
            # Use <button> not <a> so there's no broken href fallback if
            # JS hasn't loaded yet. JS attaches a click handler that
            # navigates to ?step=2&set=... when 1+ boxes are checked.
            parts.append(
                "<button type='button' id='baseline-ms-go' "
                "class='baseline-ms-go' disabled>"
                "Refine selected as a set →</button>"
                "</div>"
            )
            # Pre-render every expanded panel; one is unhidden at a time
            # based on URL hash. CSS keeps them all hidden by default.
            # When `interactive=False` (--no-watch / --upload), there is
            # no trajectory.js loaded to unhide on hash change, so we
            # leave one panel visible — the selected iter, or the latest.
            visible_iter = (selected_iter
                            if selected_iter is not None
                            else iters[-1] if iters else None)
            for n in iters:
                start_hidden = interactive or (n != visible_iter)
                parts.append(_render_iter_expanded(
                    workdir, n, selected=(n == selected_iter),
                    url_prefix=url_prefix,
                    run_status=status_attr,
                    start_hidden=start_hidden,
                ))
            parts.append("</section>")

        # Final section removed (user feedback): the shipped figure is
        # already the same as the last iter's image in the strip + Step
        # 2 surfaces the working baseline image. Showing it as a
        # separate "Final" card duplicated information without adding
        # signal. The figure.png + figure.pdf files still exist on
        # disk and are served via /static/<name>/... + /api/.../pdf;
        # the workspace landing thumbnail also still uses figure.png.
    else:
        # ─── Step 2: chat refinement view ───
        # Phase 3: prefer ?set=1,3,5 (multi-baseline). Fall back to
        # ?template=N (legacy single template) → baseline_iters=[N].
        if baseline_iters:
            # Filter to iters that actually exist on disk.
            existing = {i for i in iters}
            baseline_iters = [n for n in baseline_iters if n in existing]
        if not baseline_iters and template_iter is not None:
            baseline_iters = [template_iter]
        if not baseline_iters:
            chosen = selected_iter if selected_iter is not None else (
                iters[-1] if iters else None
            )
            if chosen is not None:
                baseline_iters = [chosen]
        if not baseline_iters:
            # No iters yet — empty state.
            parts.append(
                "<section class='step2-section step2-empty-state'>"
                "<div class='missing'>"
                "No iterations yet. Wait for the runner to produce at "
                "least one iter, then come back to Step 2."
                "</div>"
                "</section>"
            )
        else:
            # Anchor template = first selected baseline (sorted).
            template_iter = baseline_iters[0]
            template_img = workdir / f"img_iter{template_iter}.png"
            template_audit = parse_audit(
                workdir / f"audit_iter{template_iter}.json"
            )
            verdict = template_audit.get(
                "fidelity", {}
            ).get("verdict", "?") if template_audit else "?"
            template_img_url = (
                _img_url(template_img, workdir, url_prefix)
                if template_img.exists() else ""
            )
            run_name_q = quote(workdir.name, safe="") if url_prefix else ""
            baseline_csv = ",".join(str(i) for i in baseline_iters)
            baseline_label = (
                f"baselines [{baseline_csv}]"
                if len(baseline_iters) > 1
                else f"baseline iter {baseline_iters[0]}"
            )

            parts.append(
                f"<section class='step2-section' "
                f"data-template-iter='{template_iter}' "
                f"data-baseline-iters='{baseline_csv}' "
                f"data-run-name='{escape(workdir.name)}'>"
                f"<header class='step2-head'>"
                f"<h2>Refine "
                f"<span class='muted'>· {escape(baseline_label)}</span>"
                f"</h2>"
                f"<div class='pills'>{fmt_audit_pill(template_audit)}</div>"
                f"</header>"
                f"<div class='step2-grid'>"
                # left column: template image + direct controls
                f"<div class='step2-left'>"
            )
            if template_img_url:
                # Resolution comes from the agent rendering at dpi=200
                # (per the Step-1 / Step-2 system prompts), not from a
                # server-side @2x fallback. Single src is enough.
                parts.append(
                    f"<figure class='step2-template'>"
                    f"<img src='{template_img_url}' "
                    f"alt='template iter {template_iter}'>"
                    f"<figcaption>template · iter {template_iter} · "
                    f"{escape(verdict)}</figcaption>"
                    f"</figure>"
                )
            # Working-image figure — pre-filled server-side so first
            # paint is stable (no JS-driven hidden→visible flash that
            # forces a left-rail height recalculation). Strategy:
            #
            # 1. If THIS baseline-set already has a chat with a prior
            #    refine_NNN.png on disk → use that (the user's coming
            #    back to a chat, they want to see current state).
            # 2. Otherwise → use the first baseline iter's image as a
            #    placeholder, with a "(no refine yet)" caption.
            #
            # JS swaps src on each SSE refine_complete (with no-op
            # guard) so live updates remain in sync.
            from figcopy_runner.chat_log import (
                read_turns as _rt,
                recover_orphan_refines as _recover,
            )
            from figcopy_runner.interface import compute_set_id as _csid
            try:
                _sid = _csid(baseline_iters)
            except Exception:
                _sid = None
            _working_url = template_img_url
            _working_caption = (
                f"baseline · iter {template_iter} (no refine yet)"
            )
            if _sid:
                _recover(workdir, set_id=_sid)
                _prior = _rt(workdir, set_id=_sid)
                for _e in reversed(_prior):
                    if _e.get("role") != "assistant":
                        continue
                    _iu = _e.get("image_url")
                    if not isinstance(_iu, str):
                        continue
                    if _iu.startswith("/"):
                        _working_url = _iu
                    else:
                        _full = workdir / _iu
                        if _full.exists():
                            _working_url = _img_url(_full, workdir, url_prefix)
                    import re as _re
                    _m = _re.search(r"refine_(\d{3})", _iu)
                    _working_caption = (
                        f"refine {int(_m.group(1))}" if _m
                        else "latest refinement"
                    )
                    break
            if _working_url:
                parts.append(
                    "<figure class='step2-current' id='step2-current'>"
                    f"<img src='{_working_url}' "
                    f"data-current-url='{_working_url}' "
                    "alt='current working refinement'>"
                    f"<figcaption>{escape(_working_caption)}</figcaption>"
                    "</figure>"
                )
            else:
                # No template image and no prior refine — keep hidden
                # so JS can populate later (rare edge case).
                parts.append(
                    "<figure class='step2-current' id='step2-current' hidden>"
                    "<img alt='latest refinement'>"
                    "<figcaption>most recent refinement</figcaption>"
                    "</figure>"
                )
            # Direct controls panel — JS appends a stepper per rcParam
            # mentioned. Server emits the empty shell + a hint so it's
            # discoverable before any chat turns happen.
            parts.append(
                "<section class='step2-controls' id='step2-controls' "
                "aria-labelledby='step2-controls-h3'>"
                "<h3 id='step2-controls-h3'>Direct controls "
                "<span class='muted'>· surfaces as the AI mentions params</span>"
                "</h3>"
                "<div class='step2-controls-list' "
                "id='step2-controls-list'></div>"
                "<div class='step2-controls-empty' "
                "id='step2-controls-empty'>"
                "No parameters surfaced yet. Send a chat message — the "
                "first delta the AI returns appears here as a stepper."
                "</div>"
                "</section>"
                "</div>"  # /.step2-left
            )
            # right column: chat log + send form
            parts.append(
                "<div class='step2-right'>"
                "<div class='step2-chat' id='step2-chat' role='log' "
                "aria-live='polite' aria-relevant='additions'>"
                "<div class='step2-chat-empty' id='step2-chat-empty'>"
                "<p>Type a refinement instruction below.</p>"
                "<p class='muted'>e.g. <code>字大一点</code>, "
                "<code>x 轴标题再大些</code>, <code>switch palette</code>.</p>"
                "</div>"
                "</div>"
                "<form class='step2-form' id='step2-form'>"
                "<textarea class='step2-msg' id='step2-msg' "
                "placeholder='Type a refinement…' "
                "aria-label='refinement message' rows='2'></textarea>"
                "<div class='step2-form-row'>"
                "<span class='step2-form-hint muted'>"
                "<kbd>⌘</kbd>+<kbd>Enter</kbd> to send</span>"
                "<span id='step2-pending-badge' "
                "class='step2-pending-badge' hidden "
                "title='direct-control edits bundled with next Send'></span>"
                "<button type='button' id='step2-cancel' "
                "class='step2-cancel' title='Cancel the in-flight "
                "refine on this baseline set'>Cancel</button>"
                "<button type='submit' class='step2-send'>Send →</button>"
                "</div>"
                "</form>"
                "</div>"  # /.step2-right
                "</div>"  # /.step2-grid
                "</section>"
            )

    parts.append("</div>")  # container

    # help bar (only when interactive)
    if interactive:
        parts.append(
            "<div class='helpbar' id='helpbar'>"
            "<kbd>←</kbd> <kbd>→</kbd> nav iters"
            "<span style='opacity:.4'>·</span>"
            "<kbd>click</kbd> image to enlarge"
            "<span style='opacity:.4'>·</span>"
            "<kbd>T</kbd> compare reference"
            "<span style='opacity:.4'>·</span>"
            "<kbd>Esc</kbd> close"
            "</div>"
        )

    # ---- interactive features (only when serving locally) ----
    if interactive:
        # JSON-encode so a path containing ', ", \, or </script would not
        # break out of the JS literal. (json.dumps output is safe in a
        # <script> context for our shape: pure strings of URL-safe chars
        # produced by quote(); contains no closing-tag sequence.)
        ref_url = (json.dumps(_img_url(ref, workdir, url_prefix))
                   if ref.exists() else "null")
        parts.append(
            f"<script>window.FIGCOPY_REF_URL = {ref_url};</script>"
            f"<script src='/static-ui/trajectory.js?v="
            f"{int((STATIC_UI_DIR / 'trajectory.js').stat().st_mtime) if (STATIC_UI_DIR / 'trajectory.js').exists() else 0}' "
            "defer></script>"
        )

    parts.append("</body></html>")
    return "\n".join(parts)


def render_inline(workdir: Path) -> str:
    """Render HTML with all images embedded as base64 data URIs and the
    CSS inlined as a <style> block. Self-contained — works as a single
    file, no server / no relative paths. Used by --upload (HF Space)
    and reused for --no-serve writes.
    """
    import base64

    html = render_html(workdir, inline_assets=True)
    serve_root = workdir.parent

    def _inline(match: "re.Match[str]") -> str:
        rel = match.group(1)
        # path could be relative to serve_root (workdir parent)
        candidates = [serve_root / rel, workdir / rel.split("/", 1)[-1]]
        for p in candidates:
            if p.exists():
                b64 = base64.b64encode(p.read_bytes()).decode("ascii")
                return f"src='data:image/png;base64,{b64}'"
        return match.group(0)

    return re.sub(r"src='([^']+\.(?:png|jpg|jpeg))'", _inline, html)


def upload_to_hf(html_text: str, workdir: Path, space_id: str) -> str:
    """Upload self-contained HTML to a Space, return the shareable URL."""
    try:
        from huggingface_hub import HfApi, upload_file  # noqa
    except ImportError:
        raise SystemExit("huggingface_hub not installed; pip install huggingface_hub")

    import datetime
    import io
    import tempfile

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"trajectory__{workdir.name}__{stamp}.html"
    path_in_repo = f"trajectories/{fname}"

    # write to a temp file then upload (HfApi wants a path)
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as fp:
        fp.write(html_text)
        tmp = fp.name

    upload_file(
        path_or_fileobj=tmp,
        path_in_repo=path_in_repo,
        repo_id=space_id,
        repo_type="space",
        commit_message=f"FigMirror serve upload: {workdir.name} @ {stamp}",
    )
    return f"https://huggingface.co/spaces/{space_id}/blob/main/{path_in_repo}"


def _run_state(workdir: Path, iters: list[int]) -> tuple[str, int | None, str | None, str | None]:
    """Best-guess status + thumbnail-image for one run.

    Returns ``(status, current_iter, thumb_url, reason)``:

    - ``status``: ``shipped`` if ``figure.png`` exists; ``idle`` otherwise.
      Phase 3 :class:`MockRunner` (and the real codex / claude backends)
      additionally surface ``running`` / ``failed`` by writing
      ``status.json``; we read that file when present.
    - ``current_iter``: the highest iter number on disk (or ``None`` if no
      iters yet).
    - ``thumb_url``: an absolute URL under ``/static/<name>/...`` pointing
      to ``figure.png`` if shipped, otherwise the latest ``img_iter<N>.png``
      that exists; ``None`` if nothing renderable yet.
    """
    name_url = quote(workdir.name, safe="")
    status = "shipped" if (workdir / "figure.png").exists() else "idle"
    current_iter: int | None = iters[-1] if iters else None
    reason: str | None = None

    # MockRunner / CodexRunner / ClaudeRunner all write this sidecar
    # via the same atomic protocol; we read it here read-only.
    sidecar = read_status_sidecar(workdir)
    if sidecar:
        status = sidecar["state"]
        if isinstance(sidecar.get("current_iter"), int):
            current_iter = sidecar["current_iter"]
        if isinstance(sidecar.get("reason"), str):
            reason = sidecar["reason"]

    protocol_failure = reviewer_protocol_failure_reason(workdir)
    if protocol_failure and status != "cancelled":
        status = "failed"
        reason = protocol_failure

    if (workdir / "figure.png").exists():
        thumb_url = f"/static/{name_url}/figure.png"
    else:
        # Walk iters from newest to oldest looking for a real image.
        # discover_iters counts an iter that has only notes/code/audit
        # but no img_iterN.png yet (mid-write or render-failed); using
        # the latest such index unconditionally would 404 the run-bar
        # thumbnail. Prefer the newest iter that actually has an image.
        thumb_url = None
        for i in reversed(iters):
            if (workdir / f"img_iter{i}.png").exists():
                thumb_url = f"/static/{name_url}/img_iter{i}.png"
                break
    return status, current_iter, thumb_url, reason


def discover_runs(
    workspace: Path,
    *,
    runner_for: Callable[[Path], object] | None = None,
) -> list[dict]:
    """List subdirs of workspace that look like figcopy workdirs.

    Each entry includes ``name``, ``n_iters``, ``status``, ``current_iter``,
    ``thumb_url``, ``shipped``, ``mtime`` — consumed by both
    ``render_landing`` (server-side first paint) and ``/api/runs.json``
    (client-side live polling).

    When ``runner_for`` is supplied, each run is also resolved against
    the active backend table; runs whose configured backend is missing
    on this host (``BackendUnavailable``) are reported as ``failed``
    with the exception's text in ``reason``. Without this, such runs
    appear permanently spinning in the workspace UI even though every
    POST against them 503s.
    """
    runs: list[dict] = []
    if not workspace.is_dir():
        return runs
    # stat() once per dir, reuse for both sort key and the response.
    # Was previously double-statting at sort + dict-build time.
    entries: list[tuple[Path, float]] = []
    for p in workspace.iterdir():
        if not p.is_dir() or p.name.startswith("."):
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        entries.append((p, mtime))
    entries.sort(key=lambda pm: pm[1], reverse=True)
    for p, mtime in entries:
        iters = discover_iters(p)
        # treat as run if it has an inputs/ subdir or any iter artifacts
        if iters or (p / "inputs").exists():
            status, current_iter, thumb_url, reason = _run_state(p, iters)
            if runner_for is not None and status not in _TERMINAL_RUN_STATUSES:
                try:
                    runner_for(p)
                except BackendUnavailable as e:
                    # The configured backend is missing on this host —
                    # POSTs against this run will 503 forever, so don't
                    # render it as "running" or "queued"; mark failed
                    # with the exception's remediation text so the UI
                    # surfaces something actionable. Skip terminal
                    # statuses (shipped/failed/cancelled) — their
                    # disk-derived outcome is the source of truth and
                    # the user is browsing artifacts that don't need
                    # the backend.
                    status = "failed"
                    reason = str(e)
            # Backend tag: read from config.json (written by
            # create_run from the form's backend dropdown). Older
            # runs predating per-run backend selection have no field
            # → returned as None and the UI renders no badge.
            backend = None
            try:
                cfg = json.loads(
                    (p / "config.json").read_text(encoding="utf-8")
                )
                bk = cfg.get("backend")
                if isinstance(bk, str) and bk in KNOWN_BACKENDS:
                    backend = bk
            except Exception:
                pass
            runs.append({
                "name": p.name,
                "n_iters": len(iters),
                "status": status,
                "current_iter": current_iter,
                "thumb_url": thumb_url,
                "shipped": (p / "figure.png").exists(),
                "mtime": mtime,
                "backend": backend,
                "reason": reason,
            })
    return runs


def _pill_for_status(status: str) -> str:
    """Map run status → pill HTML. Used by both server-render first paint
    and client-side rendering (mirror logic in workspace.js)."""
    label = {
        "shipped": "shipped",
        "running": "running",
        "failed":  "failed",
        "idle":    "idle",
    }.get(status, status)
    cls = {
        "shipped": "good",
        "running": "neutral",
        "failed":  "bad",
        "idle":    "mute",
    }.get(status, "mute")
    return _pill(cls, label)


_BACKEND_BADGE_LABEL = {
    "codex": "Cx",
    "claude": "Cl",
    "mock":  "Mk",
}
_BACKEND_BADGE_TITLE = {
    "codex": "Codex CLI",
    "claude": "Claude Code",
    "mock":  "MockRunner",
}


_BACKEND_OPTION_LABELS = {
    "codex": "Codex CLI",
    "claude": "Claude Code",
    "mock": "MockRunner",
}


def _render_backend_option(
    value: str,
    label: str,
    *,
    available_backends: set[str] | None,
    selected_backend: str,
) -> str:
    """Render one ``<option>`` for the backend dropdown.

    Invariant: never combine ``selected`` with ``disabled`` on the same
    option. Browsers do not submit disabled fields, so a pre-selected
    disabled option produces a form whose ``backend`` value is empty —
    which then falls through to server-side defaulting and may pick
    another unavailable backend, defeating the entire point of the
    availability gating. ``render_landing`` is responsible for picking
    a ``selected_backend`` that is actually available; this helper
    enforces the invariant defensively.
    """
    disabled = (
        available_backends is not None
        and value not in available_backends
    )
    attrs = [f"value='{escape(value)}'"]
    if value == selected_backend and not disabled:
        attrs.append("selected")
    if disabled:
        attrs.append("disabled")
        attrs.append("data-unavailable='1'")
        attrs.append("title='CLI not found on PATH'")
    return f"<option {' '.join(attrs)}>{escape(label)}</option>"


def _render_backend_options(
    *,
    available_backends: set[str] | None,
    selected_backend: str,
) -> str:
    """Render every option in ``KNOWN_BACKENDS`` with availability gating.

    Iterates ``KNOWN_BACKENDS`` (the same constant that ``create_run``
    accepts) so the UI cannot drift behind the server's accepted set.
    A backend whose CLI isn't on this host's PATH renders disabled.
    """
    return "".join(
        _render_backend_option(
            value,
            _BACKEND_OPTION_LABELS.get(value, value),
            available_backends=available_backends,
            selected_backend=selected_backend,
        )
        for value in KNOWN_BACKENDS
    )


def _render_backend_badge(backend: str | None) -> str:
    """Return a small backend-tag pill HTML, or empty string when
    backend is unknown (legacy runs created before the per-run
    backend field existed). workspace.js mirrors the same DOM
    shape on diff-update."""
    if backend not in _BACKEND_BADGE_LABEL:
        return ""
    label = _BACKEND_BADGE_LABEL[backend]
    title = _BACKEND_BADGE_TITLE[backend]
    return (
        f"<span class='run-backend-badge run-backend-{escape(backend)}' "
        f"title='{escape(title)}' aria-label='backend: {escape(title)}'>"
        f"{escape(label)}</span>"
    )


def _render_run_bar(r: dict) -> str:
    """Render one run as a long-bar card. Server-side first paint;
    workspace.js mirrors this layout when it diff-updates entries."""
    name = r["name"]
    href = quote(name, safe="")
    n_iters = r["n_iters"]
    status = r["status"]
    cur = r.get("current_iter")
    thumb_url = r.get("thumb_url")
    backend = r.get("backend")
    reason = r.get("reason")
    pill_html = _pill_for_status(status)
    backend_html = _render_backend_badge(backend)
    reason_attr = f" title='{escape(reason)}'" if reason else ""

    # Iter counter: "i/N" while running, "N iters" if idle/shipped.
    if status == "running" and cur is not None:
        iter_label = f"iter {cur + 1}"  # 1-based for display
    else:
        iter_label = f"{n_iters} iter{'s' if n_iters != 1 else ''}"

    if thumb_url:
        thumb_html = (
            f"<a href='/r/{href}' class='run-bar-thumb' tabindex='-1'>"
            f"<img src='{escape(thumb_url)}' alt='' loading='lazy'>"
            f"</a>"
        )
    else:
        empty_state = (
            "loading" if status == "running"
            else "failed" if status == "failed"
            else "idle"
        )
        thumb_html = (
            "<span class='run-bar-thumb run-bar-thumb-empty "
            f"run-bar-thumb-{empty_state}' aria-hidden='true'></span>"
        )

    return (
        f"<li class='run-bar' data-name='{escape(name)}' "
        f"data-status='{escape(status)}' data-n-iters='{n_iters}' "
        f"data-backend='{escape(backend or '')}'{reason_attr}>"
        f"{thumb_html}"
        f"<div class='run-bar-body'>"
        f"<div class='run-bar-name-row'>"
        f"{backend_html}"
        f"<a href='/r/{href}' class='run-bar-name'>{escape(name)}</a>"
        f"</div>"
        f"<span class='run-bar-meta'>{escape(iter_label)}</span>"
        f"</div>"
        f"<span class='run-bar-status'>{pill_html}</span>"
        # Delete button — small × on the row's right edge, hidden
        # until the user hovers the row (CSS). Click confirms + sends
        # DELETE /api/runs/<name>. Disabled-look while the run is
        # running (server still refuses with 409; the visual cue
        # tells the user before they try).
        f"<button type='button' class='run-bar-delete' "
        f"data-name='{escape(name)}' "
        f"aria-label='delete run {escape(name)}' "
        f"title='Delete this run'>×</button>"
        f"</li>"
    )


def render_landing(
    workspace: Path,
    *,
    available_backends: set[str] | None = None,
    default_backend: str = "codex",
    runner_for: Callable[[Path], object] | None = None,
) -> str:
    """Landing page for workspace mode: New-run form + live run-bar list."""
    runs = discover_runs(workspace, runner_for=runner_for)
    # Pick the option to mark `selected` in the dropdown:
    #   1. The configured `default_backend` if it's available on this host.
    #   2. Otherwise the first KNOWN_BACKENDS entry that is available
    #      (preserves the canonical preference order: codex > claude > mock).
    #   3. Otherwise fall back to the configured default — every option
    #      renders disabled in this case and `_render_backend_option`
    #      drops the `selected` attribute, so the resulting form has no
    #      pre-selected backend (browser will default to the first option,
    #      which is fine because none can be submitted anyway).
    if available_backends is None or default_backend in available_backends:
        selectable_backend = default_backend
    else:
        selectable_backend = next(
            (b for b in KNOWN_BACKENDS if b in available_backends),
            default_backend,
        )

    parts: list[str] = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<title>{escape(workspace.name)} · FigMirror</title>",
        "<link rel='stylesheet' href='/static-ui/style.css'>",
        f"<script src='/static-ui/workspace.js?v="
        f"{int((STATIC_UI_DIR / 'workspace.js').stat().st_mtime) if (STATIC_UI_DIR / 'workspace.js').exists() else 0}' "
        "defer></script>",
        # `is-landing` lets CSS lock the page to viewport-height
        # with the runs panel scrolling internally. Same shape as
        # `is-step2` (Step 2 workbench) — see body.is-landing rules
        # in style.css.
        "</head><body class='is-landing'>",
        "<header class='top'><div class='container'>",
        "<div class='brand'>FigMirror · workspace</div>",
        f"<div class='run'>{escape(workspace.name)}</div>",
        f"<div class='run-path'>{escape(str(workspace))}</div>",
        "</div></header>",
        "<div class='container'><div class='layout'>",
        # left panel: New run form
        "<section class='panel'>",
        "<h2>New run</h2>",
        "<form class='run-form' id='new-run-form' action='/api/run' method='POST' "
        "enctype='multipart/form-data'>",

        "<label>Run name <span class='hint'>· optional, auto if blank</span>"
        "<input type='text' name='run_name' placeholder='e.g. nature-style-radar'></label>",

        # Reference figure: drop-zone with paste/drag/click. The native
        # <input type=file> is the source of truth for form submission;
        # JS mirrors paste + drag-drop into it via DataTransfer.
        "<div class='input-zone' data-input='ref' data-kind='image'>",
        "<span class='input-zone-label'>Reference figure"
        "<span class='hint'>· paste, drag-drop, or click</span></span>",
        "<div class='dropzone' tabindex='0' role='button' "
        "aria-label='reference figure: paste, drop, or click to choose'>",
        "<input type='file' name='ref' accept='image/png,image/jpeg' "
        "required class='dropzone-input'>",
        "<div class='dropzone-hint'>",
        "<span class='dropzone-icon' aria-hidden='true'>⤓</span>",
        "<span class='dropzone-text'>drop image, paste from clipboard, "
        "or click to browse</span>",
        "</div>",
        "<div class='dropzone-preview' hidden>",
        "<img class='dropzone-img' alt='reference preview'>",
        "<button type='button' class='dropzone-clear' "
        "aria-label='remove reference image'>×</button>",
        "</div>",
        "</div>",  # /.dropzone
        "</div>",  # /.input-zone

        # Data: drop-zone with paste/drag/click. Renders fingerprint
        # summary (sha256 / lines / size / first 3) instead of full content.
        "<div class='input-zone' data-input='data' data-kind='data'>",
        "<span class='input-zone-label'>Data"
        "<span class='hint'>· optional · paste table, drop CSV, or click</span></span>",
        "<div class='dropzone dropzone-data' tabindex='0' role='button' "
        "aria-label='data: paste, drop, or click to choose'>",
        "<input type='file' name='data' class='dropzone-input'>",
        "<div class='dropzone-hint'>",
        "<span class='dropzone-icon' aria-hidden='true'>⤓</span>",
        "<span class='dropzone-text'>drop CSV/TSV, paste table, "
        "or click to browse <span class='muted'>· skill fabricates if blank</span></span>",
        "</div>",
        "<div class='dropzone-preview dropzone-preview-data' hidden>",
        "<div class='fingerprint'>",
        "<div class='fp-row'><span class='fp-label'>lines</span>"
        "<span class='fp-val' data-fp='lines'>—</span></div>",
        "<div class='fp-row'><span class='fp-label'>size</span>"
        "<span class='fp-val' data-fp='size'>—</span></div>",
        "<div class='fp-row'><span class='fp-label'>sha256</span>"
        "<span class='fp-val' data-fp='sha'>—</span></div>",
        "</div>",
        "<pre class='fp-preview' data-fp='preview'></pre>",
        "<button type='button' class='dropzone-clear' "
        "aria-label='remove data'>×</button>",
        "</div>",  # /.dropzone-preview
        "</div>",  # /.dropzone
        "</div>",  # /.input-zone

        "<label>Style instructions <span class='hint'>· optional, free text</span>"
        "<textarea name='prompt' placeholder='e.g. use a dark Nature-style "
        "palette, prefer thicker axis lines'></textarea></label>",

        # max_iters is always a hard Drawer cap. The `auto` checkbox asks the
        # runner to follow deterministic review actions without pausing until
        # ship or that cap. Layout: input and the checkbox sit side-by-side in
        # `.iters-row` — vertically centered against each other —
        # so the two interactive "boxes" line up at the same y. The
        # top-level wrapper is a `div` (not `label`) so we can put a
        # standalone `<label for=...>` above the input without
        # nesting labels (which browsers tolerate but is invalid).
        "<div class='form-field'>"
        "<label for='run-max-iters'>Max iterations</label>"
        "<div class='iters-row'>"
        "<input type='number' id='run-max-iters' name='max_iters' "
        "value='5' min='1' max='20'>"
        "<label class='checkbox' for='run-auto-cb'>"
        "<input type='checkbox' id='run-auto-cb' name='auto' value='1'>"
        "<span>auto-continue to ship or cap</span>"
        "</label>"
        "</div>"
        "</div>",

        # Backend selector. Iterates KNOWN_BACKENDS so the dropdown
        # cannot drift behind the server's accepted set; each option
        # renders disabled when its CLI isn't on PATH on this host.
        # Mock surfaces here (it's part of KNOWN_BACKENDS) so a host
        # started with `--backend mock` for offline dev can actually
        # select it from the UI.
        "<div class='form-field'>"
        "<label for='run-backend'>Backend</label>"
        "<select id='run-backend' name='backend' class='run-backend'>"
        f"{_render_backend_options(available_backends=available_backends, selected_backend=selectable_backend)}"
        "</select>"
        "</div>",

        "<button class='submit' type='submit'>Run →</button>",

        # Backend-aware footnote. Populated by workspace.js on page
        # load and on every dropdown change; reflects whichever
        # backend the user has selected. Kept as an empty div here
        # (no SSR fallback) since the JS handler runs at boot and
        # there's no useful no-JS variant — the form submission
        # itself depends on JS anyway.
        "<div class='stub-note' id='backend-note' aria-live='polite'></div>",

        "</form>",
        "</section>",
        # right panel: run list (live-updated by workspace.js
        # polling). The `run-panel` class is the hook for the
        # landing-page height-sync CSS+JS: this panel's max-height
        # is pinned to the LEFT panel's offsetHeight so a long
        # runs list scrolls internally instead of growing the page.
        "<section class='panel run-panel'>",
        f"<h2>Runs <span class='muted' data-runs-count>· {len(runs)}</span></h2>",
    ]
    if not runs:
        parts.append(
            "<ul class='run-list' id='run-list' data-empty='1'></ul>"
            "<div class='empty' id='run-list-empty'>"
            "No runs yet. Submit one with the form, or drop a workdir in "
            "the workspace to see it here."
            "</div>"
        )
    else:
        parts.append("<ul class='run-list' id='run-list'>")
        for r in runs:
            parts.append(_render_run_bar(r))
        parts.append("</ul>")
        parts.append("<div class='empty' id='run-list-empty' hidden>"
                     "No runs yet."
                     "</div>")
    parts.append("</section>")
    parts.append("</div></div></body></html>")
    return "\n".join(parts)


def parse_multipart(headers, body: bytes) -> dict:
    """Minimal multipart/form-data parser, stdlib-only.

    Returns a dict mapping field name to either:
      - str (for plain text fields), or
      - {'filename': str, 'content': bytes} (for file uploads).
    """
    ctype = headers.get("Content-Type", "")
    m = re.search(r'boundary=("?)([^";]+)\1', ctype)
    if not m:
        raise ValueError("no boundary in Content-Type")
    boundary = b"--" + m.group(2).encode()
    chunks = body.split(boundary)
    out: dict = {}
    for chunk in chunks[1:-1]:  # skip preamble and closing boundary
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        head_end = chunk.find(b"\r\n\r\n")
        if head_end < 0:
            continue
        head_blob = chunk[:head_end].decode("latin-1", errors="replace")
        content = chunk[head_end + 4:]
        if content.endswith(b"\r\n"):
            content = content[:-2]
        disp = re.search(r'Content-Disposition:\s*form-data;\s*([^\r\n]+)',
                         head_blob, re.I)
        if not disp:
            continue
        params = dict(re.findall(r'(\w+)="([^"]*)"', disp.group(1)))
        name = params.get("name")
        filename = params.get("filename")
        if not name:
            continue
        if filename is not None:
            out[name] = {"filename": filename, "content": content}
        else:
            try:
                out[name] = content.decode("utf-8")
            except UnicodeDecodeError:
                out[name] = content.decode("latin-1", errors="replace")
    return out


_SLUG_OK = re.compile(r"[^a-zA-Z0-9._-]")


def slugify_run_name(name: str) -> str:
    """Sanitize user-supplied run name to a path-safe slug."""
    s = _SLUG_OK.sub("-", name.strip()).strip("-._")
    return s[:64] if s else ""


def auto_run_name() -> str:
    import datetime
    return "run_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def create_run(
    workspace: Path,
    form: dict,
    *,
    available_backends: set[str] | None = None,
) -> tuple[str, Path, dict]:
    """Stage a new run from a parsed multipart form.

    Returns ``(name, run_dir, config)``. The caller (WorkspaceHandler)
    uses ``config["prompt"]`` and ``config["max_iters"]`` to invoke
    ``runner.start(run_dir, ...)`` after staging completes.
    """
    raw_backend = form.get("backend")
    if isinstance(raw_backend, str) and raw_backend in KNOWN_BACKENDS:
        run_backend = raw_backend
    else:
        run_backend = None  # let runner_for() use the server's default
    if (
        run_backend is not None
        and available_backends is not None
        and run_backend not in available_backends
    ):
        raise BackendUnavailable(
            f"backend {run_backend!r} is unavailable in this server "
            f"process; available: {sorted(available_backends)}. Install "
            f"the {run_backend!r} CLI on this host (and restart the "
            f"server) or pick one of the available backends."
        )

    raw_name = form.get("run_name", "") if isinstance(form.get("run_name"), str) else ""
    name = slugify_run_name(raw_name) or auto_run_name()
    base, suffix = name, 1
    while True:
        run_dir = workspace / name
        try:
            run_dir.mkdir(parents=False, exist_ok=False)
            break
        except FileExistsError:
            suffix += 1
            name = f"{base}-{suffix}"

    inputs = run_dir / "inputs"
    inputs.mkdir(exist_ok=False)

    ref = form.get("ref")
    if isinstance(ref, dict) and ref.get("content"):
        # Preserve the upload as raw; Stage-0 preprocessing overwrites
        # reference_clean.png with the crop the skill should measure.
        (inputs / "reference_raw.png").write_bytes(ref["content"])
        (inputs / "reference_clean.png").write_bytes(ref["content"])
    data = form.get("data")
    fabricated = not (isinstance(data, dict) and data.get("content"))
    if fabricated:
        # SKILL.md path: when the user uploads no data, CodexRunner runs
        # a one-shot data-gen pass before the Drawer/Reviewer loop and
        # rewrites this file. The exact placeholder text lives in
        # figcopy_runner.interface.DATA_PLACEHOLDER_TEXT — single source
        # of truth so an edit here can't silently break the runner's
        # is_data_placeholder() detection.
        from figcopy_runner.interface import DATA_PLACEHOLDER_TEXT
        (inputs / "data.txt").write_text(
            DATA_PLACEHOLDER_TEXT, encoding="utf-8",
        )
    else:
        (inputs / "data.txt").write_bytes(data["content"])

    prompt = form.get("prompt", "") if isinstance(form.get("prompt"), str) else ""
    try:
        max_iters = int(form.get("max_iters", "5") or "5")
    except (TypeError, ValueError):
        max_iters = 5
    max_iters = max(MIN_MAX_ITERS, min(MAX_MAX_ITERS, max_iters))
    auto_flag = bool(form.get("auto"))  # checkbox sends "1" if checked, missing if not
    import datetime
    config = {
        "prompt": prompt,
        "max_iters": max_iters,
        "auto": auto_flag,
        "data_fabricated": fabricated,
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "engine_status": "mock",
    }
    if run_backend is not None:
        config["backend"] = run_backend
    (run_dir / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

    print(f"  + new run staged: {run_dir}")
    print(f"    prompt: {prompt!r}")
    print(f"    max_iters: {max_iters}, auto: {auto_flag}, backend: {run_backend or '(default)'}")
    return name, run_dir, config


def run_workspace(args) -> int:
    """Web-app mode: serve a multi-run workspace at <root>/."""
    workspace = args.workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    interactive = not args.no_watch

    # Phase 3: instantiate ALL available backends at startup. The form
    # POST /api/run includes a `backend` field (codex|claude) that
    # picks which runner this specific run uses; it's persisted to
    # config.json so subsequent /api/refine, /api/cancel, status
    # routes through the same runner. The legacy `--backend` CLI flag
    # becomes the DEFAULT used when the form omits the field.
    import shutil as _shutil
    default_backend = getattr(args, "backend", None) or (
        "mock" if getattr(args, "mock", False) else "codex"
    )
    if getattr(args, "mock", False) and default_backend != "mock":
        print(
            "[figcopy_serve] --mock is deprecated; use --backend mock. "
            f"Honoring --backend {default_backend} (set explicitly).",
            file=sys.stderr,
        )
    elif getattr(args, "mock", False):
        print(
            "[figcopy_serve] --mock is deprecated; use --backend mock.",
            file=sys.stderr,
        )

    runners: dict[str, object] = {}
    from figcopy_runner import MockRunner
    runners["mock"] = MockRunner()
    # The codex/claude runners now invoke their backend CLI through
    # `uv run --project <repo>` (see `_uv_cmd` in each runner module),
    # so `uv` is a hard runtime dep too. If `uv` is missing we MUST NOT
    # advertise these backends — otherwise the first Stage-0 Popen
    # raises `FileNotFoundError` after status.json has flipped to
    # `running`, leaving the UI permanently spinning. Gate both real
    # backends on (agent CLI present) AND (uv present) via the
    # _backend_runtime_available helper (see its docstring).
    if _backend_runtime_available("codex", which=_shutil.which):
        from figcopy_runner import CodexRunner
        runners["codex"] = CodexRunner()
    if _backend_runtime_available("claude", which=_shutil.which):
        from figcopy_runner import ClaudeRunner
        runners["claude"] = ClaudeRunner()

    if default_backend not in runners:
        # Be specific about WHY a backend is unavailable when the
        # operator picked it: missing agent CLI, missing uv, or both.
        missing = []
        if default_backend in ("codex", "claude"):
            if not _shutil.which(default_backend):
                missing.append(f"{default_backend!r} CLI")
            if not _shutil.which("uv"):
                missing.append("'uv' CLI (used to wrap subprocess "
                               "launches via `uv run --project`)")
        reason = (
            f" (missing: {', '.join(missing)})" if missing else
            " (CLI not on $PATH)"
        )
        print(
            f"[figcopy_serve] ERROR: default backend {default_backend!r} "
            f"unavailable{reason}. Available: {sorted(runners)}",
            file=sys.stderr,
        )
        sys.exit(2)

    available_backend_names = set(runners)

    def runner_for(run_dir: Path):
        """Return the runner instance bound to this run.

        Reads `backend` from config.json (written by `create_run`).
        Falls back to the server's `default_backend` only when the
        field is missing or invalid (legacy runs / form tampering).
        If a known backend is recorded but unavailable in this server
        process, fail loudly instead of silently running a different
        CLI from the one shown in the UI.
        """
        try:
            cfg = json.loads(
                (run_dir / "config.json").read_text(encoding="utf-8")
            )
            chosen = cfg.get("backend")
        except Exception:
            chosen = None
        if chosen in runners:
            return runners[chosen]
        if chosen in KNOWN_BACKENDS:
            raise BackendUnavailable(
                f"run {run_dir.name!r} is configured for backend "
                f"{chosen!r}, but this server has only "
                f"{sorted(available_backend_names)}. Install the "
                f"{chosen!r} CLI on this host (and restart the server) "
                f"or recreate the run with one of the available backends."
            )
        return runners[default_backend]

    print(
        f"[figcopy_serve] backends available: {sorted(runners)}; "
        f"default: {default_backend}",
        file=sys.stderr,
    )

    class WorkspaceHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            route = self.path.split("?", 1)[0]
            if route == "/api/run":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self.send_error(400, "invalid Content-Length")
                    return
                if length > MAX_UPLOAD_BYTES:
                    self.send_error(413, f"upload too large; limit is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
                    return
                body = self.rfile.read(length) if length else b""
                try:
                    form = parse_multipart(self.headers, body)
                except ValueError as e:
                    self.send_error(400, f"bad multipart body: {e}")
                    return
                # require ref and data to be uploaded files with content
                if not (isinstance(form.get("ref"), dict) and form["ref"].get("content")):
                    self.send_error(400, "missing reference image (field 'ref')")
                    return
                # data is optional — when absent, the skill is told to fabricate.
                try:
                    name, run_dir, run_config = create_run(
                        workspace,
                        form,
                        available_backends=available_backend_names,
                    )
                except BackendUnavailable as e:
                    self.send_error(503, str(e))
                    return
                except OSError as e:
                    self.send_error(500, f"failed to stage run: {e}")
                    return
                # Kick the runner. start() spawns a daemon thread and
                # returns immediately; the caller is redirected to the
                # trajectory page where live polling shows iters appear.
                # `runner_for(run_dir)` reads the backend selection out
                # of config.json (set by `create_run` from the form's
                # `backend` field).
                try:
                    runner_for(run_dir).start(
                        run_dir,
                        prompt=run_config["prompt"],
                        max_iters=run_config["max_iters"],
                        auto=bool(run_config.get("auto")),
                    )
                except BackendUnavailable as e:
                    # Defensive: should not fire here because create_run
                    # already validated `backend` against the same
                    # available_backend_names. If the host's backend set
                    # changed between staging and start (e.g. a CLI was
                    # uninstalled mid-flight), surface the structured
                    # `.reason` text by writing status.json so the
                    # trajectory page renders the actual remediation
                    # guidance instead of a silent "queued forever".
                    print(
                        f"[runner] start({run_dir.name}) backend "
                        f"unavailable: {e}",
                        file=sys.stderr,
                    )
                    try:
                        sj_tmp = run_dir / "status.json.tmp"
                        sj_tmp.write_text(json.dumps({
                            "state": "failed",
                            "current_iter": None,
                            "reason": str(e),
                        }), encoding="utf-8")
                        sj_tmp.replace(run_dir / "status.json")
                    except Exception:
                        # status sidecar is best-effort here; the
                        # workdir is already staged so we still want
                        # the response below to land.
                        pass
                except Exception as e:
                    # Don't fail the whole request — the workdir is already
                    # staged; surface the error in logs and the trajectory
                    # status banner. (User can retry by re-running the form
                    # or driving an agent skill against the workdir manually.)
                    print(f"[runner] start({run_dir.name}) failed: {e}",
                          file=sys.stderr)
                # If the request was made via fetch() (AJAX), return JSON
                # and stay on the landing page. Otherwise (plain form
                # POST, no JS), preserve the legacy 303 → /r/<name> for
                # the no-JS fallback.
                #
                # Per user direction (2026-05-12): "你点了run之后，不
                # 需要跳过去，只需要在右边显示一下有新的东西出现就
                # 行了" — the right-hand run list polls /api/runs.json
                # every 3s and the workspace.js form handler also kicks
                # one extra poll right after submit, so the new card
                # appears within ~100ms.
                accept = self.headers.get("Accept", "")
                xrw = self.headers.get("X-Requested-With", "")
                wants_json = (
                    "application/json" in accept
                    or xrw == "figcopy-async"
                )
                if wants_json:
                    payload = json.dumps({
                        "name": name,
                        "url": f"/r/{name}",
                    }).encode("utf-8")
                    self.send_response(201)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                self.send_response(303)
                self.send_header("Location", f"/r/{name}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            # POST /api/refine — Step 2 chat / direct-control turn.
            #
            # Phase-3 body shape (preferred):
            #     {run, baseline_iters: [int, ...],
            #      message?: str, adjustments?: dict}
            #
            # Phase-2 backward-compat (deprecated; removed in Stage F):
            #     {run, template_iter: int, message?, rcparams?: dict}
            #   → translated to: baseline_iters=[template_iter],
            #                    adjustments=rcparams.
            #
            # Returns the runner's dict, with image_url rewritten to an
            # absolute /static/<run>/<rel> URL. Phase-3 response also
            # carries set_id and seq fields the client can use to
            # subscribe to the SSE stream of this chat.
            if route == "/api/refine":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self.send_error(400, "invalid Content-Length")
                    return
                if length > MAX_UPLOAD_BYTES:
                    self.send_error(413, "request too large")
                    return
                body = self.rfile.read(length) if length else b""
                try:
                    payload = json.loads(body or b"{}")
                except json.JSONDecodeError as e:
                    self.send_error(400, f"bad JSON: {e}")
                    return
                if not isinstance(payload, dict):
                    self.send_error(400, "expected JSON object")
                    return
                run_name = payload.get("run")
                if not isinstance(run_name, str) or not run_name:
                    self.send_error(400, "missing 'run' field")
                    return
                # Resolve baseline_iters: prefer the new field; fall
                # back to template_iter (with deprecation warning).
                baseline_iters: list[int] | None = None
                raw_baseline = payload.get("baseline_iters")
                if isinstance(raw_baseline, list) and raw_baseline:
                    try:
                        baseline_iters = [int(x) for x in raw_baseline]
                    except (TypeError, ValueError):
                        self.send_error(
                            400,
                            "'baseline_iters' must be a list of ints",
                        )
                        return
                else:
                    raw_template = payload.get("template_iter")
                    if raw_template is not None:
                        try:
                            baseline_iters = [int(raw_template)]
                        except (TypeError, ValueError):
                            self.send_error(
                                400, "'template_iter' must be int"
                            )
                            return
                        print(
                            "[/api/refine] DEPRECATION: 'template_iter' "
                            "is legacy; send 'baseline_iters: [N, ...]' "
                            "instead. Phase-2 client compat shim active.",
                            flush=True,
                        )
                if not baseline_iters:
                    self._send_json_error(
                        400,
                        "bad_request",
                        "missing 'baseline_iters' (or legacy 'template_iter')",
                    )
                    return
                message = payload.get("message")
                if message is not None and not isinstance(message, str):
                    self.send_error(400, "'message' must be a string")
                    return
                # Accept both new 'adjustments' and legacy 'rcparams'.
                adjustments = payload.get("adjustments")
                if adjustments is None:
                    adjustments = payload.get("rcparams")
                if adjustments is not None and not isinstance(adjustments, dict):
                    self.send_error(
                        400, "'adjustments' must be an object"
                    )
                    return
                run_dir = _resolve_run(self, workspace, run_name)
                if run_dir is None:
                    return
                try:
                    result = runner_for(run_dir).refine(
                        run_dir,
                        baseline_iters=baseline_iters,
                        message=message,
                        adjustments=adjustments,
                    )
                except BackendUnavailable as e:
                    self._send_json_error(503, "backend_unavailable", str(e))
                    return
                except NotImplementedError as e:
                    self._send_json_error(501, "not_implemented", str(e))
                    return
                except ValueError as e:
                    self._send_json_error(
                        400, "bad_request", f"refine rejected input: {e}"
                    )
                    return
                except AttributeError:
                    self._send_json_error(
                        501,
                        "not_implemented",
                        "current runner backend has no refine() method",
                    )
                    return
                except Exception as e:
                    # Real runners raise typed exceptions for known
                    # control flow (RefineInFlight → 409; RefineFailed
                    # → 500 with detail). Avoid importing the runner
                    # module here (the runner package can vary by
                    # backend); match by class-name string instead.
                    ename = type(e).__name__
                    if ename == "RefineInFlight":
                        self._send_json_error(
                            409, "refine_in_flight", f"refine in flight: {e}"
                        )
                        return
                    if ename == "RefineFailed":
                        message = str(e)
                        code = (
                            "refine_timeout"
                            if "exceeded" in message or "timed out" in message
                            else "refine_failed"
                        )
                        self._send_json_error(500, code, message)
                        return
                    self._send_json_error(
                        500, "runner_refine_failed",
                        f"runner.refine failed: {e}",
                    )
                    return
                # Translate the runner's relative image_url into an
                # absolute URL the browser can fetch.
                if isinstance(result.get("image_url"), str) \
                        and not result["image_url"].startswith("/"):
                    rel = result["image_url"]
                    result["image_url"] = (
                        f"/static/{quote(run_name, safe='')}/"
                        f"{quote(rel, safe='')}"
                    )
                self._send_json(result)
                return
            # ─── Phase 3: cancel ──────────────────────────────────
            # POST /api/runs/<name>/cancel?slot=iter
            # POST /api/runs/<name>/cancel?slot=refine&set_id=<id>
            m = re.match(r"^/api/runs/([^/]+)/cancel$", route)
            if m:
                name = unquote(m.group(1))
                qs = self.path.split("?", 1)[1] if "?" in self.path else ""
                from urllib.parse import parse_qs as _pq
                params = {k: v[0] for k, v in _pq(qs).items()}
                slot = params.get("slot", "iter")
                if slot == "refine":
                    sid = params.get("set_id", "")
                    if not re.match(r"^[0-9a-f]{8}$", sid):
                        self.send_error(400, "missing or bad set_id")
                        return
                    slot = f"refine:{sid}"
                elif slot != "iter":
                    self.send_error(400, "slot must be 'iter' or 'refine'")
                    return
                run_dir = _resolve_run(self, workspace, name)
                if run_dir is None:
                    return
                try:
                    rnr = runner_for(run_dir)
                except BackendUnavailable as e:
                    self.send_error(503, str(e))
                    return
                try:
                    rnr.cancel(run_dir, slot=slot)
                except TypeError:
                    # Legacy runner with no slot kwarg — fall back.
                    rnr.cancel(run_dir)
                self.send_response(204)
                self.end_headers()
                return
            self.send_error(404)

        def do_DELETE(self):
            # DELETE /api/runs/<name>  →  remove a run from disk.
            #
            # Refuses to delete a still-running run (409) — the
            # client is expected to POST /cancel first, wait briefly
            # for the runner thread to flip state to "cancelled",
            # then retry DELETE. This avoids the race of tearing
            # files out from under a live subprocess.
            #
            # Safety: only allow deletes UNDER the configured
            # workspace (the `_resolve_run` helper already enforces
            # path-traversal containment), and only rmtree() that
            # specific subdirectory.
            route = self.path.split("?", 1)[0]
            m = re.match(r"^/api/runs/([^/]+)$", route)
            if not m:
                self.send_error(404)
                return
            name = unquote(m.group(1))
            run_dir = _resolve_run(self, workspace, name)
            if run_dir is None:
                return  # _resolve_run already sent 404
            # Refuse if still in-flight. We read status from the
            # runner first (in-memory truth) and fall back to the
            # sidecar.
            try:
                rstatus = runner_for(run_dir).status(run_dir) or {}
            except Exception:
                # Deliberately swallow everything (including
                # BackendUnavailable): we want users to be able to
                # delete unrunnable runs without first installing the
                # missing CLI. The fallback `cur_state = "idle"` lets
                # the rmtree below proceed; if the run was actually
                # mid-flight the rename will still race-safely
                # snapshot a stale view, which is the best we can do
                # without an in-process runner instance.
                rstatus = {}
            cur_state = rstatus.get("state") or "idle"
            if cur_state == "running":
                self.send_error(
                    409,
                    "run is still running; cancel it first "
                    "(POST /api/runs/<name>/cancel) and wait for "
                    "state to flip before retrying DELETE",
                )
                return
            # Atomic-ish removal: rename to a transient sibling
            # first so subsequent reads (poll, SSE) immediately see
            # the run as gone, then rmtree the renamed dir. Avoids
            # the half-deleted-state window where a polling client
            # could observe an empty config.json or zero-byte iter
            # PNGs.
            import shutil as _shutil
            import time as _time
            doomed = run_dir.with_name(
                f".{run_dir.name}.deleting-{int(_time.time() * 1000)}"
            )
            try:
                run_dir.rename(doomed)
            except OSError as e:
                self.send_error(500, f"failed to start delete: {e}")
                return
            try:
                _shutil.rmtree(doomed, ignore_errors=False)
            except Exception as e:
                # Partial cleanup; surface but report success on the
                # rename (the run is invisible to the workspace
                # already). Log and move on — the leftover dir is a
                # ops-cleanup item.
                print(
                    f"[figcopy_serve] DELETE {name}: rename ok but "
                    f"rmtree failed on {doomed!r}: {e}",
                    file=sys.stderr,
                )
            print(f"  - deleted run: {run_dir}", flush=True)
            self.send_response(204)
            self.end_headers()
            return

        def do_GET(self):
            route = self.path.split("?", 1)[0]
            if _serve_static_ui(self, route):
                return
            # /  — landing page
            if route == "/":
                self._send_html(render_landing(
                    workspace,
                    available_backends=available_backend_names,
                    default_backend=default_backend,
                    runner_for=runner_for,
                ))
                return
            # /api/runs.json — workspace state (poll target for landing
            # page). Returns the same dict shape `discover_runs` produces,
            # wrapped in `{"runs": [...]}`. workspace.js polls every 3s
            # and diff-updates the run-bar list.
            if route == "/api/runs.json":
                self._send_json({
                    "runs": discover_runs(workspace, runner_for=runner_for),
                })
                return
            # /r/<name>  — trajectory page for one run
            m = re.match(r"^/r/([^/]+)/?$", route)
            if m:
                name = unquote(m.group(1))
                run_dir = _resolve_run(self, workspace, name)
                if run_dir is None:
                    return
                run_url = quote(name, safe="")
                # Parse ?step=N, ?template=N (legacy single), ?set=1,3,5
                # (Phase 3 multi-baseline) from the query string.
                qs = self.path.split("?", 1)[1] if "?" in self.path else ""
                step = 1
                template_iter: int | None = None
                baseline_iters: list[int] | None = None
                if qs:
                    from urllib.parse import parse_qs
                    parsed = parse_qs(qs)
                    try:
                        step = int(parsed.get("step", ["1"])[0])
                    except (ValueError, IndexError):
                        step = 1
                    if "set" in parsed:
                        raw_set = parsed["set"][0]
                        try:
                            baseline_iters = sorted({
                                int(x.strip()) for x in raw_set.split(",")
                                if x.strip()
                            })
                        except ValueError:
                            baseline_iters = None
                    if "template" in parsed:
                        try:
                            template_iter = int(parsed["template"][0])
                        except (ValueError, IndexError):
                            template_iter = None
                # img URLs in the trajectory page resolve through /static/<name>/...
                self._send_html(render_html(
                    run_dir, interactive=interactive,
                    url_prefix=f"/static/{run_url}/",
                    breadcrumb_url="/",
                    step=step, template_iter=template_iter,
                    baseline_iters=baseline_iters,
                    runner_for=runner_for,
                ))
                return
            # /r/<name>/_state.json — state for one run
            m = re.match(r"^/r/([^/]+)/_state\.json$", route)
            if m:
                name = unquote(m.group(1))
                run_dir = _resolve_run(self, workspace, name)
                if run_dir is None:
                    return
                self._send_json(build_run_state(run_dir, runner_for=runner_for))
                return
            # /api/runs/<name>/code/<iter> — server-side highlighted
            # Python source for one iter. Produces an HTML fragment
            # (<pre class="py"><code>...</code></pre>); the browser drops
            # it into the Export Code modal. CSS for the .py-* classes
            # lives in style.css.
            m = re.match(r"^/api/runs/([^/]+)/code/(\d+)$", route)
            if m:
                name = unquote(m.group(1))
                iter_n = int(m.group(2))
                run_dir = _resolve_run(self, workspace, name)
                if run_dir is None:
                    return
                src_path = run_dir / f"figure_iter{iter_n}.py"
                if not src_path.is_file():
                    self.send_error(404, f"no source for iter {iter_n}")
                    return
                from figcopy_static.highlight import highlight_python
                html = highlight_python(src_path.read_text(errors="replace"))
                # Send as HTML fragment (the Export Code modal injects it
                # directly via innerHTML).
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            # /api/runs/<name>/pdf — stream the run's vector PDF as an
            # attachment download.
            #
            # Three tiers of source, in order:
            # 1. figure.pdf written by the agent during the iter loop
            #    (some skills produce both .png and .pdf; preferred).
            # 2. selection.md → derive shipped iter N → re-render
            #    figure_iter<N>.py to vector PDF via render_iter_pdf,
            #    then symlink/copy to figure.pdf for the next request.
            # 3. fall back to the latest iter with a .py on disk
            #    (covers auto-finalize where selection.md may name an
            #    iter but figure.pdf was never produced).
            m = re.match(r"^/api/runs/([^/]+)/pdf$", route)
            if m:
                name = unquote(m.group(1))
                run_dir = _resolve_run(self, workspace, name)
                if run_dir is None:
                    return
                pdf_path = run_dir / "figure.pdf"
                if not pdf_path.is_file():
                    # Try to derive the shipped iter from selection.md.
                    shipped_iter = detect_selected_iter(run_dir)
                    if shipped_iter is None:
                        # Last resort: latest iter with a .py.
                        for n in reversed(discover_iters(run_dir)):
                            if (run_dir / f"figure_iter{n}.py").is_file():
                                shipped_iter = n
                                break
                    if shipped_iter is None:
                        self.send_error(
                            404,
                            "no shipped figure and no iter source to "
                            "render a PDF from",
                        )
                        return
                    try:
                        iter_pdf = render_iter_pdf(run_dir, shipped_iter)
                    except FileNotFoundError:
                        self.send_error(
                            404,
                            f"figure_iter{shipped_iter}.py missing — "
                            "cannot render vector PDF",
                        )
                        return
                    except RuntimeError as e:
                        self.send_error(500, f"PDF render failed: {e}")
                        return
                    # Promote the rendered PDF to the canonical
                    # figure.pdf so subsequent requests are cache hits.
                    try:
                        import shutil as _shutil
                        _shutil.copyfile(iter_pdf, pdf_path)
                    except OSError:
                        # If we can't copy, just serve the iter PDF
                        # directly; the next request will re-render.
                        pdf_path = iter_pdf
                data = pdf_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                # ASCII-only filename so we don't have to negotiate
                # RFC 5987 filename* encoding for the slugified name.
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{name}.pdf"',
                )
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            # /api/runs/<name>/iter/<N>/pdf — re-execute the iter's
            # matplotlib script with a savefig monkeypatch so we get a
            # TRUE vector PDF (selectable text, infinite zoom). Previous
            # behavior wrapped the PNG inside a PDF via Pillow, which
            # was useless for paper submissions — that's the bug the
            # user explicitly called out.
            m = re.match(r"^/api/runs/([^/]+)/iter/(\d+)/pdf$", route)
            if m:
                name = unquote(m.group(1))
                iter_n = int(m.group(2))
                run_dir = _resolve_run(self, workspace, name)
                if run_dir is None:
                    return
                try:
                    pdf_path = render_iter_pdf(run_dir, iter_n)
                except FileNotFoundError:
                    self.send_error(
                        404,
                        f"figure_iter{iter_n}.py not found — "
                        "vector PDF needs the source script",
                    )
                    return
                except RuntimeError as e:
                    self.send_error(500, f"PDF render failed: {e}")
                    return
                data = pdf_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{name}-iter{iter_n}.pdf"',
                )
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            # /static/<name>/<rest>  — static file inside the run
            m = re.match(r"^/static/([^/]+)/(.+)$", route)
            if m:
                name, rest = unquote(m.group(1)), unquote(m.group(2))
                run_root = _resolve_run(self, workspace, name)
                if run_root is None:
                    return
                fp = (run_root / rest).resolve()
                # path traversal guard for the rest portion (the helper
                # only validated the run-name portion).
                if not path_inside(fp, run_root):
                    self.send_error(403)
                    return
                if not fp.is_file():
                    self.send_error(404)
                    return
                self._send_file(fp)
                return
            # ─── Phase 3: chat history (REST) ─────────────────────
            # GET /api/runs/<name>/chat/<set_id> → JSON array of
            # chat.jsonl entries filtered to set_id, for rehydration.
            m = re.match(r"^/api/runs/([^/]+)/chat/([0-9a-f]{8})$", route)
            if m:
                name = unquote(m.group(1))
                set_id = m.group(2)
                run_dir = _resolve_run(self, workspace, name)
                if run_dir is None:
                    return
                from figcopy_runner import chat_log
                chat_log.recover_orphan_refines(run_dir, set_id=set_id)
                entries = chat_log.read_turns(run_dir, set_id=set_id)
                # Rewrite relative image_url → absolute /static/... URL.
                for e in entries:
                    iu = e.get("image_url")
                    if isinstance(iu, str) and not iu.startswith("/"):
                        e["image_url"] = (
                            f"/static/{quote(name, safe='')}/"
                            f"{quote(iu, safe='')}"
                        )
                self._send_json(entries)
                return
            # ─── Phase 3: chat list ───────────────────────────────
            # GET /api/runs/<name>/chats → list of {set_id,
            # baseline_iters, turn_count, last_ts}
            m = re.match(r"^/api/runs/([^/]+)/chats$", route)
            if m:
                name = unquote(m.group(1))
                run_dir = _resolve_run(self, workspace, name)
                if run_dir is None:
                    return
                from figcopy_runner import chat_log
                self._send_json(chat_log.list_set_ids(run_dir))
                return
            # ─── Phase 3: SSE — chat stream ──────────────────────
            # GET /api/runs/<name>/chat/<set_id>/stream → text/event-stream
            m = re.match(r"^/api/runs/([^/]+)/chat/([0-9a-f]{8})/stream$",
                         route)
            if m:
                name = unquote(m.group(1))
                set_id = m.group(2)
                run_dir = _resolve_run(self, workspace, name)
                if run_dir is None:
                    return
                from urllib.parse import parse_qs
                qs = self.path.split("?", 1)[1] if "?" in self.path else ""
                parsed = parse_qs(qs)
                replay = parsed.get("replay", ["1"])[0] != "0"
                self._serve_sse_stream(
                    run_dir, f"refine:{set_id}", replay=replay,
                )
                return
            # ─── Phase 3: SSE — iter stream ──────────────────────
            # GET /api/runs/<name>/iter/stream → text/event-stream for
            # Step-1 loop events (text deltas, tool calls,
            # iter_complete, turn_end).
            m = re.match(r"^/api/runs/([^/]+)/iter/stream$", route)
            if m:
                name = unquote(m.group(1))
                run_dir = _resolve_run(self, workspace, name)
                if run_dir is None:
                    return
                self._serve_sse_stream(run_dir, "iter")
                return
            self.send_error(404)

        def _send_html(self, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, obj: dict) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json_error(
            self,
            status: int,
            code: str,
            message: str,
            **extra,
        ) -> None:
            body = json.dumps({
                "error": {
                    "code": code,
                    "message": message,
                    **extra,
                },
            }).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, fp: Path) -> None:
            ctype = self.guess_type(str(fp))
            data = fp.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _serve_sse_stream(
            self,
            workdir: Path,
            slot: str,
            *,
            replay: bool = True,
        ) -> None:
            """Stream SessionEvents for (workdir, slot) over Server-Sent
            Events. Honors Last-Event-ID for reconnect replay; pings
            the client every 15s of idle to keep proxies happy.

            Stdlib-only — writes ``id:\\n event:\\n data:\\n\\n`` framing
            directly to ``self.wfile``. Per-request thread (from
            ThreadingMixIn) is dedicated to this stream until the
            client disconnects.
            """
            from figcopy_runner import event_bus
            try:
                last_event_id = int(
                    self.headers.get("Last-Event-ID", "0") or "0"
                )
            except ValueError:
                last_event_id = 0

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            bus = event_bus.get_bus()

            def _write_payload(payload: dict) -> bool:
                """Returns False if the client has disconnected."""
                try:
                    body = (
                        f"id: {payload['seq']}\n"
                        f"event: {payload['type']}\n"
                        f"data: {json.dumps(payload['data'])}\n\n"
                    ).encode("utf-8")
                    self.wfile.write(body)
                    self.wfile.flush()
                    return True
                except (BrokenPipeError, ConnectionResetError,
                        ConnectionAbortedError, OSError):
                    return False

            # Send an initial comment to flush headers so the browser's
            # EventSource resolves ready-state quickly.
            try:
                self.wfile.write(b": ok\n\n")
                self.wfile.flush()
            except Exception:
                return

            # Order matters: subscribe BEFORE replay so events that fire
            # between the two calls don't get dropped on the floor (the
            # bus would add them to the buffer, but neither the replay
            # generator [snapshot taken before publish] nor the live
            # consumer [subscription not yet active] would see them).
            # After subscribe + replay, we dedupe live events by seq —
            # any seq already streamed by the replay pass is skipped.
            import queue as _queue
            q = bus.subscribe(workdir, slot)
            try:
                streamed_seqs: set[int] = set()
                # PR #25 round-1 finding #5: ``?replay=0`` is a
                # post-hydration optimization for the INITIAL
                # connection (the page already rendered the assistant
                # turn from REST, no need to re-stream the historical
                # events). On a browser auto-reconnect the EventSource
                # reuses the same URL but ALSO sends Last-Event-ID; if
                # we ignored replay there too, every event published
                # while the connection was down would be permanently
                # lost. Treat a non-zero Last-Event-ID as an explicit
                # caller-supplied resume cursor that overrides the
                # query-string suppression. See ``should_replay_sse``.
                if should_replay_sse(
                    replay_query=replay, last_event_id=last_event_id,
                ):
                    for payload in bus.replay(
                        workdir, slot, since_seq=last_event_id,
                    ):
                        seq = payload.get("seq")
                        if isinstance(seq, int):
                            streamed_seqs.add(seq)
                        if not _write_payload(payload):
                            return
                while True:
                    try:
                        payload = q.get(timeout=15)
                    except _queue.Empty:
                        # Idle — keepalive ping so proxies don't close.
                        try:
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                            continue
                        except (BrokenPipeError, ConnectionResetError,
                                ConnectionAbortedError, OSError):
                            return
                    seq = payload.get("seq")
                    # Skip live events already delivered by the replay
                    # pass (only happens for events fired between the
                    # subscribe() call and the replay snapshot — both
                    # paths see the event; replay wins to preserve
                    # ordering, the live copy is suppressed).
                    if isinstance(seq, int) and seq in streamed_seqs:
                        continue
                    if not _write_payload(payload):
                        return
            finally:
                bus.unsubscribe(workdir, slot, q)

    url = f"http://{args.host}:{args.port}/"
    print(f"FigMirror workspace at {workspace}")
    print(f"  → {url}")
    print(f"  runs detected: {len(discover_runs(workspace))}")
    print("(ctrl-c to stop)")
    if not args.no_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        with ThreadingReusableTCPServer((args.host, args.port), WorkspaceHandler) as srv:
            srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("workdir", type=Path, nargs="?",
                    help="path to a single FigMirror workdir (single-run mode)")
    ap.add_argument("--workspace", type=Path,
                    help="path to a workspace dir holding many runs (web-app mode); "
                         "mutually exclusive with positional workdir")
    ap.add_argument("--no-serve", action="store_true",
                    help="just write the HTML, don't start a server")
    ap.add_argument("--host", default=DEFAULT_HOST,
                    help="host/interface to bind (default: %(default)s)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true",
                    help="don't auto-open browser")
    ap.add_argument("--upload", action="store_true",
                    help="upload self-contained HTML to a HuggingFace Space and "
                         "print a shareable URL (requires huggingface_hub auth)")
    ap.add_argument("--space", default="zcahjl3/figcopy-taxonomy-gallery",
                    help="target HF Space repo (default: %(default)s)")
    ap.add_argument("--no-watch", action="store_true",
                    help="disable interactive features (live refresh, lightbox)")
    # Runner backend selection. Default codex; mock for offline dev.
    # --mock is kept as a deprecated alias for --backend mock.
    ap.add_argument("--backend",
                    choices=("mock", "codex", "claude"),
                    default=None,
                    help="runner backend (default: codex; use 'mock' "
                         "for offline dev with no CLI prerequisites)")
    ap.add_argument("--mock", action="store_true",
                    help="DEPRECATED alias for --backend mock")
    args = ap.parse_args()

    if args.workspace and args.workdir:
        print("error: pass either workdir (single-run) or --workspace (multi-run), not both",
              file=sys.stderr)
        return 1
    if not args.workspace and not args.workdir:
        print("error: pass a workdir, or --workspace <dir> for web-app mode",
              file=sys.stderr)
        return 1

    if args.workspace:
        return run_workspace(args)

    workdir = args.workdir.resolve()
    if not workdir.exists():
        # create empty scaffold so the viewer can start before the Codex skill runs
        (workdir / "inputs").mkdir(parents=True, exist_ok=True)
        print(f"created empty workdir at {workdir} (with inputs/ subdir)")
    elif not workdir.is_dir():
        print(f"error: {workdir} exists but is not a directory", file=sys.stderr)
        return 1

    if args.upload:
        # Upload-only path: render with inlined images and push to HF Space.
        html_inline = render_inline(workdir)
        print(f"rendered self-contained HTML ({len(html_inline) // 1024} KB), uploading…")
        url = upload_to_hf(html_inline, workdir, args.space)
        print(f"\n  ✓ shareable URL:\n    {url}\n")
        out = workdir.parent / f"{PAGE_NAME[:-5]}_inline.html"
        out.write_text(html_inline, encoding="utf-8")
        print(f"  local copy:    {out}")
        return 0

    interactive = not args.no_watch
    # When --no-serve, the page is written to disk and opened directly
    # (file://) — there's no /static-ui/ handler, so we inline the CSS
    # instead of pointing at a route that won't exist. The local-server
    # path below uses the route as before.
    html = render_html(workdir, interactive=interactive,
                       inline_assets=args.no_serve)
    out = workdir.parent / PAGE_NAME
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({len(html) // 1024} KB) — interactive={interactive}")

    if args.no_serve:
        return 0

    page_path = "/" + PAGE_NAME
    state_path = "/_state.json"

    class DynamicHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            # Strip query string for routing
            route = self.path.split("?", 1)[0]
            if _serve_static_ui(self, route):
                return
            if route in ("/", page_path):
                body = render_html(workdir, interactive=interactive).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if route == state_path:
                body = json.dumps(build_run_state(workdir)).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            # Serve only files inside the selected workdir. Do not fall back to
            # SimpleHTTPRequestHandler's parent-directory file serving.
            prefix = f"/{workdir.name}/"
            if route.startswith(prefix):
                rel = unquote(route[len(prefix):])
                fp = (workdir / rel).resolve()
                if not path_inside(fp, workdir):
                    self.send_error(403)
                    return
                if not fp.is_file():
                    self.send_error(404)
                    return
                ctype = self.guess_type(str(fp))
                data = fp.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_error(404)

    url = f"http://{args.host}:{args.port}{page_path}"
    print(f"serving {workdir} → {url}")
    if interactive:
        print("  • lightbox: click any iter image; press T to toggle reference; Esc to close")
        print("  • live refresh: page polls every 3s and reloads when a new iter appears")
    print("(ctrl-c to stop)")

    if not args.no_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        with ThreadingReusableTCPServer((args.host, args.port), DynamicHandler) as srv:
            srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
