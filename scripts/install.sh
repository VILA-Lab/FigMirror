#!/usr/bin/env bash
#
# One-line installer for the FigMirror skill.
#
# Default behavior auto-detects which harness(es) you have configured
# (~/.claude exists → install Claude Code skill; ~/.codex exists →
# install Codex skill). Pass --claude / --codex / --all to override
# detection.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/main/scripts/install.sh | bash
#   curl -fsSL .../install.sh | bash -s -- --claude
#   curl -fsSL .../install.sh | bash -s -- --codex
#   curl -fsSL .../install.sh | bash -s -- --all
#
# Env overrides:
#   INSTALL_REF     Pin to a branch / tag (default: main)
#   INSTALL_REPO    owner/repo (default: VILA-Lab/FigMirror)
#   CLAUDE_CONFIG_DIR  Claude Code config dir (default: ~/.claude)
#   CLAUDE_HOME        Compatibility alias for CLAUDE_CONFIG_DIR
#   CODEX_HOME      Override Codex config dir (default: ~/.codex)
#
# Requires: curl, tar, bash. No Python, no git clone.

set -euo pipefail

REPO="${INSTALL_REPO:-VILA-Lab/FigMirror}"
REF="${INSTALL_REF:-main}"
# Claude Code reads CLAUDE_CONFIG_DIR; CLAUDE_HOME is kept as a compatibility
# alias. Installing into a directory Claude does not load is silent - the skill
# and agents simply never resolve - so prefer the variable the harness uses.
CLAUDE_TARGET="${CLAUDE_CONFIG_DIR:-${CLAUDE_HOME:-$HOME/.claude}}"

# Single source of truth: both the tarball check and the install step iterate
# this. They drifted apart once already, which made --claude abort on a cp of a
# file that no longer exists.
CLAUDE_AGENTS=(figmirror-drawer figmirror-reviewer)
CODEX_TARGET="${CODEX_HOME:-$HOME/.codex}"

# --- Parse args ---

INSTALL_CLAUDE=""
INSTALL_CODEX=""
EXPLICIT=0

usage() {
  cat <<USAGE
Install FigMirror into Claude Code and/or Codex.

Default behavior:
  bash install.sh             Auto-detect harness(es) by checking for
                              \$HOME/.claude and \$HOME/.codex; install into
                              whichever exist. Errors if neither detected.

Explicit selection:
  bash install.sh --claude    Install Claude Code skill only.
  bash install.sh --codex     Install Codex skill only.
  bash install.sh --all       Install both, regardless of detection.

Env vars:
  INSTALL_REF=<ref>           Branch or tag (default: main)
  INSTALL_REPO=<owner/repo>   Source repo
  CLAUDE_CONFIG_DIR=<path>    Claude Code config dir (default: ~/.claude)
  CLAUDE_HOME=<path>          Compatibility alias for CLAUDE_CONFIG_DIR
  CODEX_HOME=<path>           Codex config dir (default: ~/.codex)
USAGE
}

for arg in "$@"; do
  case "$arg" in
    --claude) INSTALL_CLAUDE=1; EXPLICIT=1 ;;
    --codex)  INSTALL_CODEX=1;  EXPLICIT=1 ;;
    --all)    INSTALL_CLAUDE=1; INSTALL_CODEX=1; EXPLICIT=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "[install] Unknown arg: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

# --- Detect harnesses if no explicit choice ---

if [[ $EXPLICIT == 0 ]]; then
  [[ -d "$CLAUDE_TARGET" ]] && INSTALL_CLAUDE=1
  [[ -d "$CODEX_TARGET" ]]  && INSTALL_CODEX=1

  if [[ -z "${INSTALL_CLAUDE:-}" && -z "${INSTALL_CODEX:-}" ]]; then
    cat <<NONE >&2
[install] No harness detected.
  Looked for:  $CLAUDE_TARGET (Claude Code)
               $CODEX_TARGET (Codex)

If you're setting up a new harness, pass an explicit flag:
  bash install.sh --claude     # Claude Code
  bash install.sh --codex      # Codex
  bash install.sh --all        # both
NONE
    exit 1
  fi

  echo "[install] Detected:"
  [[ "${INSTALL_CLAUDE:-}" == 1 ]] && echo "  Claude Code at $CLAUDE_TARGET"
  [[ "${INSTALL_CODEX:-}"  == 1 ]] && echo "  Codex at $CODEX_TARGET"
fi

# --- Fetch tarball ---

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

echo "[install] Fetching $REPO@$REF..."
fetch_tarball() {
  local url="https://api.github.com/repos/$REPO/tarball/$REF"
  local token="${GH_TOKEN:-${GITHUB_TOKEN:-}}"

  if [[ -n "$token" ]]; then
    curl -fsSL \
      -H "Authorization: Bearer $token" \
      -H "Accept: application/vnd.github+json" \
      "$url"
    return
  fi

  if command -v gh >/dev/null 2>&1 && gh auth status -h github.com >/dev/null 2>&1; then
    gh api \
      --method GET \
      "repos/$REPO/tarball/$REF" \
      --header "Accept: application/vnd.github+json"
    return
  fi

  curl -fsSL "$url"
}

fetch_tarball | tar -xz -C "$TMPDIR" --strip-components=1

# --- Validate ---

require_file() {
  local f="$1"
  [[ -f "$TMPDIR/$f" ]] || {
    echo "[install] ERROR: missing $f in tarball — repo state is broken" >&2
    exit 1
  }
}

require_max_lines() {
  local f="$1" max="$2" count
  count=$(wc -l < "$TMPDIR/$f")
  if [[ "$count" -gt "$max" ]]; then
    echo "[install] ERROR: $f has $count lines; split it before shipping" >&2
    exit 1
  fi
}

validate_three_d_layout() {
  local prefix="$1"
  local total=0 count f
  require_max_lines "$prefix/references/three-d-prompting.md" 80
  require_max_lines "$prefix/references/three-d/core.md" 100
  require_max_lines "$prefix/references/three-d/scale-occupancy.md" 100
  require_max_lines "$prefix/references/three-d/style-transfer.md" 100
  require_max_lines "$prefix/references/three-d/strict-reproduction.md" 100
  require_max_lines "$prefix/references/three-d/candidate-selection.md" 100
  require_max_lines "$prefix/references/three-d/surfaces.md" 100
  require_max_lines "$prefix/references/three-d/fractured-surfaces.md" 100
  require_max_lines "$prefix/references/three-d/material-lighting.md" 100
  require_max_lines "$prefix/references/three-d/volumetric-surfaces.md" 100
  require_max_lines "$prefix/references/three-d/extrema-fold-network.md" 100
  require_max_lines "$prefix/references/three-d/patch-composition.md" 100
  require_max_lines "$prefix/references/three-d/marks-and-panels.md" 100
  require_max_lines "$prefix/references/three-d/reviewer-scorecard.md" 100
  require_max_lines "$prefix/references/three-d/repair-feedback.md" 100
  for f in \
    "$prefix/references/three-d-prompting.md" \
    "$prefix/references/three-d/core.md" \
    "$prefix/references/three-d/scale-occupancy.md" \
    "$prefix/references/three-d/style-transfer.md" \
    "$prefix/references/three-d/strict-reproduction.md" \
    "$prefix/references/three-d/candidate-selection.md" \
    "$prefix/references/three-d/surfaces.md" \
    "$prefix/references/three-d/fractured-surfaces.md" \
    "$prefix/references/three-d/material-lighting.md" \
    "$prefix/references/three-d/volumetric-surfaces.md" \
    "$prefix/references/three-d/extrema-fold-network.md" \
    "$prefix/references/three-d/patch-composition.md" \
    "$prefix/references/three-d/marks-and-panels.md" \
    "$prefix/references/three-d/reviewer-scorecard.md" \
    "$prefix/references/three-d/repair-feedback.md"; do
    count=$(wc -l < "$TMPDIR/$f")
    total=$((total + count))
  done
  if [[ "$total" -gt 650 ]]; then
    echo "[install] ERROR: $prefix 3D prompt package has $total lines; split or trim it before shipping" >&2
    exit 1
  fi
}

if [[ "${INSTALL_CLAUDE:-}" == 1 ]]; then
  for a in "${CLAUDE_AGENTS[@]}"; do
    require_file ".claude/agents/$a.md"
  done
  require_file ".claude/skills/figmirror/SKILL.md"
  require_file ".claude/skills/figmirror/MANIFEST.md"
  require_file ".claude/skills/figmirror/references/orchestrator-claude.md"
  require_file ".claude/skills/figmirror/references/orchestrator-codex.md"
  require_file ".claude/skills/figmirror/references/drawer.md"
  require_file ".claude/skills/figmirror/references/reviewer.md"
  require_file ".claude/skills/figmirror/references/preprocessor.md"
  require_file ".claude/skills/figmirror/references/aesthetic-library.md"
  require_file ".claude/skills/figmirror/references/three-d-prompting.md"
  require_file ".claude/skills/figmirror/references/three-d/core.md"
  require_file ".claude/skills/figmirror/references/three-d/scale-occupancy.md"
  require_file ".claude/skills/figmirror/references/three-d/style-transfer.md"
  require_file ".claude/skills/figmirror/references/three-d/strict-reproduction.md"
  require_file ".claude/skills/figmirror/references/three-d/candidate-selection.md"
  require_file ".claude/skills/figmirror/references/three-d/surfaces.md"
  require_file ".claude/skills/figmirror/references/three-d/fractured-surfaces.md"
  require_file ".claude/skills/figmirror/references/three-d/material-lighting.md"
  require_file ".claude/skills/figmirror/references/three-d/volumetric-surfaces.md"
  require_file ".claude/skills/figmirror/references/three-d/extrema-fold-network.md"
  require_file ".claude/skills/figmirror/references/three-d/patch-composition.md"
  require_file ".claude/skills/figmirror/references/three-d/marks-and-panels.md"
  require_file ".claude/skills/figmirror/references/three-d/reviewer-scorecard.md"
  require_file ".claude/skills/figmirror/references/three-d/repair-feedback.md"
  require_file ".claude/skills/figmirror/scripts/figannot.py"
  require_file ".claude/skills/figmirror/scripts/fit_images.py"
  require_file ".claude/skills/figmirror/scripts/score_3d_candidates.py"
  validate_three_d_layout ".claude/skills/figmirror"
fi

if [[ "${INSTALL_CODEX:-}" == 1 ]]; then
  require_file ".codex/agents/figmirror-drawer.toml"
  require_file ".codex/agents/figmirror-reviewer.toml"
  require_file ".codex/skills/figmirror/SKILL.md"
  require_file ".codex/skills/figmirror/agents/openai.yaml"
  require_file ".codex/skills/figmirror/references/drawer.md"
  require_file ".codex/skills/figmirror/references/reviewer.md"
  require_file ".codex/skills/figmirror/references/orchestrator-codex.md"
  require_file ".codex/skills/figmirror/references/aesthetic-library.md"
  require_file ".codex/skills/figmirror/references/three-d-prompting.md"
  require_file ".codex/skills/figmirror/references/three-d/core.md"
  require_file ".codex/skills/figmirror/references/three-d/scale-occupancy.md"
  require_file ".codex/skills/figmirror/references/three-d/style-transfer.md"
  require_file ".codex/skills/figmirror/references/three-d/strict-reproduction.md"
  require_file ".codex/skills/figmirror/references/three-d/candidate-selection.md"
  require_file ".codex/skills/figmirror/references/three-d/surfaces.md"
  require_file ".codex/skills/figmirror/references/three-d/fractured-surfaces.md"
  require_file ".codex/skills/figmirror/references/three-d/material-lighting.md"
  require_file ".codex/skills/figmirror/references/three-d/volumetric-surfaces.md"
  require_file ".codex/skills/figmirror/references/three-d/extrema-fold-network.md"
  require_file ".codex/skills/figmirror/references/three-d/patch-composition.md"
  require_file ".codex/skills/figmirror/references/three-d/marks-and-panels.md"
  require_file ".codex/skills/figmirror/references/three-d/reviewer-scorecard.md"
  require_file ".codex/skills/figmirror/references/three-d/repair-feedback.md"
  require_file ".codex/skills/figmirror/scripts/figannot.py"
  require_file ".codex/skills/figmirror/scripts/score_3d_candidates.py"
  validate_three_d_layout ".codex/skills/figmirror"
fi

# --- Install (atomic: write to .new sibling, then mv-swap) ---
#
# Avoids leaving the user with a partial install if cp is interrupted.
# Pattern: cp to <dst>.new.<pid>, mv old to <dst>.bak.<pid>, mv new
# to <dst>, rm bak. POSIX `mv` within the same filesystem is atomic.

install_file() {
  local src="$1" dst="$2"
  local tmp="${dst}.new.$$"
  cp "$src" "$tmp"
  mv "$tmp" "$dst"
}

install_dir() {
  local src="$1" dst="$2"
  local tmp="${dst}.new.$$"
  local bak="${dst}.bak.$$"
  cp -r "$src" "$tmp"
  find "$tmp" \( -name '.DS_Store' -o -name '__pycache__' -o -name '*.pyc' \) \
    -prune -exec rm -rf {} +
  find "$tmp" -type d -name 'evals' -prune -exec rm -rf {} +
  if [[ -e "$dst" ]]; then
    mv "$dst" "$bak"
  fi
  mv "$tmp" "$dst"
  if [[ -e "$bak" ]]; then
    rm -rf "$bak"
  fi
}

if [[ "${INSTALL_CLAUDE:-}" == 1 ]]; then
  echo "[install] Claude Code → $CLAUDE_TARGET/"
  mkdir -p "$CLAUDE_TARGET/agents" "$CLAUDE_TARGET/skills"
  for a in "${CLAUDE_AGENTS[@]}"; do
    install_file "$TMPDIR/.claude/agents/$a.md" "$CLAUDE_TARGET/agents/$a.md"
  done
  install_dir  "$TMPDIR/.claude/skills/figmirror" \
               "$CLAUDE_TARGET/skills/figmirror"
  # The loop dispatches these by exact name and treats an unknown
  # subagent_type as a fatal fault, so a silent miss here would surface as a
  # dead run rather than a failed install.
  for a in "${CLAUDE_AGENTS[@]}"; do
    [[ -f "$CLAUDE_TARGET/agents/$a.md" ]] || {
      echo "[install] ERROR: $CLAUDE_TARGET/agents/$a.md missing after install" >&2
      exit 1
    }
  done
  echo "  agents: ${CLAUDE_AGENTS[*]}"
  echo "  skill:  figmirror"
fi

if [[ "${INSTALL_CODEX:-}" == 1 ]]; then
  echo "[install] Codex → $CODEX_TARGET/"
  mkdir -p "$CODEX_TARGET/agents" "$CODEX_TARGET/skills"
  install_file "$TMPDIR/.codex/agents/figmirror-drawer.toml" \
               "$CODEX_TARGET/agents/figmirror-drawer.toml"
  install_file "$TMPDIR/.codex/agents/figmirror-reviewer.toml" \
               "$CODEX_TARGET/agents/figmirror-reviewer.toml"
  install_dir "$TMPDIR/.codex/skills/figmirror" \
              "$CODEX_TARGET/skills/figmirror"
  echo "  agents: figmirror-drawer, figmirror-reviewer"
  echo "  skill:  figmirror"
fi

echo
echo "[install] Done."
echo "Next: in any Claude Code or Codex session, attach a paper-figure"
echo "screenshot + paste your data and ask to mirror the figure's style."
