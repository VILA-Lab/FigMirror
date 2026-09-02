from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_claude_skill.py"
SPEC = importlib.util.spec_from_file_location("install_claude_skill", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
install_claude_skill = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install_claude_skill)


def test_validate_source_matches_active_claude_bundle():
    install_claude_skill.validate_source()
    assert install_claude_skill.AGENT_NAMES == (
        "figmirror-drawer",
        "figmirror-reviewer",
    )


def test_default_target_prefers_native_config_dir(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    compat_home = tmp_path / "compat"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("CLAUDE_HOME", str(compat_home))
    assert install_claude_skill.default_target_root() == config_dir


def test_default_target_accepts_compat_home(monkeypatch, tmp_path):
    compat_home = tmp_path / "compat"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("CLAUDE_HOME", str(compat_home))
    assert install_claude_skill.default_target_root() == compat_home


def test_skill_only_orchestrators_have_dependency_aware_python_fallback():
    for path in (
        ROOT / ".codex/skills/figmirror/references/orchestrator-codex.md",
        ROOT / ".claude/skills/figmirror/references/orchestrator-claude.md",
    ):
        text = path.read_text(encoding="utf-8")
        assert "FIGMIRROR_PYTHON_CMD" in text
        assert "uv run --with pillow --with matplotlib --with numpy python" in text
        assert "UV_CACHE_DIR" in text
        assert "largest non-root writable filesystem" in text


def test_drawer_roles_use_canonical_review_feedback_path():
    for path in (
        ROOT / ".codex/agents/figmirror-drawer.toml",
        ROOT / ".claude/agents/figmirror-drawer.md",
    ):
        text = path.read_text(encoding="utf-8")
        assert "review_feedback_<N-1>/annotated.png" in text
        assert "audit_view_<N-1>/annotated.png" not in text


def test_codex_bundle_checker_runs_through_python_command():
    text = (
        ROOT / ".codex/skills/figmirror/references/orchestrator-codex.md"
    ).read_text(encoding="utf-8")
    assert (
        "<PYTHON_CMD> /absolute/path/to/run-directory/tools/figannot.py "
        "check-drawer-bundle"
    ) in text
