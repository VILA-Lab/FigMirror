#!/usr/bin/env python3
"""Install the repo-local FigMirror skill into Codex."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path


SKILL_NAME = "figmirror"
REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / ".codex" / "skills" / SKILL_NAME
SOURCE_AGENTS_DIR = REPO_ROOT / ".codex" / "agents"

REQUIRED_AGENT_FILES = (
    "figmirror-drawer.toml",
    "figmirror-reviewer.toml",
)

REQUIRED_FILES = (
    "SKILL.md",
    "agents/openai.yaml",
    "references/preprocessor.md",
    "references/drawer.md",
    "references/reviewer.md",
    "references/orchestrator-codex.md",
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
    "scripts/figannot.py",
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

PROHIBITED_SKILL_MD_FRAGMENTS = (
    "/".join(("resources", "prompts")),
)

PROHIBITED_RUNTIME_FRAGMENTS = (
    "/".join(
        (
            "resources",
            "prompts",
            "-".join(("figure", "style", "copier", "codex.md")),
        )
    ),
)
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

IGNORE_NAMES = {
    ".DS_Store",
    "__pycache__",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install FigMirror's .codex/skills/figmirror into Codex.",
    )
    parser.add_argument(
        "--target",
        type=Path,
        help="Codex skills root. Defaults to $CODEX_HOME/skills or ~/.codex/skills.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing target skill directory.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the install plan without copying files.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the source skill package and exit.",
    )
    return parser.parse_args()


def default_target_root() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "skills"
    return Path.home() / ".codex" / "skills"


def target_root_from_args(args: argparse.Namespace) -> Path:
    return (args.target or default_target_root()).expanduser().resolve()


def read_frontmatter(skill_md: Path) -> str:
    content = skill_md.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        raise ValueError("SKILL.md must start with YAML frontmatter bounded by ---")
    return match.group(1)


def validate_frontmatter(skill_dir: Path) -> None:
    frontmatter = read_frontmatter(skill_dir / "SKILL.md")
    name_match = re.search(r"^name:\s*([a-z0-9-]+)\s*$", frontmatter, re.MULTILINE)
    if not name_match:
        raise ValueError("SKILL.md frontmatter must include a hyphen-case name")
    name = name_match.group(1)
    if name != SKILL_NAME:
        raise ValueError(f"SKILL.md name must be {SKILL_NAME!r}, got {name!r}")
    if not re.search(r"^description:\s*(>|[^\n]+)", frontmatter, re.MULTILINE):
        raise ValueError("SKILL.md frontmatter must include description")


def validate_required_files(skill_dir: Path) -> None:
    missing = [rel for rel in REQUIRED_FILES if not (skill_dir / rel).is_file()]
    if missing:
        raise ValueError("Missing required skill files: " + ", ".join(missing))


def validate_required_agent_files(agent_dir: Path = SOURCE_AGENTS_DIR) -> None:
    missing = [rel for rel in REQUIRED_AGENT_FILES if not (agent_dir / rel).is_file()]
    if missing:
        raise ValueError("Missing required Codex agent files: " + ", ".join(missing))

    for rel in REQUIRED_AGENT_FILES:
        text = (agent_dir / rel).read_text(encoding="utf-8")
        expected_name = rel[:-5] if rel.endswith(".toml") else rel
        if f'name = "{expected_name}"' not in text:
            raise ValueError(f"{rel} must declare name = {expected_name!r}")


def line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


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


def validate_no_repo_runtime_refs(skill_dir: Path) -> None:
    skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    for fragment in PROHIBITED_SKILL_MD_FRAGMENTS:
        if fragment in skill_md:
            raise ValueError(f"SKILL.md must not reference repo-local path fragment: {fragment}")

    for path in iter_skill_files(skill_dir):
        rel = path.relative_to(skill_dir).as_posix()
        if path.suffix not in {".md", ".yaml", ".yml", ".py", ".sh"}:
            continue
        text = path.read_text(encoding="utf-8")
        for fragment in PROHIBITED_RUNTIME_FRAGMENTS:
            if fragment in text:
                raise ValueError(f"{rel} must not reference repo-local path fragment: {fragment}")


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


def validate_markdown_file_sizes(skill_dir: Path) -> None:
    for path in iter_skill_files(skill_dir):
        if path.suffix != ".md":
            continue
        lines = line_count(path)
        if lines > MAX_MARKDOWN_FILE_LINES:
            rel = path.relative_to(skill_dir).as_posix()
            raise ValueError(
                f"{rel} is too large for one product Markdown file "
                f"(got {lines} lines, max {MAX_MARKDOWN_FILE_LINES}); "
                "split it before shipping"
            )


def validate_skill_package(skill_dir: Path = SOURCE_DIR) -> None:
    if not skill_dir.is_dir():
        raise ValueError(f"Skill source directory not found: {skill_dir}")
    validate_required_files(skill_dir)
    validate_required_agent_files()
    validate_frontmatter(skill_dir)
    validate_three_d_prompt_structure(skill_dir)
    validate_no_repo_runtime_refs(skill_dir)
    validate_no_product_residue(skill_dir)
    validate_no_nonproduct_artifacts(skill_dir)
    validate_markdown_file_sizes(skill_dir)


def iter_skill_files(skill_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(skill_dir.rglob("*")):
        if any(part in IGNORE_NAMES for part in path.parts):
            continue
        if path.is_file():
            files.append(path)
    return files


def print_plan(source: Path, destination: Path, agent_destination: Path) -> None:
    files = iter_skill_files(source)
    print(f"Source: {source}")
    print(f"Destination: {destination}")
    print(f"Files: {len(files)}")
    for path in files:
        print(f"  {path.relative_to(source).as_posix()}")
    print(f"Agent source: {SOURCE_AGENTS_DIR}")
    print(f"Agent destination: {agent_destination}")
    for rel in REQUIRED_AGENT_FILES:
        print(f"  {rel}")


def remove_existing(destination: Path) -> None:
    if destination.is_symlink() or destination.is_file():
        destination.unlink()
        return
    if destination.is_dir():
        shutil.rmtree(destination)


def copy_skill(source: Path, destination: Path, force: bool) -> None:
    if destination.exists():
        if not force:
            raise FileExistsError(
                f"Target already exists: {destination}. Re-run with --force to replace it."
            )
        remove_existing(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(".DS_Store", "__pycache__", "*.pyc"),
    )


def copy_agent_configs(source: Path, destination: Path, force: bool) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for rel in REQUIRED_AGENT_FILES:
        src = source / rel
        dst = destination / rel
        if dst.exists():
            if not force:
                raise FileExistsError(
                    f"Target already exists: {dst}. Re-run with --force to replace it."
                )
            remove_existing(dst)
        shutil.copy2(src, dst)


def main() -> int:
    args = parse_args()

    try:
        validate_skill_package(SOURCE_DIR)
    except Exception as exc:
        print(f"[ERROR] Skill package validation failed: {exc}", file=sys.stderr)
        return 1

    if args.validate_only:
        print(f"[OK] Skill package is valid: {SOURCE_DIR}")
        return 0

    target_root = target_root_from_args(args)
    destination = target_root / SKILL_NAME
    agent_destination = target_root.parent / "agents"

    if args.dry_run:
        print_plan(SOURCE_DIR, destination, agent_destination)
        if destination.exists() and not args.force:
            print("Note: destination exists; install would fail without --force.")
        return 0

    try:
        copy_skill(SOURCE_DIR, destination, args.force)
        copy_agent_configs(SOURCE_AGENTS_DIR, agent_destination, args.force)
    except Exception as exc:
        print(f"[ERROR] Install failed: {exc}", file=sys.stderr)
        return 1

    print(f"[OK] Installed {SKILL_NAME} to {destination}")
    print(f"[OK] Installed FigMirror agents to {agent_destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
