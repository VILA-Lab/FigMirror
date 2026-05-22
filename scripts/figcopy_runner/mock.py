"""MockRunner — synthesizes plausible iter files into a workdir on a
timer, driving the UI without spawning any real agent backend (codex,
claude, …). One mock serves all real backends because the contract
they all honor is identical: write iter files + status sidecar into
the workdir.

Per phase2-webui-workpanel/design.md (D3, D4):

    Keeps UI dev decoupled from codex orchestration concerns. Phase 3
    swaps the mock body for `subprocess.Popen` while preserving the
    runner interface.

How it works:

1. ``start(workdir)`` spawns a daemon thread, registers in-memory
   state, writes ``status.json`` with ``state=running``, returns the
   run id immediately.

2. The thread walks ``figcopy_static/mock_iters/iter0..iter4``
   (sorted), copying each directory's generic-named files into the
   workdir with iter-numbered names:

       mock_iters/iter<N>/img.png    →  workdir/img_iter<N>.png
       mock_iters/iter<N>/code.py    →  workdir/figure_iter<N>.py
       mock_iters/iter<N>/notes.md   →  workdir/notes_iter<N>.md
       mock_iters/iter<N>/audit.json →  workdir/audit_iter<N>.json

   Each file is written via ``.tmp`` + ``Path.replace`` (POSIX rename)
   so the server's poll-disk loop never observes a half-written file.

3. Sleeps a randomized 4–8s between iters to simulate codex latency.

4. After copying, parses the iter's audit.json. If the verdict is
   ``ship`` (or after exhausting all mock iters or hitting
   ``max_iters``), promotes the iter's image to ``figure.png`` and
   writes ``status.json`` with ``state=shipped``. If the loop ends
   without a ship verdict, writes ``state=failed``.

Status semantics — a running mock will emit:

    iter 0:  running, current_iter=0  →  files appear  →  4-8s sleep
    iter 1:  running, current_iter=1  →  ...
    ...
    iter K:  running, current_iter=K  →  audit.verdict=ship detected
    →  shipped, current_iter=K  +  figure.png

The 4-8s gradient is intentional: earlier iters land faster (the
visual loop feels responsive), later iters land slower (mimicking
codex's typical "thinking gets slower as the prompt context grows").
"""

from __future__ import annotations

import json
import random
import shutil
import threading
import time
from pathlib import Path
from typing import Optional

from . import chat_log, event_bus, events
from .adjustments_to_prose import to_prose
from .interface import (
    compute_set_id,
    read_sessions,
    read_status_sidecar,
    write_sessions,
)


_DEFAULT_MOCK_ITERS_DIR = (
    Path(__file__).resolve().parent.parent / "figcopy_static" / "mock_iters"
)

_FILE_MAP = {
    "img.png":    "img_iter{n}.png",
    "code.py":    "figure_iter{n}.py",
    "notes.md":   "notes_iter{n}.md",
    "audit.json": "audit_iter{n}.json",
}


def _atomic_copy(src: Path, dst: Path) -> None:
    """Copy src → dst atomically: write to dst.tmp, then rename.

    POSIX rename is atomic; the server's poll-disk loop will see either
    the old contents (if any) or the new contents, never partial.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copyfile(src, tmp)
    tmp.replace(dst)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _format_value(v: object) -> str:
    """Render an rcParam value tightly for the overlay (e.g. 13 not 13.0)."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


# TTF font candidates for the overlay annotation card. We pick the
# first one that exists. DejaVu ships with most Linuxen + Pillow's
# vendored builds; Liberation is the RHEL/Fedora alternative.
_TITLE_TTF_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",  # Pillow's vendored copy
)
_BODY_TTF_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "DejaVuSansMono.ttf",
)


def _load_ttf(candidates: tuple[str, ...], *, size: int):
    """First-hit TTF loader; returns None if every candidate fails."""
    from PIL import ImageFont
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except (OSError, IOError):
            continue
    return None


def _render_refine_image(template_path: Path, dst_path: Path, *,
                         delta: dict, prev_snapshot: dict,
                         refine_n: int) -> None:
    """Open the template image, overlay a delta annotation card in the
    bottom-right, save atomically to dst_path.

    The card shape:

        ┌─────────────────────────────┐
        │ ◆ refine #N                 │
        ├─────────────────────────────┤
        │ font.size       11 → 13     │
        │ axes.labelsize  12 → 14     │
        └─────────────────────────────┘

    Each delta entry shows the prev value when known (from MockRunner's
    accumulated state); first-time params show ``— → 13`` so the user
    can tell it's a new surface.
    """
    # Lazy import: PIL is in dev group + only needed when refine() runs.
    # Single-run mode + the rest of the runner work without it.
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(template_path).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Prefer a real TTF — DejaVu ships on most Linuxen and gives a
    # readable overlay; the body uses a mono variant for the
    # rcParam-name=value lines so the columns line up.
    title_font = _load_ttf(_TITLE_TTF_CANDIDATES, size=14) \
        or ImageFont.load_default()
    body_font = _load_ttf(_BODY_TTF_CANDIDATES, size=13) \
        or title_font

    # Compose lines.
    title = f"◆ refine #{refine_n}"
    if not delta:
        body_lines = ["(no rcParams change)"]
    else:
        body_lines = []
        for k, v in delta.items():
            prev = prev_snapshot.get(k)
            new_str = _format_value(v)
            if prev is None or prev == v:
                body_lines.append(f"{k}: {new_str}")
            else:
                body_lines.append(f"{k}: {_format_value(prev)} → {new_str}")

    # Card geometry — measure widest line via textbbox.
    pad_x = 12
    pad_y = 10
    line_h = 16
    title_h = 18
    sep_h = 8

    def measure(text: str, font) -> int:
        # textbbox returns (x0, y0, x1, y1).
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]

    text_widths = [measure(title, title_font)] + [
        measure(line, body_font) for line in body_lines
    ]
    card_w = min(int(max(text_widths)) + 2 * pad_x, img.width - 32)
    card_h = (
        pad_y + title_h + sep_h
        + line_h * len(body_lines)
        + pad_y
    )

    # Position bottom-right with 14px gutter.
    margin = 14
    x0 = max(margin, img.width - card_w - margin)
    y0 = max(margin, img.height - card_h - margin)
    x1, y1 = x0 + card_w, y0 + card_h

    # Card background — dark slate with a thin teal hairline (matches
    # the Plot Surface webui palette so the overlay reads as part of
    # the figcopy chrome).
    BG = (15, 23, 42, 232)         # slate-900 @ 91% alpha
    BORDER = (13, 148, 136, 220)   # teal-600
    TITLE_COL = (94, 234, 212, 255)  # teal-300 — heading
    BODY_COL = (226, 232, 240, 255)  # slate-200 — values
    SEP_COL = (45, 55, 72, 200)
    draw.rounded_rectangle(
        [x0, y0, x1, y1], radius=7,
        fill=BG, outline=BORDER, width=1,
    )
    # Title
    draw.text((x0 + pad_x, y0 + pad_y), title,
              fill=TITLE_COL, font=title_font)
    # Separator
    sep_y = y0 + pad_y + title_h + 2
    draw.line([(x0 + pad_x, sep_y), (x1 - pad_x, sep_y)],
              fill=SEP_COL, width=1)
    # Body
    cy = sep_y + sep_h
    for line in body_lines:
        draw.text((x0 + pad_x, cy), line, fill=BODY_COL, font=body_font)
        cy += line_h

    composed = Image.alpha_composite(img, overlay).convert("RGB")
    tmp = dst_path.with_name(dst_path.name + ".tmp")
    composed.save(tmp, format="PNG", optimize=True)
    tmp.replace(dst_path)


class MockRunner:
    """In-process mock runner. One instance per server process."""

    def __init__(self, mock_iters_dir: Optional[Path] = None,
                 *,
                 sleep_min: float = 4.0,
                 sleep_max: float = 8.0,
                 rng: Optional[random.Random] = None):
        self.mock_iters_dir = (mock_iters_dir or _DEFAULT_MOCK_ITERS_DIR).resolve()
        self.sleep_min = sleep_min
        self.sleep_max = sleep_max
        self._rng = rng or random.Random()
        # Keyed by resolved workdir Path. Contains:
        #   {"state": str, "current_iter": int|None, "cancelled": bool}
        self._states: dict[Path, dict] = {}
        self._lock = threading.Lock()

    # --- public Runner interface ---------------------------------------

    def start(self, workdir: Path, *, prompt: str = "",
              max_iters: int = 6, auto: bool = False) -> str:
        workdir = workdir.resolve()
        with self._lock:
            existing = self._states.get(workdir)
            if existing and existing.get("state") == "running":
                # Already running — refuse rather than silently doubling.
                raise RuntimeError(
                    f"MockRunner: {workdir.name} is already running"
                )
            self._states[workdir] = {
                "state": "running",
                "current_iter": None,
                "cancelled": False,
            }
        # First sidecar write announces "we're working on it" before any
        # iter file lands; this is what the workspace landing page will
        # surface as "running" via discover_runs in the first poll.
        self._write_sidecar(workdir, state="running", current_iter=None)
        threading.Thread(
            target=self._run, args=(workdir, max_iters, auto),
            daemon=True, name=f"mock-runner:{workdir.name}",
        ).start()
        return workdir.name

    def status(self, workdir: Path) -> dict:
        workdir = workdir.resolve()
        with self._lock:
            entry = self._states.get(workdir)
        if entry:
            out = {"state": entry["state"]}
            if entry.get("current_iter") is not None:
                out["current_iter"] = entry["current_iter"]
            return out
        # Fall back to the on-disk sidecar so a server restart still
        # surfaces the right status to a returning client.
        return read_status_sidecar(workdir) or {"state": "idle"}

    def cancel(self, workdir: Path, *, slot: str = "iter") -> None:
        # MockRunner refines are synchronous (no subprocess to signal),
        # so a "refine:<set_id>" cancel is a no-op. Only "iter" actually
        # affects the running daemon thread.
        if slot != "iter":
            return
        workdir = workdir.resolve()
        with self._lock:
            entry = self._states.get(workdir)
            if entry:
                entry["cancelled"] = True

    # --- Step 2 refine (chat refinement) -------------------------------

    # Pattern → rcparams delta + review-text template. The mock is
    # deliberately small; the point is to drive the UI, not to be smart.
    # Real backends (codex / claude) will emit a structured
    # rcparams_delta.json sidecar per Phase 3.
    _REFINE_PATTERNS: list[tuple[tuple[str, ...], dict, str]] = [
        # (matchers — match if any substring is present in the message,
        #  rcparams delta to merge, human review template (formatted))
        (("axis label font", "axis label fonts", "axis labels larger",
          "axis label larger", "larger axis label"),
         {"axes.labelsize": 15},
         "Increased axes.labelsize to 15 so axis labels read larger "
         "without changing tick labels or other text."),
        (("字大", "bigger", "larger font", "字号大"),
         {"font.size": 13, "axes.labelsize": 14},
         "Bumped font.size 11 → 13 and axes.labelsize 12 → 14 — "
         "now closer to the reference's heavier label weight."),
        (("字小", "smaller font", "字号小"),
         {"font.size": 9},
         "Reduced font.size to 9. Reference uses denser type."),
        (("x 轴", "x-axis", "x axis", "x轴"),
         {"axes.labelsize": 14, "xtick.labelsize": 11},
         "Boosted x-axis labelsize and tick label size."),
        (("y 轴", "y-axis", "y axis", "y轴"),
         {"axes.labelsize": 14, "ytick.labelsize": 11},
         "Boosted y-axis labelsize and tick label size."),
        (("配色", "palette", "color"),
         {"axes.prop_cycle": "tableau-10"},
         "Switched palette to tableau-10 for closer parity with reference."),
        (("线条", "line width", "thicker", "粗"),
         {"lines.linewidth": 1.8},
         "Set lines.linewidth to 1.8 (was 1.0)."),
        (("legend", "图例"),
         {"legend.frameon": False},
         "Removed legend frame to match reference's frameless treatment."),
        (("tick", "刻度"),
         {"xtick.major.size": 4, "ytick.major.size": 4},
         "Tightened major tick lengths."),
    ]

    def refine(self, workdir: Path, *,
               baseline_iters: list[int],
               message: str | None = None,
               adjustments: dict | None = None) -> dict:
        """Step 2 turn. Synchronous; returns dict per Phase-3 contract:

            {"image_url": str, "rcparams_delta": dict, "review": str,
             "set_id": str, "seq": int}

        Multi-baseline / multi-turn semantics (Phase 3):

        - ``baseline_iters`` is a 1+ length list; the runner computes
          ``set_id = compute_set_id(...)`` and uses ``(workdir, set_id)``
          as the session key (chat history + accumulated state +
          mock session-id all index by set_id).
        - ``adjustments`` (structured direct-control payload) is
          translated to prose via ``adjustments_to_prose`` and
          combined with ``message``; the agent only ever sees one
          natural-language message.
        - Both user turn and assistant reply are appended atomically
          to ``{workdir}/chat.jsonl`` indexed by ``set_id``.
        - A mock session-id is recorded in ``{workdir}/sessions.json``
          so the helper code paths get exercised even on mock; real
          backends overwrite with a real session-id on turn 1.

        Image synthesis: opens the first baseline iter's
        ``img_iter<N>.png`` as the template, draws the latest delta
        as a small annotation card, saves atomically to
        ``refine_NNN.png``. Real backends generate the figure via the
        agent's own matplotlib calls.

        Pattern-match table at module level still drives the delta
        when ``adjustments`` is empty — this is offline-dev / CI fuel,
        not real intelligence.
        """
        if not baseline_iters:
            raise ValueError(
                "MockRunner.refine: baseline_iters must be non-empty"
            )
        workdir = workdir.resolve()
        set_id = compute_set_id(baseline_iters)
        # Prose form of the user's turn (what the agent would see).
        user_message = to_prose(adjustments, message)

        # Pick delta + review. ``adjustments`` is the explicit
        # direct-control path — echo it back as the delta. Otherwise
        # try the pattern-match table on the message body.
        if adjustments:
            delta = dict(adjustments)
            review = "Applied direct control changes: " + ", ".join(
                f"{k}={v}" for k, v in adjustments.items()
            ) + "."
        else:
            delta, review = self._match_message(message or "")

        # Bump refine counter + accumulator under (workdir, set_id).
        with self._lock:
            if workdir not in self._states:
                # Cold path (server restart or refine before any start).
                sidecar = read_status_sidecar(workdir)
                refine_count_seed = 0
                for existing in workdir.glob("refine_*.png"):
                    try:
                        idx = int(existing.stem.split("_", 1)[1])
                        refine_count_seed = max(refine_count_seed, idx)
                    except (ValueError, IndexError):
                        pass
                self._states[workdir] = {
                    "state": sidecar["state"] if sidecar else "idle",
                    "current_iter": (
                        sidecar.get("current_iter") if sidecar else None
                    ),
                    "cancelled": False,
                    "refine_count": refine_count_seed,
                }
            entry = self._states[workdir]
            n = entry.get("refine_count", 0) + 1
            entry["refine_count"] = n
            # Per-set accumulator. Cross-set state stays isolated.
            per_set = entry.setdefault("accumulated_rcparams_by_set", {})
            accumulated = dict(per_set.get(set_id, {}))
            prev_snapshot = {k: accumulated.get(k) for k in delta}
            accumulated.update(delta)
            per_set[set_id] = accumulated

        # Render the visible overlay using the first baseline as template.
        first_baseline = sorted(set(baseline_iters))[0]
        dst = workdir / f"refine_{n:03d}.png"
        template_img = workdir / f"img_iter{first_baseline}.png"
        try:
            _render_refine_image(
                template_img, dst,
                delta=delta, prev_snapshot=prev_snapshot, refine_n=n,
            )
        except Exception as e:
            print(f"[mock-runner:{workdir.name}] refine render failed: {e}",
                  flush=True)
            if template_img.exists():
                _atomic_copy(template_img, dst)

        # Write the structured outcome alongside the png, matching the
        # contract real runners will follow (see refine_prompt's output
        # contract section). This lets later refine_prompt history
        # selection see this turn.
        from .interface import atomic_write_text
        atomic_write_text(
            workdir / f"refine_{n:03d}.json",
            json.dumps({
                "rcparams_delta": delta,
                "review": review,
                "baseline_iters": sorted(set(baseline_iters)),
            }, indent=2, sort_keys=True) + "\n",
        )

        # Persist a mock session-id in sessions.json so the multi-turn
        # helper code paths are exercised. Real backends will overwrite
        # this with an actual agent session-id on turn 1.
        sessions = read_sessions(workdir)
        if set_id not in sessions["refine"]:
            sessions["refine"][set_id] = f"mock-sid-{set_id}"
            write_sessions(workdir, sessions)

        # Append user + assistant turns to chat.jsonl.
        chat_log.append_turn(
            workdir,
            role="user",
            content=user_message,
            set_id=set_id,
            baseline_iters=sorted(set(baseline_iters)),
            adjustments=adjustments or None,
        )

        # Publish the refine_complete event (mock exercises the bus
        # path too — real backends emit token-streaming events along
        # the way; mock emits only the final event).
        bus = event_bus.get_bus()
        ev = events.RefineCompleteEvent(data={
            "set_id": set_id,
            "image_url": f"refine_{n:03d}.png",
            "rcparams_delta": delta,
            "review": review,
            "refine_idx": n,
        })
        seq = bus.publish(workdir, f"refine:{set_id}", ev)

        chat_log.append_turn(
            workdir,
            role="assistant",
            content=review,
            set_id=set_id,
            baseline_iters=sorted(set(baseline_iters)),
            image_url=f"refine_{n:03d}.png",
            rcparams_delta=delta,
            review=review,
            refine_idx=n,
            seq=seq,
        )

        return {
            "image_url": f"refine_{n:03d}.png",
            "rcparams_delta": delta,
            "review": review,
            "set_id": set_id,
            "seq": seq,
        }

    @classmethod
    def _match_message(cls, message: str) -> tuple[dict, str]:
        """Pattern-match a user message → (delta, review). Falls back
        to a generic acknowledgement on no-match."""
        if not message:
            return ({}, "(no change)")
        m = message.lower()
        for matchers, delta, review in cls._REFINE_PATTERNS:
            if any(needle.lower() in m for needle in matchers):
                return (dict(delta), review)
        return (
            {},
            "Acknowledged. (Mock backend has no specific delta for "
            "this prompt; the real codex / claude backend will produce "
            "a structured rcparams_delta in Phase 3.)",
        )

    # --- internals ------------------------------------------------------

    def _run(self, workdir: Path, max_iters: int, auto: bool) -> None:
        try:
            mock_iter_dirs = self._list_mock_iter_dirs()
        except FileNotFoundError as e:
            print(f"[mock-runner:{workdir.name}] {e}", flush=True)
            self._set_state(workdir, "failed", None)
            return

        n = len(mock_iter_dirs) if auto else min(max_iters, len(mock_iter_dirs))
        if n == 0:
            self._set_state(workdir, "failed", None)
            return

        shipped_at: Optional[int] = None

        for i in range(n):
            # Cancellation check at the top of each iter loop.
            if self._is_cancelled(workdir):
                self._set_state(workdir, "failed", i)
                return

            # Sleep before producing this iter (gradient: faster early,
            # slower later, mirroring codex thinking-time growth).
            if n > 1:
                t = i / (n - 1)  # 0..1
            else:
                t = 0.0
            delay = self.sleep_min + (self.sleep_max - self.sleep_min) * t
            # Apply small jitter so timing isn't lockstep-synthetic.
            delay = delay * self._rng.uniform(0.9, 1.1)
            time.sleep(delay)

            if self._is_cancelled(workdir):
                self._set_state(workdir, "failed", i)
                return

            # Update status BEFORE writing files so a fast-polling
            # client sees current_iter=i + the new files together.
            self._set_state(workdir, "running", i)
            self._copy_iter_files(mock_iter_dirs[i], workdir, i)

            # Detect early ship.
            audit = self._read_audit(workdir, i)
            verdict = audit.get("fidelity", {}).get("verdict") if audit else None
            if verdict == "ship":
                shipped_at = i
                break

        if shipped_at is not None:
            self._finalize_shipped(workdir, shipped_at)
            self._set_state(workdir, "shipped", shipped_at)
        else:
            # Ran out of iters without a ship verdict — mark failed
            # rather than idle so the user can see the run terminated.
            self._set_state(workdir, "failed", n - 1)

    # --- helpers --------------------------------------------------------

    def _list_mock_iter_dirs(self) -> list[Path]:
        if not self.mock_iters_dir.is_dir():
            raise FileNotFoundError(
                f"mock_iters/ not found at {self.mock_iters_dir}; "
                "run scripts/figcopy_static/mock_iters/_build.py to stage it"
            )
        dirs = sorted(
            p for p in self.mock_iters_dir.iterdir()
            if p.is_dir() and p.name.startswith("iter")
        )
        return dirs

    def _copy_iter_files(self, src_dir: Path, workdir: Path, n: int) -> None:
        for src_name, dst_template in _FILE_MAP.items():
            src = src_dir / src_name
            if not src.exists():
                continue
            _atomic_copy(src, workdir / dst_template.format(n=n))

    def _read_audit(self, workdir: Path, n: int) -> Optional[dict]:
        p = workdir / f"audit_iter{n}.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except Exception:
            return None

    def _finalize_shipped(self, workdir: Path, n: int) -> None:
        # Promote the shipping iter's canonical artifacts. Atomic.
        img_src = workdir / f"img_iter{n}.png"
        if img_src.exists():
            _atomic_copy(img_src, workdir / "figure.png")
        code_src = workdir / f"figure_iter{n}.py"
        if code_src.exists():
            _atomic_copy(code_src, workdir / "figure.py")
        # Write a minimal selection.md if the user hasn't already.
        sel = workdir / "selection.md"
        if not sel.exists():
            _atomic_write_text(sel,
                f"# Selection notes\n\n"
                f"Selected: **iter {n}** (MockRunner verdict=ship).\n\n"
                f"_Generated by MockRunner; replace with reviewer\n"
                f"narrative when integrating a real backend (codex/claude)._\n"
            )

    def _is_cancelled(self, workdir: Path) -> bool:
        with self._lock:
            entry = self._states.get(workdir)
            return bool(entry and entry.get("cancelled"))

    def _set_state(self, workdir: Path, state: str,
                   current_iter: Optional[int]) -> None:
        with self._lock:
            entry = self._states.setdefault(workdir, {})
            entry["state"] = state
            entry["current_iter"] = current_iter
        self._write_sidecar(workdir, state=state, current_iter=current_iter)

    def _write_sidecar(self, workdir: Path, *, state: str,
                       current_iter: Optional[int]) -> None:
        try:
            data: dict = {"state": state}
            if current_iter is not None:
                data["current_iter"] = current_iter
            _atomic_write_text(
                workdir / "status.json",
                json.dumps(data, indent=2) + "\n",
            )
        except Exception as e:
            # Best-effort sidecar; in-memory state is authoritative.
            print(f"[mock-runner:{workdir.name}] sidecar write failed: {e}",
                  flush=True)
