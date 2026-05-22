#!/usr/bin/env python3
"""Install the repo-local FigMirror Claude Code skill + agents
into the user's Claude Code config directory.

This installs:
- 3 first-class subagents to ~/.claude/agents/:
    figure-preprocessor.md (Stage-0 reference crop role)
    figure-illustrator.md  (Drawer role)
    figure-critic.md       (Reviewer role)
- 1 skill to ~/.claude/skills/figmirror/:
    SKILL.md, references/aesthetic-library.md, references/three-d-prompting.md,
    references/three-d/*.md, references/iter-loop-spec.md

The orchestrator role is NOT a separate subagent — it lives in the skill's
SKILL.md and is executed by the caller (the agent that loads the skill).
This is required because Claude Code subagents cannot spawn other
subagents (Task tool not exposed in nested context — see Anthropic
issue #4182 + the Claude Code custom-subagents docs which state "If your
workflow requires nested delegation, use Skills or chain subagents from
the main conversation").

After install, the skill is auto-discovered by Claude Code and the three
subagents are invocable via `subagent_type: figure-preprocessor` /
`figure-illustrator` / `figure-critic`. The user triggers the skill by asking to "copy this
figure's style" or similar phrasing while attaching a reference image
+ their data.

Note: this script is a user-built convenience wrapping `cp -r` with
frontmatter validation. The official user-level install paths per the
Claude Code skills docs are also (a) manual `cp -r .claude/agents/* ~/.claude/agents/`
+ `cp -r .claude/skills/figmirror ~/.claude/skills/`, or
(b) plugin marketplace via `/plugin install` (not yet packaged for this
skill — TODO).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path


SKILL_NAME = "figmirror"
AGENT_NAMES = ("figure-preprocessor", "figure-illustrator", "figure-critic")

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_AGENTS_DIR = REPO_ROOT / ".claude" / "agents"
SOURCE_SKILL_DIR = REPO_ROOT / ".claude" / "skills" / SKILL_NAME

REQUIRED_AGENT_FILES = tuple(f"{name}.md" for name in AGENT_NAMES)
REQUIRED_SKILL_FILES = (
    "SKILL.md",
    "references/aesthetic-library.md",
    "references/three-d-prompting.md",
    "references/three-d/core.md",
    "references/three-d/scale-occupancy.md",
    "references/three-d/style-transfer.md",
    "references/three-d/strict-reproduction.md",
    "references/three-d/candidate-selection.md",
    "references/three-d/surfaces.md",
    "references/three-d/fractured-surfaces.md",
    "references/three-d/material-lighting.md",
    "references/three-d/volumetric-surfaces.md",
    "references/three-d/extrema-fold-network.md",
    "references/three-d/patch-composition.md",
    "references/three-d/marks-and-panels.md",
    "references/three-d/reviewer-scorecard.md",
    "references/three-d/repair-feedback.md",
    "references/iter-loop-spec.md",
    "scripts/score_3d_candidates.py",
)

THREE_D_ENTRY_FILE = "references/three-d-prompting.md"
THREE_D_MODULE_DIR = "references/three-d"
THREE_D_MODULE_FILES = (
    "references/three-d/core.md",
    "references/three-d/scale-occupancy.md",
    "references/three-d/style-transfer.md",
    "references/three-d/strict-reproduction.md",
    "references/three-d/candidate-selection.md",
    "references/three-d/surfaces.md",
    "references/three-d/fractured-surfaces.md",
    "references/three-d/material-lighting.md",
    "references/three-d/volumetric-surfaces.md",
    "references/three-d/extrema-fold-network.md",
    "references/three-d/patch-composition.md",
    "references/three-d/marks-and-panels.md",
    "references/three-d/reviewer-scorecard.md",
    "references/three-d/repair-feedback.md",
)
MAX_THREE_D_ENTRY_LINES = 80
MAX_THREE_D_MODULE_LINES = 100
MAX_THREE_D_TOTAL_LINES = 650
MAX_MARKDOWN_FILE_LINES = 1000
PRODUCT_RESIDUE_PATTERNS = (
    (
        "local absolute path",
        re.compile(r"(?<![\w.~+-])/(?:[A-Za-z0-9._-]+/){2,}[A-Za-z0-9._-]+"),
    ),
    ("run label", re.compile(r"\bruns?_\d+[A-Za-z0-9_-]*\b")),
    ("numbered reference label", re.compile(r"\bref\d{2,}\b", re.IGNORECASE)),
    ("versioned scratch label", re.compile(r"\bv\d{2,}\b", re.IGNORECASE)),
    (
        "scratch snapshot label",
        re.compile(r"\b[A-Za-z0-9_-]+_snapshots?\b|\bsnapshots?_[A-Za-z0-9_-]+\b"),
    ),
)
ALLOWED_ABSOLUTE_PATH_PREFIXES = (
    "/absolute/path/",
    "/Applications/Codex.app/",
    "/usr/bin/",
)

IGNORE_NAMES = {".DS_Store", "__pycache__", "evals"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install FigMirror skill + agents into Claude Code.",
    )
    parser.add_argument(
        "--target",
        type=Path,
        help="Claude Code config root. Defaults to $CLAUDE_HOME or ~/.claude.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace existing target files / directory.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the install plan without copying.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the source skill + agents and exit.",
    )
    return parser.parse_args()


def default_target_root() -> Path:
    claude_home = os.environ.get("CLAUDE_HOME")
    if claude_home:
        return Path(claude_home).expanduser()
    return Path.home() / ".claude"


def target_root_from_args(args: argparse.Namespace) -> Path:
    return (args.target or default_target_root()).expanduser().resolve()


# --- validation ---


def read_frontmatter(md_path: Path) -> str:
    content = md_path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        raise ValueError(
            f"{md_path.relative_to(REPO_ROOT)} must start with YAML frontmatter "
            f"bounded by ---"
        )
    return match.group(1)


def validate_agent(agent_md: Path, expected_name: str) -> None:
    frontmatter = read_frontmatter(agent_md)
    name_match = re.search(r"^name:\s*([a-z0-9-]+)\s*$", frontmatter, re.MULTILINE)
    if not name_match:
        raise ValueError(
            f"{agent_md.name} frontmatter must include `name: <hyphen-case>`"
        )
    if name_match.group(1) != expected_name:
        raise ValueError(
            f"{agent_md.name} name must be {expected_name!r}, got "
            f"{name_match.group(1)!r}"
        )
    if not re.search(r"^description:", frontmatter, re.MULTILINE):
        raise ValueError(f"{agent_md.name} frontmatter must include `description:`")
    validate_markdown_file_size(agent_md, agent_md.name)


def validate_skill(skill_dir: Path) -> None:
    skill_md = skill_dir / "SKILL.md"
    frontmatter = read_frontmatter(skill_md)
    name_match = re.search(r"^name:\s*([a-z0-9-]+)\s*$", frontmatter, re.MULTILINE)
    if not name_match:
        raise ValueError("SKILL.md frontmatter must include `name: <hyphen-case>`")
    if name_match.group(1) != SKILL_NAME:
        raise ValueError(
            f"SKILL.md name must be {SKILL_NAME!r}, got {name_match.group(1)!r}"
        )
    if not re.search(r"^description:\s*(>|[^\n]+)", frontmatter, re.MULTILINE):
        raise ValueError("SKILL.md frontmatter must include `description:`")

    missing = [
        rel
        for rel in REQUIRED_SKILL_FILES
        if not (skill_dir / rel).is_file()
    ]
    if missing:
        raise ValueError(
            f"Missing required skill files: {', '.join(missing)} (in {skill_dir})"
        )

    validate_three_d_prompt_structure(skill_dir)
    validate_no_product_residue(skill_dir)
    validate_no_nonproduct_artifacts(skill_dir)
    validate_markdown_file_sizes(skill_dir)


def line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def validate_markdown_file_size(path: Path, label: str) -> None:
    lines = line_count(path)
    if lines > MAX_MARKDOWN_FILE_LINES:
        raise ValueError(
            f"{label} is too large for one product Markdown file "
            f"(got {lines} lines, max {MAX_MARKDOWN_FILE_LINES}); "
            "split it before shipping"
        )


def validate_three_d_prompt_structure(skill_dir: Path) -> None:
    expected = set(THREE_D_MODULE_FILES)
    discovered = {
        path.relative_to(skill_dir).as_posix()
        for path in (skill_dir / THREE_D_MODULE_DIR).glob("*.md")
    }
    extra = sorted(discovered - expected)
    if extra:
        raise ValueError(
            "Unexpected 3D prompt modules: "
            + ", ".join(extra)
            + "; register or remove them before shipping"
        )

    router_text = (skill_dir / THREE_D_ENTRY_FILE).read_text(encoding="utf-8")
    expected_routes = {rel.replace("references/", "", 1) for rel in expected}
    routed = set(re.findall(r"`(three-d/[^`]+\.md)`", router_text))
    unrouted = sorted(expected_routes - routed)
    if unrouted:
        raise ValueError(
            f"{THREE_D_ENTRY_FILE} does not route registered modules: "
            + ", ".join(unrouted)
        )

    entry_lines = line_count(skill_dir / THREE_D_ENTRY_FILE)
    if entry_lines > MAX_THREE_D_ENTRY_LINES:
        raise ValueError(
            f"{THREE_D_ENTRY_FILE} must stay a thin router "
            f"(got {entry_lines} lines, max {MAX_THREE_D_ENTRY_LINES})"
        )

    for rel in THREE_D_MODULE_FILES:
        module_lines = line_count(skill_dir / rel)
        if module_lines > MAX_THREE_D_MODULE_LINES:
            raise ValueError(
                f"{rel} is too large for one 3D module "
                f"(got {module_lines} lines, max {MAX_THREE_D_MODULE_LINES}); "
                "split it before shipping"
            )

    total_lines = entry_lines + sum(line_count(skill_dir / rel) for rel in discovered)
    if total_lines > MAX_THREE_D_TOTAL_LINES:
        raise ValueError(
            f"3D prompt package is too large "
            f"(got {total_lines} lines, max {MAX_THREE_D_TOTAL_LINES}); "
            "split or trim modules before shipping"
        )


def validate_no_nonproduct_artifacts(skill_dir: Path) -> None:
    blocked: list[str] = []
    if (skill_dir / "evals").exists():
        blocked.append("evals/")
    for path in sorted(skill_dir.rglob("*")):
        if path.name == "__pycache__" or path.suffix == ".pyc":
            blocked.append(path.relative_to(skill_dir).as_posix())
    if blocked:
        raise ValueError(
            "Skill package contains non-product artifacts: " + ", ".join(blocked)
        )


def validate_no_product_residue(skill_dir: Path) -> None:
    for path in iter_skill_files(skill_dir):
        rel = path.relative_to(skill_dir).as_posix()
        if path.suffix not in {".md", ".yaml", ".yml", ".py", ".sh"}:
            continue
        text = path.read_text(encoding="utf-8")
        for label, pattern in PRODUCT_RESIDUE_PATTERNS:
            for match in pattern.finditer(text):
                if label == "local absolute path" and match.group(0).startswith(
                    ALLOWED_ABSOLUTE_PATH_PREFIXES
                ):
                    continue
                raise ValueError(f"{rel} contains non-product residue: {label}")


def validate_markdown_file_sizes(skill_dir: Path) -> None:
    for path in iter_skill_files(skill_dir):
        if path.suffix != ".md":
            continue
        validate_markdown_file_size(path, path.relative_to(skill_dir).as_posix())


def iter_skill_files(skill_dir: Path) -> list[Path]:
    out: list[Path] = []
    for path in sorted(skill_dir.rglob("*")):
        if any(part in IGNORE_NAMES for part in path.parts):
            continue
        if path.is_file():
            out.append(path)
    return out


def validate_source() -> None:
    if not SOURCE_AGENTS_DIR.is_dir():
        raise ValueError(f"Source agents directory not found: {SOURCE_AGENTS_DIR}")
    if not SOURCE_SKILL_DIR.is_dir():
        raise ValueError(f"Source skill directory not found: {SOURCE_SKILL_DIR}")

    for name in AGENT_NAMES:
        agent_md = SOURCE_AGENTS_DIR / f"{name}.md"
        if not agent_md.is_file():
            raise ValueError(f"Missing required agent: {agent_md}")
        validate_agent(agent_md, expected_name=name)

    validate_skill(SOURCE_SKILL_DIR)


# --- install ---


def print_plan(target_root: Path, force: bool) -> None:
    target_agents_dir = target_root / "agents"
    target_skill_dir = target_root / "skills" / SKILL_NAME

    def existing_marker(dst: Path) -> str:
        if not dst.exists():
            return " (NEW)"
        return " (REPLACE)" if force else " (FAIL — re-run with --force to replace)"

    print(f"Source repo:    {REPO_ROOT}")
    print(f"Target root:    {target_root}")
    print()
    print("Agents:")
    for name in AGENT_NAMES:
        src = SOURCE_AGENTS_DIR / f"{name}.md"
        dst = target_agents_dir / f"{name}.md"
        print(f"  {src.relative_to(REPO_ROOT)} -> {dst}{existing_marker(dst)}")

    print()
    print("Skill:")
    print(f"  {SOURCE_SKILL_DIR.relative_to(REPO_ROOT)}/ -> "
          f"{target_skill_dir}/{existing_marker(target_skill_dir)}")
    files = iter_skill_files(SOURCE_SKILL_DIR)
    for f in files:
        print(f"      {f.relative_to(SOURCE_SKILL_DIR).as_posix()}")


def remove_existing(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def install_agent(name: str, target_root: Path, force: bool) -> Path:
    src = SOURCE_AGENTS_DIR / f"{name}.md"
    dst = target_root / "agents" / f"{name}.md"
    if dst.exists() and not force:
        raise FileExistsError(
            f"Target exists: {dst}. Re-run with --force to replace it."
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        remove_existing(dst)
    shutil.copy2(src, dst)
    return dst


def install_skill(target_root: Path, force: bool) -> Path:
    dst = target_root / "skills" / SKILL_NAME
    if dst.exists() and not force:
        raise FileExistsError(
            f"Target exists: {dst}. Re-run with --force to replace it."
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        remove_existing(dst)
    shutil.copytree(
        SOURCE_SKILL_DIR,
        dst,
        ignore=shutil.ignore_patterns(".DS_Store", "__pycache__", "*.pyc", "evals"),
    )
    return dst


def main() -> int:
    args = parse_args()

    try:
        validate_source()
    except Exception as exc:
        print(f"[ERROR] Source validation failed: {exc}", file=sys.stderr)
        return 1

    if args.validate_only:
        print(f"[OK] Source skill + agents are valid.")
        return 0

    target_root = target_root_from_args(args)

    if args.dry_run:
        print_plan(target_root, force=args.force)
        return 0

    # Preflight: collect all conflicts before mutating anything. Without
    # this, install_agent could land figure-illustrator.md and then fail
    # on figure-critic.md / skill dir, leaving a partial install.
    if not args.force:
        conflicts: list[Path] = []
        for name in AGENT_NAMES:
            dst = target_root / "agents" / f"{name}.md"
            if dst.exists():
                conflicts.append(dst)
        skill_dst = target_root / "skills" / SKILL_NAME
        if skill_dst.exists():
            conflicts.append(skill_dst)
        if conflicts:
            print(
                "[ERROR] Targets already exist; re-run with --force to replace:",
                file=sys.stderr,
            )
            for path in conflicts:
                print(f"  {path}", file=sys.stderr)
            return 1

    try:
        installed_agents = [
            install_agent(name, target_root, args.force) for name in AGENT_NAMES
        ]
        installed_skill = install_skill(target_root, args.force)
    except Exception as exc:
        print(f"[ERROR] Install failed: {exc}", file=sys.stderr)
        return 1

    print(f"[OK] Installed {SKILL_NAME} skill + {len(AGENT_NAMES)} agents to:")
    for path in installed_agents:
        print(f"  agent: {path}")
    print(f"  skill: {installed_skill}")
    print()
    print("Next: invoke the skill in any Claude Code session by asking to copy a")
    print("paper figure's style onto your own data. Attach the reference image and")
    print("paste your data; the skill will drive the iter loop.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
