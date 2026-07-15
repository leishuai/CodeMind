#!/usr/bin/env bash
# Install optional Lark Bridge dependencies and rebuild only when its inputs changed.
set -euo pipefail

BRIDGE_DIR="${1:-}"
FORCE_BUILD="${2:-}"

if [[ -z "$BRIDGE_DIR" || ! -d "$BRIDGE_DIR" ]]; then
    echo "Usage: ensure_lark_bridge_build.sh <lark-bridge-dir> [--force]" >&2
    exit 2
fi

require_node_18() {
    if ! command -v node >/dev/null 2>&1; then
        echo "Node.js 18 or newer is required for the optional Lark Bridge" >&2
        exit 1
    fi
    local version major
    version="$(node --version 2>/dev/null || true)"
    if [[ ! "$version" =~ ^v([0-9]+)(\.[0-9]+){1,2}([+-].*)?$ ]]; then
        echo "Unable to determine the Node.js version for the optional Lark Bridge: ${version:-unknown}" >&2
        exit 1
    fi
    major="${BASH_REMATCH[1]}"
    if (( major < 18 )); then
        echo "Node.js 18 or newer is required for the optional Lark Bridge; found $version" >&2
        exit 1
    fi
}

require_node_18

hash_stream() {
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 | awk '{print $1}'
    elif command -v sha256sum >/dev/null 2>&1; then
        sha256sum | awk '{print $1}'
    else
        cksum | awk '{print $1 ":" $2}'
    fi
}

hash_files() {
    local root="$1"
    shift
    {
        local relative
        for relative in "$@"; do
            if [[ -f "$root/$relative" ]]; then
                printf '\n--- %s ---\n' "$relative"
                cat "$root/$relative"
            fi
        done
    } | hash_stream
}

hash_sources() {
    local root="$1"
    {
        if [[ -f "$root/tsconfig.json" ]]; then
            printf '%s\n' "--- tsconfig.json ---"
            cat "$root/tsconfig.json"
        fi
        if [[ -d "$root/src" ]]; then
            find "$root/src" -type f \( -name '*.ts' -o -name '*.tsx' \) -print |
                LC_ALL=C sort |
                while IFS= read -r file; do
                    printf '\n--- %s ---\n' "${file#"$root/"}"
                    cat "$file"
                done
        fi
    } | hash_stream
}

DEPENDENCY_FINGERPRINT="$(hash_files "$BRIDGE_DIR" package.json package-lock.json)"
SOURCE_FINGERPRINT="$(hash_sources "$BRIDGE_DIR")"
DEPENDENCY_STATE="$BRIDGE_DIR/dist/.dependency-fingerprint"
SOURCE_STATE="$BRIDGE_DIR/dist/.source-fingerprint"

INSTALLED_DEPENDENCY_FINGERPRINT=""
BUILT_SOURCE_FINGERPRINT=""
[[ -f "$DEPENDENCY_STATE" ]] && INSTALLED_DEPENDENCY_FINGERPRINT="$(cat "$DEPENDENCY_STATE")"
[[ -f "$SOURCE_STATE" ]] && BUILT_SOURCE_FINGERPRINT="$(cat "$SOURCE_STATE")"

NEEDS_INSTALL="no"
NEEDS_BUILD="no"

if [[ ! -d "$BRIDGE_DIR/node_modules" || "$DEPENDENCY_FINGERPRINT" != "$INSTALLED_DEPENDENCY_FINGERPRINT" ]]; then
    NEEDS_INSTALL="yes"
fi
if [[ "$FORCE_BUILD" == "--force" || ! -f "$BRIDGE_DIR/dist/main.js" || "$SOURCE_FINGERPRINT" != "$BUILT_SOURCE_FINGERPRINT" || "$NEEDS_INSTALL" == "yes" ]]; then
    NEEDS_BUILD="yes"
fi

if [[ "$NEEDS_INSTALL" == "yes" ]]; then
    if ! command -v npm >/dev/null 2>&1; then
        echo "npm is required to install the optional Lark Bridge dependencies" >&2
        exit 1
    fi
    echo "[CodeMind] Installing Lark Bridge dependencies..."
    if [[ -f "$BRIDGE_DIR/package-lock.json" ]]; then
        (cd "$BRIDGE_DIR" && npm ci)
    else
        (cd "$BRIDGE_DIR" && npm install)
    fi
    # `npm install` may create/update package-lock.json. Recompute so the saved
    # state describes the dependency files that actually produced node_modules.
    DEPENDENCY_FINGERPRINT="$(hash_files "$BRIDGE_DIR" package.json package-lock.json)"
fi

if [[ "$NEEDS_BUILD" == "yes" ]]; then
    if ! command -v npm >/dev/null 2>&1; then
        echo "npm is required to build the optional Lark Bridge" >&2
        exit 1
    fi
    echo "[CodeMind] Building Lark Bridge..."
    (cd "$BRIDGE_DIR" && npm run build)
    mkdir -p "$BRIDGE_DIR/dist"
    printf '%s\n' "$DEPENDENCY_FINGERPRINT" > "$DEPENDENCY_STATE"
    printf '%s\n' "$SOURCE_FINGERPRINT" > "$SOURCE_STATE"
fi
