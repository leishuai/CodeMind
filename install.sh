#!/usr/bin/env bash
#
# AutoMind installer
#
# Local checkout development usage:
#   ./install.sh
#
# For public curl installation, host install-curl.sh at a public URL and use:
#   curl -fsSL https://raw.githubusercontent.com/leishuai/Automind/main/install-curl.sh | bash
#

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${BLUE}[AutoMind]${NC} $*"; }
warn() { echo -e "${YELLOW}[AutoMind]${NC} $*"; }
error() { echo -e "${RED}[AutoMind]${NC} $*" >&2; }
success() { echo -e "${GREEN}[AutoMind]${NC} $*"; }

write_git_guard() {
    local dir="$1"
    rm -rf "$dir/.git"
    cat > "$dir/.git" <<'GIT_GUARD_EOF'
AutoMind runtime install is intentionally not a Git checkout.
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

AUTOMIND_REPO="${AUTOMIND_REPO:-https://github.com/leishuai/Automind.git}"
AUTOMIND_BRANCH="${AUTOMIND_BRANCH:-main}"
AUTOMIND_HOME_WAS_SET=0
if [[ -n "${AUTOMIND_HOME+x}" ]]; then
    AUTOMIND_HOME_WAS_SET=1
fi
AUTOMIND_HOME="${AUTOMIND_HOME:-$HOME/.automind/automind}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || pwd)"
LOCAL_SOURCE=""
if [[ -f "$SCRIPT_DIR/automind.sh" && -d "$SCRIPT_DIR/orchestrator" ]]; then
    LOCAL_SOURCE="$SCRIPT_DIR"
    if [[ "$AUTOMIND_HOME_WAS_SET" == "0" ]]; then
        AUTOMIND_HOME="$LOCAL_SOURCE"
    fi
fi
AUTOMIND_BIN_DIR="${AUTOMIND_BIN_DIR:-$HOME/.local/bin}"
AUTOMIND_INSTALL_AGENT="${AUTOMIND_INSTALL_AGENT:-all}" # none|all|auto|claude|codex|trae|trae-cn
AUTOMIND_INSTALL_COMMAND="${AUTOMIND_INSTALL_COMMAND:-1}" # default public install also installs /automind command
AUTOMIND_UPDATE="${AUTOMIND_UPDATE:-1}"

usage() {
    cat <<EOF
AutoMind installer

Environment variables:
  AUTOMIND_REPO            Git repository URL. Default: $AUTOMIND_REPO
  AUTOMIND_BRANCH          Git branch/ref to install. Default: $AUTOMIND_BRANCH
  AUTOMIND_HOME            Git-free runtime install directory. Default: $AUTOMIND_HOME
  AUTOMIND_BIN_DIR         Wrapper directory. Default: $AUTOMIND_BIN_DIR
  AUTOMIND_INSTALL_AGENT   Advanced override for skill target: none|all|auto|claude|codex|trae|trae-cn. Default: $AUTOMIND_INSTALL_AGENT
  AUTOMIND_INSTALL_COMMAND Advanced override; default 1 installs /automind command for the same agent target.
  AUTOMIND_UPDATE          Advanced override; set to 0 to skip git pull when directory already exists.

Examples:
  ./install.sh
  curl -fsSL https://raw.githubusercontent.com/leishuai/Automind/main/install-curl.sh | bash
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 0
fi

echo ""
log "========================================"
log "  AutoMind Installer"
log "========================================"
echo ""

if [[ "$AUTOMIND_REPO" == *"<"* && "$AUTOMIND_REPO" == *">"* && -z "$LOCAL_SOURCE" ]]; then
    error "AUTOMIND_REPO is not configured."
    echo "The public installer default should point to the AutoMind repository before publishing."
    exit 2
fi

OS="$(uname -s)"
case "$OS" in
    Darwin|Linux)
        success "Operating system: $OS"
        ;;
    *)
        warn "Untested operating system: $OS. AutoMind is primarily tested on macOS and Linux."
        ;;
esac

if ! command -v git >/dev/null 2>&1; then
    error "git is required but was not found."
    exit 1
fi
success "git: $(command -v git)"

if ! command -v python3 >/dev/null 2>&1; then
    error "python3 is required but was not found."
    exit 1
fi
success "python3: $(python3 --version 2>&1)"

mkdir -p "$(dirname "$AUTOMIND_HOME")"

if [[ -n "$LOCAL_SOURCE" && "$AUTOMIND_HOME" == "$LOCAL_SOURCE" ]]; then
    log "Using local AutoMind checkout: $AUTOMIND_HOME"
elif [[ -n "$LOCAL_SOURCE" && "$AUTOMIND_HOME_WAS_SET" == "1" ]]; then
    if [[ -e "$AUTOMIND_HOME" && ! -d "$AUTOMIND_HOME" ]]; then
        error "Install path exists but is not a directory: $AUTOMIND_HOME"
        exit 1
    fi
    log "Syncing git-free AutoMind runtime to: $AUTOMIND_HOME"
    mkdir -p "$AUTOMIND_HOME"
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete \
            --exclude='.git/' \
            --exclude='.automind/tasks/' \
            --exclude='.automind/summary/' \
            --exclude='dist/' \
            --exclude='.venv-*/' \
            --exclude='summaries/accumulated/' \
            "$LOCAL_SOURCE/" "$AUTOMIND_HOME/"
    else
        warn "rsync not found; falling back to tar copy without deleting stale runtime files."
        (cd "$LOCAL_SOURCE" && tar --exclude='.git' --exclude='.automind/tasks' --exclude='.automind/summary' --exclude='dist' --exclude='.venv-*' --exclude='summaries/accumulated' -cf - .) | (cd "$AUTOMIND_HOME" && tar -xf -)
    fi
    if [[ -d "$AUTOMIND_HOME/.git" ]]; then
        warn "Removing legacy .git directory from runtime install: $AUTOMIND_HOME/.git"
    fi
    write_git_guard "$AUTOMIND_HOME"
elif [[ -d "$AUTOMIND_HOME/.git" ]]; then
    warn "Existing git checkout install detected. Migrating to a git-free runtime copy."
    staging="${AUTOMIND_CACHE_DIR:-$HOME/.automind/cache}/automind-git"
    mkdir -p "$(dirname "$staging")"
    if [[ ! -d "$staging/.git" ]]; then
        git clone --depth 1 --branch "$AUTOMIND_BRANCH" "$AUTOMIND_REPO" "$staging"
        disable_cache_push_url "$staging"
    elif [[ "$AUTOMIND_UPDATE" == "1" ]]; then
        git -C "$staging" fetch --depth 1 origin "$AUTOMIND_BRANCH"
        git -C "$staging" checkout -q -B "$AUTOMIND_BRANCH" "origin/$AUTOMIND_BRANCH" || git -C "$staging" checkout -q FETCH_HEAD
        git -C "$staging" reset --hard -q HEAD
        git -C "$staging" clean -fd -q
        disable_cache_push_url "$staging"
    else
        disable_cache_push_url "$staging"
    fi
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete --exclude='.git/' --exclude='.automind/tasks/' --exclude='.automind/summary/' --exclude='dist/' --exclude='.venv-*/' --exclude='summaries/accumulated/' "$staging/" "$AUTOMIND_HOME/"
    else
        (cd "$staging" && tar --exclude='.git' --exclude='.automind/tasks' --exclude='.automind/summary' --exclude='dist' --exclude='.venv-*' --exclude='summaries/accumulated' -cf - .) | (cd "$AUTOMIND_HOME" && tar -xf -)
    fi
    write_git_guard "$AUTOMIND_HOME"
else
    if [[ -e "$AUTOMIND_HOME" && ! -d "$AUTOMIND_HOME" ]]; then
        error "Install path exists but is not a directory: $AUTOMIND_HOME"
        echo "Choose another AUTOMIND_HOME or remove the existing path."
        exit 1
    fi
    staging="${AUTOMIND_CACHE_DIR:-$HOME/.automind/cache}/automind-git"
    mkdir -p "$(dirname "$staging")"
    if [[ ! -d "$staging/.git" ]]; then
        log "Cloning AutoMind into installer cache..."
        git clone --depth 1 --branch "$AUTOMIND_BRANCH" "$AUTOMIND_REPO" "$staging"
        disable_cache_push_url "$staging"
    elif [[ "$AUTOMIND_UPDATE" == "1" ]]; then
        log "Updating installer cache..."
        git -C "$staging" fetch --depth 1 origin "$AUTOMIND_BRANCH"
        git -C "$staging" checkout -q -B "$AUTOMIND_BRANCH" "origin/$AUTOMIND_BRANCH" || git -C "$staging" checkout -q FETCH_HEAD
        git -C "$staging" reset --hard -q HEAD
        git -C "$staging" clean -fd -q
        disable_cache_push_url "$staging"
    else
        disable_cache_push_url "$staging"
    fi
    log "Syncing git-free AutoMind runtime to: $AUTOMIND_HOME"
    mkdir -p "$AUTOMIND_HOME"
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete --exclude='.git/' --exclude='.automind/tasks/' --exclude='.automind/summary/' --exclude='dist/' --exclude='.venv-*/' --exclude='summaries/accumulated/' "$staging/" "$AUTOMIND_HOME/"
    else
        (cd "$staging" && tar --exclude='.git' --exclude='.automind/tasks' --exclude='.automind/summary' --exclude='dist' --exclude='.venv-*' --exclude='summaries/accumulated' -cf - .) | (cd "$AUTOMIND_HOME" && tar -xf -)
    fi
    write_git_guard "$AUTOMIND_HOME"
fi

chmod +x "$AUTOMIND_HOME/automind.sh" || true
chmod +x "$AUTOMIND_HOME/install.sh" || true

log "Running AutoMind initialization..."
"$AUTOMIND_HOME/automind.sh" init

mkdir -p "$AUTOMIND_BIN_DIR"
WRAPPER="$AUTOMIND_BIN_DIR/automind"
cat > "$WRAPPER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export AUTOMIND_HOME="$AUTOMIND_HOME"
exec "$AUTOMIND_HOME/automind.sh" "\$@"
EOF
chmod +x "$WRAPPER"
success "CLI wrapper installed: $WRAPPER"

if [[ ":$PATH:" != *":$AUTOMIND_BIN_DIR:"* ]]; then
    warn "$AUTOMIND_BIN_DIR is not on PATH. Add this to your shell profile:"
    echo "  export PATH=\"$AUTOMIND_BIN_DIR:\$PATH\""
fi

if [[ "$AUTOMIND_INSTALL_AGENT" != "none" ]]; then
    log "Installing AutoMind skill for agent target: $AUTOMIND_INSTALL_AGENT"
    "$AUTOMIND_HOME/automind.sh" export-skill --install "$AUTOMIND_INSTALL_AGENT"
    if [[ "$AUTOMIND_INSTALL_COMMAND" == "1" ]]; then
        log "Installing AutoMind slash command for agent target: $AUTOMIND_INSTALL_AGENT"
        "$AUTOMIND_HOME/automind.sh" export-command --install "$AUTOMIND_INSTALL_AGENT"
    fi
else
    log "Skipping agent skill/command install because AUTOMIND_INSTALL_AGENT=none."
fi

echo ""
success "AutoMind installation complete."
echo ""
echo "Next steps:"
echo "  1. Restart your shell or add the wrapper directory to PATH:"
echo "     export PATH=\"$AUTOMIND_BIN_DIR:\$PATH\""
echo "  2. Try the no-device smoke test:"
echo "     automind smoke offline-demo"
echo "  3. AutoMind attempted to install the skill and /automind command for Claude/Codex/Trae/Trae-CN."
echo "     If an agent root was not detected yet, rerun: automind export-skill --install auto after opening that agent."
echo "  4. In the coding agent after restart/reload, try:"
echo "     /automind help"
echo ""
echo "Installed checkout: $AUTOMIND_HOME"
echo "CLI wrapper:        $WRAPPER"
