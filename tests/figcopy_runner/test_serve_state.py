from __future__ import annotations

import json
import re

import pytest

import figcopy_serve


def test_should_replay_sse_initial_connection_with_default_replay():
    """Initial connection (Last-Event-ID absent → 0) with the default
    ``?replay=1`` query MUST trigger the historical replay pass."""
    assert figcopy_serve.should_replay_sse(
        replay_query=True, last_event_id=0,
    ) is True


def test_should_replay_sse_initial_connection_post_hydration_suppresses():
    """The post-hydration optimization: trajectory.js passes
    ``?replay=0`` when the page already rendered the historical chat
    via REST. On the INITIAL connection (no Last-Event-ID), suppress
    the replay pass to avoid re-streaming events the client already
    sees in chat.jsonl."""
    assert figcopy_serve.should_replay_sse(
        replay_query=False, last_event_id=0,
    ) is False


def test_should_replay_sse_reconnect_overrides_replay_zero():
    """PR #25 round-1 finding #5: on a browser auto-reconnect the
    EventSource reuses the original URL (so ``?replay=0`` sticks),
    but ALSO sends a non-zero ``Last-Event-ID``. Without overriding
    the suppression, every event published while the connection was
    down would be permanently lost. Pin that a non-zero
    ``Last-Event-ID`` overrides the post-hydration suppression — the
    client is explicitly asking to resume from a known cursor."""
    assert figcopy_serve.should_replay_sse(
        replay_query=False, last_event_id=42,
    ) is True


def test_should_replay_sse_reconnect_with_replay_one_still_replays():
    """Defensive: the reconnect path with the default ``?replay=1`` is
    a no-op for the new gate (it was already going to replay). Pin
    that we don't accidentally invert the predicate."""
    assert figcopy_serve.should_replay_sse(
        replay_query=True, last_event_id=42,
    ) is True


def test_build_run_state_handles_runner_reason(tmp_path):
    (tmp_path / "inputs").mkdir()
    (tmp_path / "img_iter0.png").write_bytes(b"png")
    (tmp_path / "status.json").write_text(
        json.dumps({
            "state": "failed",
            "current_iter": 0,
            "reason": "reviewer unavailable",
        }),
        encoding="utf-8",
    )

    state = figcopy_serve.build_run_state(tmp_path)

    assert state["status"] == "failed"
    assert state["current_iter"] == 0
    assert state["reason"] == "reviewer unavailable"
    assert state["iters"] == [{"i": 0, "img": True, "audit": False}]


def test_create_run_rejects_unavailable_backend_before_staging(tmp_path):
    form = {
        "run_name": "needs-claude",
        "backend": "claude",
        "ref": {"filename": "ref.png", "content": b"fake png"},
    }

    with pytest.raises(figcopy_serve.BackendUnavailable):
        figcopy_serve.create_run(
            tmp_path,
            form,
            available_backends={"codex", "mock"},
        )

    assert not (tmp_path / "needs-claude").exists()


def test_create_run_accepts_available_backend(tmp_path):
    form = {
        "run_name": "uses-claude",
        "backend": "claude",
        "ref": {"filename": "ref.png", "content": b"fake png"},
    }

    _name, run_dir, config = figcopy_serve.create_run(
        tmp_path,
        form,
        available_backends={"claude"},
    )

    assert config["backend"] == "claude"
    assert (run_dir / "config.json").is_file()
    assert not (run_dir / "prompts").exists()
    assert not (run_dir / "inputs" / "aesthetic-library.md").exists()


def test_create_run_does_not_stage_prompt_bundle(tmp_path):
    """The Web UI runner must exercise the installed FigMirror skill.

    It prepares only user inputs and config; prompt/reference files are
    loaded through the native `$figmirror` skill invocation in the
    Codex/Claude backend.
    """
    form = {
        "run_name": "surface",
        "backend": "codex",
        "prompt": "Make a 3D surface plot from this CSV.",
        "ref": {"filename": "ref.png", "content": b"fake png"},
        "data": {
            "filename": "points.csv",
            "content": b"x,y,z\n0,0,1\n1,0,2\n",
        },
    }

    _name, run_dir, config = figcopy_serve.create_run(
        tmp_path,
        form,
        available_backends={"codex"},
    )

    assert config["backend"] == "codex"
    assert "use_3d_insert" not in config
    assert not (run_dir / "prompts").exists()
    assert not (run_dir / "tools").exists()


def test_render_landing_disables_unavailable_backend(tmp_path):
    html = figcopy_serve.render_landing(
        tmp_path,
        available_backends={"codex"},
        default_backend="codex",
    )

    # codex is the default and available → preselected, not disabled.
    assert "<option value='codex' selected>Codex CLI</option>" in html
    # claude and mock are both unavailable → disabled, no `selected`.
    assert (
        "<option value='claude' disabled data-unavailable='1' "
        "title='CLI not found on PATH'>Claude Code</option>"
    ) in html
    assert (
        "<option value='mock' disabled data-unavailable='1' "
        "title='CLI not found on PATH'>MockRunner</option>"
    ) in html


def test_render_landing_renders_mock_option_when_available(tmp_path):
    """KNOWN_BACKENDS includes 'mock'; the dropdown must surface it.

    Pre-fix the dropdown hardcoded only 'codex' and 'claude', so a
    server started with `--backend mock` (offline dev) had no way to
    select mock from the UI even though `create_run` accepted it.
    """
    html = figcopy_serve.render_landing(
        tmp_path,
        available_backends={"mock"},
        default_backend="mock",
    )

    assert "<option value='mock' selected>MockRunner</option>" in html


def test_render_iter_pdf_falls_back_to_live_matplotlib_figure(tmp_path):
    """Claude-generated iter scripts may save only ``img_iterN.png``.

    The PDF endpoint still needs to produce a vector PDF from the same
    script instead of requiring the script to know the canonical
    ``figure.py`` filename convention.
    """
    (tmp_path / "figure_iter0.py").write_text(
        """
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["pdf.fonttype"] = 42
fig, ax = plt.subplots(figsize=(2, 1.5), dpi=100)
ax.plot([0, 1], [0, 1], marker="o")
fig.savefig("img_iter0.png", format="png")
""".lstrip(),
        encoding="utf-8",
    )

    pdf_path = figcopy_serve.render_iter_pdf(tmp_path, 0)

    assert pdf_path == tmp_path / "img_iter0.pdf"
    assert pdf_path.read_bytes().startswith(b"%PDF")


def test_render_landing_picks_available_backend_when_default_missing(tmp_path):
    """Regression: when default_backend is unavailable, the form must
    pre-select an *available* backend, not emit `selected disabled`
    (browsers don't submit disabled fields, so a deploy whose default
    backend's CLI is missing previously produced an unsubmittable form
    that fell through to server-side defaulting on POST).
    """
    html = figcopy_serve.render_landing(
        tmp_path,
        available_backends={"mock"},
        default_backend="codex",
    )

    # Disabled options must not also carry `selected` — the invariant
    # is enforced in _render_backend_option.
    assert "selected disabled" not in html
    assert "disabled selected" not in html
    # mock is the only available backend → it is the one preselected.
    assert "<option value='mock' selected>MockRunner</option>" in html
    # codex / claude must be present-but-disabled (so the user can see
    # what's missing) — and crucially without `selected`. Extract the
    # actual <option> fragments from the rendered html and assert the
    # absence of `selected` on each. The earlier substring asserts cover
    # the contiguous `selected disabled` shape; this catches a regression
    # that would emit `<option value='codex' selected ... disabled>` with
    # any intervening attributes.
    codex_opt_match = re.search(
        r"<option value='codex'[^>]*>", html
    )
    claude_opt_match = re.search(
        r"<option value='claude'[^>]*>", html
    )
    assert codex_opt_match is not None, "codex option must be rendered"
    assert claude_opt_match is not None, "claude option must be rendered"
    assert "disabled" in codex_opt_match.group(0)
    assert "disabled" in claude_opt_match.group(0)
    assert "selected" not in codex_opt_match.group(0)
    assert "selected" not in claude_opt_match.group(0)


def test_build_run_state_marks_unavailable_backend_failed(tmp_path):
    """Regression for finding #3: GET /r/<name>/_state.json never
    invoked runner_for, so a run pre-staged with backend=claude on a
    host without the claude CLI rendered as a permanently spinning
    'idle'/'queued' card. With runner_for threaded through, the run
    is reported failed with the BackendUnavailable text in `reason`.
    """
    (tmp_path / "inputs").mkdir()

    def fake_runner_for(_workdir):
        raise figcopy_serve.BackendUnavailable(
            "run X is configured for backend 'claude', but this server "
            "has only ['mock']. Install the 'claude' CLI on this host..."
        )

    state = figcopy_serve.build_run_state(
        tmp_path, runner_for=fake_runner_for
    )

    assert state["status"] == "failed"
    assert "claude" in state["reason"]
    assert "Install the 'claude' CLI" in state["reason"]


def test_discover_runs_marks_unavailable_backend_failed(tmp_path):
    """Sibling regression for /api/runs.json: workspace-level run list
    must also flip status to 'failed' for runs whose backend is
    missing on this host, otherwise the right-hand panel shows them
    spinning forever.
    """
    run_dir = tmp_path / "needs-claude"
    run_dir.mkdir()
    (run_dir / "inputs").mkdir()
    (run_dir / "config.json").write_text(
        json.dumps({"backend": "claude"}), encoding="utf-8"
    )

    def fake_runner_for(_workdir):
        raise figcopy_serve.BackendUnavailable(
            "run 'needs-claude' is configured for backend 'claude', but "
            "this server has only ['mock']."
        )

    runs = figcopy_serve.discover_runs(tmp_path, runner_for=fake_runner_for)

    assert len(runs) == 1
    assert runs[0]["name"] == "needs-claude"
    assert runs[0]["status"] == "failed"
    assert "claude" in (runs[0]["reason"] or "")
    # The backend tag must still come through so the UI badge renders.
    assert runs[0]["backend"] == "claude"


def test_runner_for_resolution_raises_when_backend_unavailable(tmp_path, monkeypatch):
    """Regression for finding #4: a pre-staged run whose config.json
    names a backend that's no longer on this host must raise
    BackendUnavailable at resolution time (not silently fall back to
    a different runner).

    Exercised by reconstructing the same `runner_for` closure that
    `run_workspace` builds, against an empty `runners` table — exactly
    the shape "the backend's CLI was uninstalled between staging and
    serve restart" produces.
    """
    run_dir = tmp_path / "stale-run"
    run_dir.mkdir()
    (run_dir / "config.json").write_text(
        json.dumps({"backend": "claude"}), encoding="utf-8"
    )

    # Build the same closure run_workspace builds — if the recorded
    # backend isn't a runner instance we have, but IS in
    # KNOWN_BACKENDS, raise BackendUnavailable. This pins the
    # resolution-time contract independently of whether the host has
    # any CLI installed.
    runners: dict[str, object] = {}
    available_backend_names: set[str] = set()
    default_backend = "codex"

    def runner_for(rd):
        try:
            cfg = json.loads(
                (rd / "config.json").read_text(encoding="utf-8")
            )
            chosen = cfg.get("backend")
        except Exception:
            chosen = None
        if chosen in runners:
            return runners[chosen]
        if chosen in figcopy_serve.KNOWN_BACKENDS:
            raise figcopy_serve.BackendUnavailable(
                f"run {rd.name!r} is configured for backend "
                f"{chosen!r}, but this server has only "
                f"{sorted(available_backend_names)}. Install the "
                f"{chosen!r} CLI on this host..."
            )
        return runners[default_backend]

    with pytest.raises(figcopy_serve.BackendUnavailable) as exc_info:
        runner_for(run_dir)

    assert "claude" in str(exc_info.value)
    assert "Install" in str(exc_info.value)


@pytest.mark.parametrize("terminal_state", ["shipped", "failed", "cancelled"])
def test_build_run_state_preserves_terminal_status_when_backend_unavailable(
    tmp_path, terminal_state
):
    """Regression for round-2 finding #1: ``runner_for`` raising
    ``BackendUnavailable`` must NOT clobber a terminal disk-derived
    status (``shipped`` / ``failed`` / ``cancelled``).

    Concrete scenario: a run shipped yesterday on a host where the
    backend CLI is later uninstalled. ``status.json`` says
    ``shipped`` (or ``failed`` with the original reason, or
    ``cancelled``); the figure / partial outputs are still on disk and
    the user is browsing them — no backend needed. The pre-fix code
    unconditionally rewrote ``status="failed"`` and clobbered the
    authored ``reason`` with the misleading
    "backend X unavailable; install …" string.
    """
    (tmp_path / "inputs").mkdir()
    original_reason = (
        "reviewer protocol exited 17" if terminal_state == "failed" else None
    )
    sidecar: dict = {"state": terminal_state, "current_iter": 2}
    if original_reason is not None:
        sidecar["reason"] = original_reason
    (tmp_path / "status.json").write_text(
        json.dumps(sidecar), encoding="utf-8"
    )

    def fake_runner_for(_workdir):
        raise figcopy_serve.BackendUnavailable(
            "backend 'claude' unavailable; install the 'claude' CLI..."
        )

    state = figcopy_serve.build_run_state(
        tmp_path, runner_for=fake_runner_for
    )

    assert state["status"] == terminal_state, (
        f"runner_for must not override terminal status {terminal_state}"
    )
    assert state["reason"] == original_reason, (
        "authored reason must survive the runner_for call"
    )


@pytest.mark.parametrize("terminal_state", ["shipped", "failed", "cancelled"])
def test_discover_runs_preserves_terminal_status_when_backend_unavailable(
    tmp_path, terminal_state
):
    """Sibling regression to ``test_build_run_state_preserves_terminal_...``
    for the workspace-level run list (``/api/runs.json``).
    """
    run_dir = tmp_path / "shipped-yesterday"
    run_dir.mkdir()
    (run_dir / "inputs").mkdir()
    (run_dir / "config.json").write_text(
        json.dumps({"backend": "claude"}), encoding="utf-8"
    )
    (run_dir / "figure.png").write_bytes(b"png")
    sidecar: dict = {"state": terminal_state, "current_iter": 3}
    if terminal_state == "failed":
        sidecar["reason"] = "reviewer protocol exited 17"
    (run_dir / "status.json").write_text(
        json.dumps(sidecar), encoding="utf-8"
    )

    runner_for_calls: list = []

    def fake_runner_for(workdir):
        runner_for_calls.append(workdir)
        raise figcopy_serve.BackendUnavailable(
            "backend 'claude' unavailable; install the 'claude' CLI..."
        )

    runs = figcopy_serve.discover_runs(tmp_path, runner_for=fake_runner_for)

    assert len(runs) == 1
    assert runs[0]["status"] == terminal_state
    if terminal_state == "failed":
        assert runs[0]["reason"] == "reviewer protocol exited 17"
    # The override is gated on terminal status — runner_for must not
    # have been called at all (cheap optimisation: no need to probe a
    # backend whose verdict we won't use).
    assert runner_for_calls == [], (
        "runner_for must be skipped for terminal-status runs"
    )


def test_render_html_surfaces_failure_reason_when_backend_unavailable(tmp_path):
    """Regression for round-2 finding #2: trajectory page first-load.

    Pre-fix, a direct ``/r/<name>`` load of a run whose configured
    backend is unavailable rendered "Waiting for first iter…" from
    disk. The first ``_state.json`` poll then returned ``failed``,
    but ``trajectory.js`` treats the first response as its baseline
    and stops polling on terminal states — banner is never updated
    and the user has no actionable text.

    Threading ``runner_for`` into ``render_html`` lets the
    server-side first paint match the first poll: status_attr is
    flipped to ``failed`` and the banner carries the remediation
    text immediately.
    """
    (tmp_path / "inputs").mkdir()

    def fake_runner_for(_workdir):
        raise figcopy_serve.BackendUnavailable(
            "run 'X' is configured for backend 'claude', but this server "
            "has only ['mock']. Install the 'claude' CLI on this host..."
        )

    html = figcopy_serve.render_html(
        tmp_path, runner_for=fake_runner_for
    )

    # data-status drives trajectory.js's poll-init guard so it does
    # not start polling on a terminal-state first paint.
    assert "data-status='failed'" in html
    # The remediation text is what tells the user what's wrong; it
    # must appear in the visible status-text span, not just buried
    # in a tooltip. Single quotes in the message get HTML-escaped to
    # `&#x27;`, so check the substring without quotes.
    assert "Install the" in html
    assert "claude" in html
    assert "CLI on this host" in html


@pytest.mark.parametrize("terminal_state", ["shipped", "failed", "cancelled"])
def test_render_html_does_not_invoke_runner_for_on_terminal_runs(
    tmp_path, terminal_state
):
    """Mirror of ``test_discover_runs_preserves_terminal_status_...`` for
    ``render_html``. A terminal-status first paint must skip the
    backend probe so the user can browse a shipped figure on a host
    that no longer has the originating CLI.
    """
    (tmp_path / "inputs").mkdir()
    sidecar: dict = {"state": terminal_state, "current_iter": 1}
    if terminal_state == "failed":
        sidecar["reason"] = "reviewer protocol exited 17"
    (tmp_path / "status.json").write_text(
        json.dumps(sidecar), encoding="utf-8"
    )
    if terminal_state == "shipped":
        (tmp_path / "figure.png").write_bytes(b"png")

    runner_for_calls: list = []

    def fake_runner_for(workdir):
        runner_for_calls.append(workdir)
        raise figcopy_serve.BackendUnavailable(
            "backend 'claude' unavailable"
        )

    html = figcopy_serve.render_html(
        tmp_path, runner_for=fake_runner_for
    )

    assert runner_for_calls == [], (
        "runner_for must be skipped for terminal-status first paint"
    )
    assert f"data-status='{terminal_state}'" in html
    # The runner_for exception's text must NOT leak into the visible
    # status banner. Extract the status-text span and verify it carries
    # one of the disk-derived defaults, not the BackendUnavailable text.
    status_text_match = re.search(
        r"<span class='status-text'>([^<]*)</span>", html
    )
    assert status_text_match is not None
    status_text = status_text_match.group(1)
    assert "backend" not in status_text.lower()
    assert "unavailable" not in status_text.lower()


# ───────── Backend-availability probe (PR-27 round 1, Important #1) ─────────


def _which_factory(present: set[str]):
    """Return a `shutil.which`-compatible callable that reports only
    binaries in ``present`` as installed (returning a fake absolute
    path) and everything else as missing (returning None).
    """
    def _which(name):
        return f"/usr/bin/{name}" if name in present else None
    return _which


def test_backend_runtime_available_codex_requires_uv():
    """Real backends `codex` / `claude` invoke their CLI through
    `uv run --project <repo>`, so missing `uv` ⇒ first Stage-0 Popen
    raises `FileNotFoundError` AFTER status.json flipped to `running`,
    and the run hangs forever. The availability probe MUST refuse to
    advertise these backends when `uv` is missing, even if the agent
    CLI itself is installed (PR-27 Agent-A round-1 Important #1,
    3-angle agreement).
    """
    # Both CLIs present → backend reported available.
    assert figcopy_serve._backend_runtime_available(
        "codex", which=_which_factory({"codex", "uv"})
    ) is True
    assert figcopy_serve._backend_runtime_available(
        "claude", which=_which_factory({"claude", "uv"})
    ) is True

    # uv missing → backend MUST be reported unavailable, even though
    # `codex` / `claude` are on PATH.
    assert figcopy_serve._backend_runtime_available(
        "codex", which=_which_factory({"codex"})
    ) is False
    assert figcopy_serve._backend_runtime_available(
        "claude", which=_which_factory({"claude"})
    ) is False

    # Agent CLI missing → unavailable regardless of uv.
    assert figcopy_serve._backend_runtime_available(
        "codex", which=_which_factory({"uv"})
    ) is False
    assert figcopy_serve._backend_runtime_available(
        "claude", which=_which_factory({"uv"})
    ) is False

    # Both missing → unavailable.
    assert figcopy_serve._backend_runtime_available(
        "codex", which=_which_factory(set())
    ) is False


def test_backend_runtime_available_mock_never_needs_uv():
    """The mock backend is in-process; its availability must NOT
    depend on `uv` or any other external CLI."""
    assert figcopy_serve._backend_runtime_available(
        "mock", which=_which_factory(set())
    ) is True


def test_backend_runtime_available_unknown_backend_is_false():
    """An unknown backend name must report unavailable rather than
    raising — the caller pattern is `if available(name): instantiate`,
    and an exception there would crash startup."""
    assert figcopy_serve._backend_runtime_available(
        "totally-made-up", which=_which_factory({"totally-made-up", "uv"})
    ) is False
