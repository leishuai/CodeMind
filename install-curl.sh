#!/usr/bin/env bash
#
# CodeMind public curl installer bootstrap.
#
# Host this file at a public URL, then install with one command:
#   curl -fsSL https://raw.githubusercontent.com/leishuai/CodeMind/main/install-curl.sh | bash
#
# This bootstrap downloads only the installer script from the public URL. It then
# clones or updates the CodeMind git repository in a private installer cache,
# syncs a git-free runtime copy to AUTOMIND_HOME, and delegates the real install
# work to that runtime copy's install.sh.
#
# Default repository:
#   https://github.com/leishuai/CodeMind.git
#
# Users must have permission to clone the repository. The final runtime install
# directory intentionally does not contain a .git directory or an origin remote, so local
# runtime summaries/config cannot be accidentally pushed back to the source repo.
# If you need installation without git/repo access, publish a release tarball and
# extend this bootstrap to download/extract that tarball instead of cloning git.

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${BLUE}[CodeMind bootstrap]${NC} $*"; }
warn() { echo -e "${YELLOW}[CodeMind bootstrap]${NC} $*"; }
error() { echo -e "${RED}[CodeMind bootstrap]${NC} $*" >&2; }
success() { echo -e "${GREEN}[CodeMind bootstrap]${NC} $*"; }

write_git_guard() {
    local dir="$1"
    rm -rf "$dir/.git"
    cat > "$dir/.git" <<'GIT_GUARD_EOF'
CodeMind runtime install is intentionally not a Git checkout.
This guard file prevents Git from discovering a parent repository.
Use the installer cache or source project checkout for updates.
GIT_GUARD_EOF
}

disable_cache_push_url() {
    local repo_dir="$1"
    if [[ -d "$repo_dir/.git" ]]; then
        git -C "$repo_dir" remote set-url --push origin DISABLED_BY_AUTOMIND_INSTALLER 2>/dev/null || true
    fi
}

BOOTSTRAP_URL="https://raw.githubusercontent.com/leishuai/CodeMind/main/install-curl.sh"
# Public sync rewrites this URL to the canonical CodeMind GitHub raw URL.

usage() {
    cat <<USAGE_EOF
CodeMind curl bootstrap installer

Usage:
  curl -fsSL $BOOTSTRAP_URL | bash

What it does:
  1. clones or updates the CodeMind repo in an installer cache;
  2. syncs a git-free runtime copy into ~/.automind/automind by default;
  3. runs ~/.automind/automind/install.sh;
  4. install.sh creates the codemind CLI, compatibility aliases, and agent integrations.

Environment variables:
  AUTOMIND_REPO             Repository to clone.
                            Default: https://github.com/leishuai/CodeMind.git
  AUTOMIND_BRANCH           Branch or tag to install. Default: main
  AUTOMIND_HOME             Git-free runtime install path. Default: ~/.automind/automind
  AUTOMIND_CACHE_DIR        Installer git cache parent. Default: ~/.automind/cache
  AUTOMIND_BIN_DIR          CLI wrapper dir passed to install.sh. Default: ~/.local/bin
  AUTOMIND_INSTALL_AGENT    Agent target passed to install.sh.
                            none|all|auto|claude|codex|trae|trae-cn. Default: all
  AUTOMIND_INSTALL_COMMAND  Whether to install /codemind and /automind. Default: 1
  AUTOMIND_UPDATE           Update existing checkout before install. Default: 1
  AUTOMIND_DEPTH            Git clone/fetch depth. Default: 1. Use 0 for full history.

Examples:
  curl -fsSL $BOOTSTRAP_URL | bash
  curl -fsSL $BOOTSTRAP_URL | AUTOMIND_BRANCH=0.2.0 bash
  curl -fsSL $BOOTSTRAP_URL | AUTOMIND_INSTALL_AGENT=auto bash
USAGE_EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 0
fi

AUTOMIND_REPO="${AUTOMIND_REPO:-https://github.com/leishuai/CodeMind.git}"
AUTOMIND_BRANCH="${AUTOMIND_BRANCH:-main}"
AUTOMIND_HOME="${AUTOMIND_HOME:-$HOME/.automind/automind}"
AUTOMIND_CACHE_DIR="${AUTOMIND_CACHE_DIR:-$HOME/.automind/cache}"
AUTOMIND_STAGING_HOME="${AUTOMIND_STAGING_HOME:-$AUTOMIND_CACHE_DIR/automind-git}"
AUTOMIND_BIN_DIR="${AUTOMIND_BIN_DIR:-$HOME/.local/bin}"
AUTOMIND_INSTALL_AGENT="${AUTOMIND_INSTALL_AGENT:-all}"
AUTOMIND_INSTALL_COMMAND="${AUTOMIND_INSTALL_COMMAND:-1}"
AUTOMIND_UPDATE="${AUTOMIND_UPDATE:-1}"
AUTOMIND_DEPTH="${AUTOMIND_DEPTH:-1}"

export AUTOMIND_REPO AUTOMIND_BRANCH AUTOMIND_HOME AUTOMIND_BIN_DIR
export AUTOMIND_INSTALL_AGENT AUTOMIND_INSTALL_COMMAND AUTOMIND_UPDATE

log "========================================"
log "  CodeMind public installer bootstrap"
log "========================================"
log "Repo:   $AUTOMIND_REPO"
log "Ref:    $AUTOMIND_BRANCH"
log "Home:   $AUTOMIND_HOME"
log "Cache:  $AUTOMIND_STAGING_HOME"
log "Agents: $AUTOMIND_INSTALL_AGENT"

case "$(uname -s)" in
    Darwin|Linux)
        ;;
    *)
        warn "Untested OS: $(uname -s). CodeMind is primarily tested on macOS and Linux."
        ;;
esac

if ! command -v git >/dev/null 2>&1; then
    error "git is required but was not found. Install git first, then rerun the one-line install command."
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    error "python3 is required but was not found. Install python3 first, then rerun the one-line install command."
    exit 1
fi

fetch_depth_args=()
clone_depth_args=()
if [[ "$AUTOMIND_DEPTH" != "0" ]]; then
    fetch_depth_args=(--depth "$AUTOMIND_DEPTH")
    clone_depth_args=(--depth "$AUTOMIND_DEPTH")
fi

mkdir -p "$(dirname "$AUTOMIND_HOME")" "$AUTOMIND_CACHE_DIR"

if [[ -e "$AUTOMIND_HOME" && ! -d "$AUTOMIND_HOME" ]]; then
    error "Install path exists but is not a directory: $AUTOMIND_HOME"
    echo "Choose another path, for example:"
    echo "  curl -fsSL $BOOTSTRAP_URL | AUTOMIND_HOME=\"$HOME/.automind/automind-new\" bash"
    exit 1
fi

if [[ -d "$AUTOMIND_STAGING_HOME/.git" ]]; then
    log "Existing CodeMind installer cache found."
    current_remote="$(git -C "$AUTOMIND_STAGING_HOME" remote get-url origin 2>/dev/null || true)"
    if [[ -n "$current_remote" && "$current_remote" != "$AUTOMIND_REPO" ]]; then
        warn "Installer cache origin differs from AUTOMIND_REPO; resetting origin."
        warn "  origin:          $current_remote"
        warn "  AUTOMIND_REPO:  $AUTOMIND_REPO"
        git -C "$AUTOMIND_STAGING_HOME" remote set-url origin "$AUTOMIND_REPO"
    fi
    if [[ "$AUTOMIND_UPDATE" == "1" ]]; then
        log "Fetching latest CodeMind ref into installer cache..."
        git -C "$AUTOMIND_STAGING_HOME" fetch "${fetch_depth_args[@]}" origin "$AUTOMIND_BRANCH"
        if git -C "$AUTOMIND_STAGING_HOME" rev-parse --verify --quiet "refs/remotes/origin/$AUTOMIND_BRANCH" >/dev/null; then
            git -C "$AUTOMIND_STAGING_HOME" checkout -q -B "$AUTOMIND_BRANCH" "origin/$AUTOMIND_BRANCH"
        else
            git -C "$AUTOMIND_STAGING_HOME" checkout -q FETCH_HEAD
        fi
        git -C "$AUTOMIND_STAGING_HOME" reset --hard -q HEAD
        git -C "$AUTOMIND_STAGING_HOME" clean -fd -q
        disable_cache_push_url "$AUTOMIND_STAGING_HOME"
    else
        warn "Skipping cache update because AUTOMIND_UPDATE=0"
        disable_cache_push_url "$AUTOMIND_STAGING_HOME"
    fi
elif [[ -e "$AUTOMIND_STAGING_HOME" ]]; then
    error "Installer cache path exists but is not a git checkout: $AUTOMIND_STAGING_HOME"
    echo "Remove it or set AUTOMIND_CACHE_DIR to another path."
    exit 1
else
    log "Cloning CodeMind into installer cache..."
    git clone "${clone_depth_args[@]}" --branch "$AUTOMIND_BRANCH" "$AUTOMIND_REPO" "$AUTOMIND_STAGING_HOME"
    disable_cache_push_url "$AUTOMIND_STAGING_HOME"
fi

log "Syncing git-free CodeMind runtime to: $AUTOMIND_HOME"
mkdir -p "$AUTOMIND_HOME"
if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
        --exclude='.git/' \
        --exclude='.automind/tasks/' \
        --exclude='.automind/summary/' \
        --exclude='dist/' \
        --exclude='lark-bridge/node_modules/' \
        --exclude='.venv-*/' \
        --exclude='summaries/accumulated/' \
        "$AUTOMIND_STAGING_HOME/" "$AUTOMIND_HOME/"
else
    warn "rsync not found; falling back to tar copy without deleting stale runtime files."
    (cd "$AUTOMIND_STAGING_HOME" && tar --exclude='.git' --exclude='.automind/tasks' --exclude='.automind/summary' --exclude='dist' --exclude='lark-bridge/node_modules' --exclude='.venv-*' --exclude='summaries/accumulated' -cf - .) | (cd "$AUTOMIND_HOME" && tar -xf -)
fi
if [[ -d "$AUTOMIND_HOME/.git" ]]; then
    warn "Removing legacy .git directory from runtime install: $AUTOMIND_HOME/.git"
fi
write_git_guard "$AUTOMIND_HOME"

# Lark Bridge JavaScript output and dependencies are optional runtime caches.
# Always invalidate them after a runtime sync so `automind channel start` cannot
# execute stale compiled code from the previous CodeMind version. Node/npm stay
# optional: the channel launcher installs/builds lazily on first use.
if [[ -d "$AUTOMIND_HOME/lark-bridge" ]]; then
    log "Invalidating optional Lark Bridge build cache..."
    rm -rf "$AUTOMIND_HOME/lark-bridge/dist" "$AUTOMIND_HOME/lark-bridge/node_modules"
fi

if [[ ! -f "$AUTOMIND_HOME/install.sh" ]]; then
    error "install.sh was not found in $AUTOMIND_HOME. The repository layout may be invalid."
    exit 1
fi

if [[ ! -f "$AUTOMIND_HOME/automind.sh" ]]; then
    error "automind.sh was not found in $AUTOMIND_HOME. The repository layout may be invalid."
    exit 1
fi

chmod +x "$AUTOMIND_HOME/install.sh" "$AUTOMIND_HOME/automind.sh" || true

log "Delegating to CodeMind install.sh..."
(
    cd "$AUTOMIND_HOME"
    bash ./install.sh
)

success "CodeMind bootstrap completed."
echo ""
echo "Try:"
echo "  automind smoke offline-demo"
echo ""
