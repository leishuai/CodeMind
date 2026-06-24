#!/usr/bin/env bash
#
# AutoMind main script
# Usage: automind <command>
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CALLER_CWD="$(pwd)"
export AUTOMIND_RUNTIME_ROOT="${AUTOMIND_RUNTIME_ROOT:-$SCRIPT_DIR}"
# Workspace root is the user's target project. Installed wrappers may live in a
# different AutoMind runtime checkout, so never default task artifacts to SCRIPT_DIR
# unless the user is actually invoking from that checkout.
export AUTOMIND_WORKSPACE_ROOT="${AUTOMIND_WORKSPACE_ROOT:-$CALLER_CWD}"
PYTHON_EXEC="${PYTHON_EXEC:-python3}"
ORCHESTRATOR="$SCRIPT_DIR/orchestrator/main.py"
CLI_NAME="${AUTOMIND_CLI_DISPLAY:-automind}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${BLUE}[AutoMind]${NC} $*"; }
success() { echo -e "${GREEN}[AutoMind]${NC} $*"; }
warn() { echo -e "${YELLOW}[AutoMind]${NC} $*"; }
error() { echo -e "${RED}[AutoMind]${NC} $*" >&2; }

if [[ $# -eq 0 ]]; then
    if [[ -t 0 && -t 1 ]]; then
        $PYTHON_EXEC "$ORCHESTRATOR" shell
        exit $?
    fi
    command="help"
else
    command="$1"
    shift || true
fi

case "$command" in
    shell)
        $PYTHON_EXEC "$ORCHESTRATOR" shell
        ;;

    version)
        $PYTHON_EXEC "$ORCHESTRATOR" version
        ;;

    update)
        $PYTHON_EXEC "$ORCHESTRATOR" update "$@"
        ;;

    init)
        log "Checking environment..."
        
        # Check Python
        if ! command -v python3 &>/dev/null; then
            error "Python3 is not installed"
            exit 1
        fi
        success "Python3: $(python3 --version)"
        
        # Check Git. Runtime installs may contain a .git guard file (not a
        # repository) to prevent accidental parent-repo discovery/push. Do not
        # run git init in that guarded runtime directory.
        if [[ -f ".git" ]] && grep -q "AutoMind runtime install is intentionally not a Git checkout" ".git" 2>/dev/null; then
            success "Git: guarded runtime (no repository)"
        elif ! git rev-parse --is-inside-work-tree &>/dev/null; then
            warn "Initializing Git repository..."
            git init
            success "Git: ✓"
        else
            success "Git: ✓"
        fi
        
        # Check optional platform tools
        MISSING=()
        
        if command -v xcodebuild &>/dev/null; then
            success "xcodebuild: ✓"
        else
            MISSING+=("xcodebuild")
            warn "xcodebuild: ✗"
        fi
        
        if command -v gradle &>/dev/null; then
            success "gradle: ✓"
        else
            MISSING+=("gradle")
            warn "gradle: ✗"
        fi
        
        if command -v adb &>/dev/null; then
            success "adb: ✓"
        else
            MISSING+=("adb")
            warn "adb: ✗"
        fi
        
        if [[ ${#MISSING[@]} -gt 0 ]]; then
            warn "Missing tools: ${MISSING[*]}"
            warn "iOS/Android builds may be affected"
        fi
        
        # Create directories
        mkdir -p "$AUTOMIND_WORKSPACE_ROOT/.automind/tasks"
        mkdir -p "$AUTOMIND_WORKSPACE_ROOT/.automind/summary"
        mkdir -p "$AUTOMIND_WORKSPACE_ROOT/.automind/checkpoints"
        mkdir -p "$SCRIPT_DIR/docs"
        mkdir -p "$SCRIPT_DIR/orchestrator"
        
        success "Environment check complete"
        ;;
        
    ask)
        if [[ $# -lt 1 ]]; then
            error "Please provide a requirement description"
            echo "Usage: $CLI_NAME ask <requirement> [agent] [--tui|--detached|--no-tui]"
            exit 1
        fi
        USER_INPUT="$1"
        AGENT="auto"
        TUI_MODE="auto"
        for arg in "${@:2}"; do
            case "$arg" in
                --tui) TUI_MODE="on" ;;
                --detached|--no-tui) TUI_MODE="off" ;;
                *) AGENT="$arg" ;;
            esac
        done
        if [[ "$TUI_MODE" == "auto" ]]; then
            if [[ -t 0 && -t 1 ]]; then
                TUI_MODE="on"
            else
                TUI_MODE="off"
            fi
        fi
        
        log "Creating task..."
        if [[ "$TUI_MODE" == "on" ]]; then
            $PYTHON_EXEC "$ORCHESTRATOR" ask "$USER_INPUT" "$AGENT" "--tui"
        else
            $PYTHON_EXEC "$ORCHESTRATOR" ask "$USER_INPUT" "$AGENT" "--detached"
        fi
        ;;

    scaffold)
        if [[ $# -lt 1 ]]; then
            error "Please provide a requirement description"
            echo "Usage: $CLI_NAME scaffold <requirement>"
            exit 1
        fi
        USER_INPUT="$1"

        log "Scaffolding task for current-session AutoMind workflow..."
        $PYTHON_EXEC "$ORCHESTRATOR" scaffold "$USER_INPUT"
        ;;

    context-pack)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME context-pack <task-code> [iteration]"
            exit 1
        fi
        $PYTHON_EXEC "$ORCHESTRATOR" context-pack "$@"
        ;;
        
    list)
        $PYTHON_EXEC "$ORCHESTRATOR" list
        ;;
        
    status)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME status <task-code>"
            exit 1
        fi
        $PYTHON_EXEC "$ORCHESTRATOR" status "$1"
        ;;

    trace)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME trace <task-code> [--json|--write]"
            exit 1
        fi
        $PYTHON_EXEC "$ORCHESTRATOR" trace "$@"
        ;;

    process-check)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME process-check <task-code> [--json|--soft|--no-write]"
            exit 1
        fi
        $PYTHON_EXEC "$ORCHESTRATOR" process-check "$@"
        ;;
        
    logs)
        if [[ $# -lt 1 ]]; then
            $PYTHON_EXEC "$ORCHESTRATOR" logs
        else
            $PYTHON_EXEC "$ORCHESTRATOR" logs "$1"
        fi
        ;;

    plan)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME plan <task-code> [agent]"
            exit 1
        fi
        TASK_CODE="$1"
        AGENT="${2:-auto}"
        $PYTHON_EXEC "$ORCHESTRATOR" plan "$TASK_CODE" "$AGENT"
        ;;

    report)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME report <task-code>"
            exit 1
        fi
        $PYTHON_EXEC "$ORCHESTRATOR" report "$@"
        ;;

    summary)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME summary <task-code> [--ai codex|claude|trae]"
            exit 1
        fi
        $PYTHON_EXEC "$ORCHESTRATOR" summary "$@"
        ;;

    summary-refine)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME summary-refine <task-code> [codex|claude|trae]"
            exit 1
        fi
        warn "summary-refine is deprecated; forwarding to: summary <task-code> --ai <agent>"
        TASK_CODE="$1"
        AGENT="${2:-auto}"
        $PYTHON_EXEC "$ORCHESTRATOR" summary "$TASK_CODE" --ai "$AGENT"
        ;;

    improve-suggestions)
        $PYTHON_EXEC "$ORCHESTRATOR" improve-suggestions "$@"
        ;;


    preloaded-check)
        $PYTHON_EXEC "$ORCHESTRATOR" preloaded-check
        ;;

    record-check)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME record-check <task-code>  (diagnostic alias; embedded in completion-check)"
            exit 1
        fi
        $PYTHON_EXEC "$ORCHESTRATOR" record-check "$1"
        ;;

    notifications)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME notifications <task-code> [--limit N]"
            exit 1
        fi
        $PYTHON_EXEC "$ORCHESTRATOR" notifications "$@"
        ;;

    doctor)
        # Two modes:
        # 1) doctor <task-code> [--stale-seconds N] : per-task JSON report
        # 2) doctor [--auto-resume] [--dry-run] [--stale-seconds N] [--agent A]
        #    : scan all tasks and prompt y/n/all/skip for stalled ones
        $PYTHON_EXEC "$ORCHESTRATOR" doctor "$@"
        ;;

    workflow-check)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME workflow-check <task-code>"
            exit 1
        fi
        $PYTHON_EXEC "$ORCHESTRATOR" workflow-check "$1"
        ;;

    synthesize-evidence)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME synthesize-evidence <task-code>"
            exit 1
        fi
        $PYTHON_EXEC "$ORCHESTRATOR" synthesize-evidence "$1"
        ;;

    completion-check)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME completion-check <task-code>"
            exit 1
        fi
        $PYTHON_EXEC "$ORCHESTRATOR" completion-check "$1"
        ;;

    continue)
        $PYTHON_EXEC "$ORCHESTRATOR" continue "$@"
        ;;

    phase-gate)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME phase-gate <task-code> [auto|plan|build|verify|finish] [--soft]"
            exit 1
        fi
        $PYTHON_EXEC "$ORCHESTRATOR" phase-gate "$@"
        ;;


    answer)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME answer <task-code> --text TEXT | --option ID | --json JSON"
            exit 1
        fi
        $PYTHON_EXEC "$ORCHESTRATOR" answer "$@"
        ;;

    message)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME message <task-code> --text TEXT [--resume agent]"
            exit 1
        fi
        $PYTHON_EXEC "$ORCHESTRATOR" message "$@"
        ;;

    event)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME event <task-code> [--type TYPE] [--message TEXT] [--phase PHASE] [--replace-key KEY]"
            exit 1
        fi
        $PYTHON_EXEC "$ORCHESTRATOR" event "$@"
        ;;

    tui)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME tui <task-code> [--watch|--interactive]"
            exit 1
        fi
        $PYTHON_EXEC "$ORCHESTRATOR" tui "$@"
        ;;

    tick-iteration)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME tick-iteration <task-code> [phase]"
            exit 1
        fi
        $PYTHON_EXEC "$ORCHESTRATOR" tick-iteration "$@"
        ;;

    reuse)
        LIMIT="${1:-80}"
        $PYTHON_EXEC "$ORCHESTRATOR" reuse "$LIMIT"
        ;;

    reuse-ack)
        $PYTHON_EXEC "$ORCHESTRATOR" reuse-ack "$@"
        ;;

    knowledge)
        $PYTHON_EXEC "$ORCHESTRATOR" knowledge "$@"
        ;;

    summary-compact)
        KEEP="${1:-60}"
        MAXCHARS="${2:-200000}"
        $PYTHON_EXEC "$ORCHESTRATOR" summary-compact "$KEEP" "$MAXCHARS"
        ;;


    checkpoint)
        if [[ $# -lt 1 ]]; then
            error "Please provide a checkpoint subcommand"
            echo "Usage: $CLI_NAME checkpoint create|list|plan-restore|restore ..."
            exit 1
        fi
        $PYTHON_EXEC "$SCRIPT_DIR/scripts/checkpoint.py" "$@"
        ;;

    rollback)
        if [[ $# -lt 2 ]]; then
            error "Please provide a task code and checkpoint id"
            echo "Usage: $CLI_NAME rollback <task-code> <checkpoint-id>"
            exit 1
        fi
        TASK_CODE="$1"
        CP_ID="$2"
        $PYTHON_EXEC "$SCRIPT_DIR/scripts/checkpoint.py" restore "$TASK_CODE" "$CP_ID"
        ;;

    resume)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME resume <task-code> [agent] [--tui|--detached|--no-tui]"
            exit 1
        fi
        TASK_CODE="$1"
        AGENT="auto"
        TUI_MODE="auto"
        for arg in "${@:2}"; do
            case "$arg" in
                --tui) TUI_MODE="on" ;;
                --detached|--no-tui) TUI_MODE="off" ;;
                *) AGENT="$arg" ;;
            esac
        done
        if [[ "$TUI_MODE" == "auto" ]]; then
            if [[ -t 0 && -t 1 ]]; then
                TUI_MODE="on"
            else
                TUI_MODE="off"
            fi
        fi
        if [[ "$TUI_MODE" == "on" ]]; then
            $PYTHON_EXEC "$ORCHESTRATOR" resume "$TASK_CODE" "$AGENT" "--tui"
        else
            $PYTHON_EXEC "$ORCHESTRATOR" resume "$TASK_CODE" "$AGENT" "--detached"
        fi
        ;;

    smoke)
        if [[ $# -lt 1 ]]; then
            error "Please provide a smoke test name"
            echo "Usage: $CLI_NAME smoke offline-demo|planner-refiner|context-isolation|delivery-gate|mobile-review-gate|dependency-setup|ui-action-capability|unblock-gate|reuse-playbook|summary-refiner|resume-recovery|agent-session-policy|agent-failure-records|policy-guards|android-self-repair|android-probe-flow-self-repair"
            exit 1
        fi
        $PYTHON_EXEC "$ORCHESTRATOR" smoke "$1"
        ;;

    android-preflight)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME android-preflight <task-code> [iteration] [--serial SERIAL]"
            exit 1
        fi
        $PYTHON_EXEC "$SCRIPT_DIR/scripts/android_preflight.py" "$@"
        ;;

    android-project-probe)
        if [[ $# -lt 1 ]]; then
            error "Please provide an Android project path"
            echo "Usage: $CLI_NAME android-project-probe <project-path> [task-code]"
            exit 1
        fi
        PROJECT_PATH="$1"
        TASK_CODE="${2:-android_project_probe}"
        $PYTHON_EXEC "$ORCHESTRATOR" android-project-probe "$PROJECT_PATH" "$TASK_CODE"
        ;;

    android-apk-probe)
        if [[ $# -lt 1 ]]; then
            error "Please provide an APK path"
            echo "Usage: $CLI_NAME android-apk-probe <apk-path> [task-code] [--uninstall]"
            exit 1
        fi
        APK_PATH="$1"
        shift || true
        TASK_CODE="android_apk_probe"
        if [[ $# -gt 0 && "$1" != --* ]]; then
            TASK_CODE="$1"
            shift || true
        fi
        $PYTHON_EXEC "$SCRIPT_DIR/scripts/android_apk_probe.py" "$APK_PATH" "$TASK_CODE" "$@"
        ;;

    android-probe-flow)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME android-probe-flow <task-code> [iteration] [--dry-run] [--retries N]"
            exit 1
        fi
        $PYTHON_EXEC "$ORCHESTRATOR" android-probe-flow "$@"
        ;;

    setup-automation-tools|tools-setup)
        if [[ $# -gt 0 && "${1:-}" != --* ]]; then
            TARGET="$1"
            shift || true
        else
            TARGET="all"
        fi
        case "$TARGET" in
            android|ios|visual|all) ;;
            *)
                error "Unknown automation tool target: $TARGET"
                echo "Usage: $CLI_NAME setup-automation-tools [android|ios|visual|all] [--dry-run]"
                exit 1
                ;;
        esac
        $PYTHON_EXEC "$ORCHESTRATOR" setup-automation-tools "$TARGET" "$@"
        ;;

    dependency-check)
        $PYTHON_EXEC "$ORCHESTRATOR" dependency-check "$@"
        ;;


    quality-check)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME quality-check <task-code> [iteration] [--merge]"
            exit 1
        fi
        TASK_CODE="$1"
        shift || true
        if [[ $# -gt 0 && "${1:-}" != --* ]]; then
            ITERATION="$1"
            shift || true
            log "Running lightweight quality-check: $TASK_CODE iteration=$ITERATION"
            $PYTHON_EXEC "$SCRIPT_DIR/scripts/quality_evaluator.py" "$TASK_CODE" --root "$AUTOMIND_WORKSPACE_ROOT" --iteration "$ITERATION" "$@"
        else
            log "Running lightweight quality-check: $TASK_CODE"
        $PYTHON_EXEC "$SCRIPT_DIR/scripts/quality_evaluator.py" "$TASK_CODE" --root "$AUTOMIND_WORKSPACE_ROOT" "$@"
        fi
        ;;

    ui-evidence-check)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME ui-evidence-check <task-code> [iteration] [--json]"
            exit 1
        fi
        $PYTHON_EXEC "$SCRIPT_DIR/scripts/ui_evidence_check.py" "$@"
        ;;

    visual-inspect)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME visual-inspect <task-code> --image PATH [--baseline PATH] [--bbox x,y,w,h]"
            exit 1
        fi
        VISUAL_PY="$AUTOMIND_WORKSPACE_ROOT/.venv-visual-tools/bin/python"
        VISUAL_RUNNER="$PYTHON_EXEC"
        VISUAL_READY=0
        if [[ -x "$VISUAL_PY" ]] && AUTOMIND_REQ_FILE="$AUTOMIND_RUNTIME_ROOT/requirements/visual-tools.txt" "$VISUAL_PY" - <<'PY' >/dev/null 2>&1
import importlib, json, os, re
# Importable AND installed versions still satisfy requirements constraints.
for name in ("PIL", "numpy", "imagehash"):
    importlib.import_module(name)
try:
    from importlib import metadata as md
except Exception:
    raise SystemExit(0)

def parse(v):
    head = re.split(r"[^0-9.]", v.split("+")[0], 1)[0]
    p = re.findall(r"\d+", head)
    return tuple(int(x) for x in p) if p else (0,)

def cmp(a, b):
    a, b = parse(a), parse(b)
    n = max(len(a), len(b))
    a += (0,) * (n - len(a)); b += (0,) * (n - len(b))
    return (a > b) - (a < b)

def ok(installed, spec):
    for cl in (spec or "").split(","):
        cl = cl.strip()
        m = re.match(r"(==|!=|>=|<=|~=|>|<)\s*(.+)", cl)
        if not m:
            continue
        op, ver = m.group(1), m.group(2).strip()
        c = cmp(installed, ver)
        if op == ">=" and not c >= 0: return False
        if op == ">" and not c > 0: return False
        if op == "<=" and not c <= 0: return False
        if op == "<" and not c < 0: return False
        if op == "==" and not c == 0: return False
        if op == "!=" and not c != 0: return False
        if op == "~=" and not c >= 0: return False
    return True

req = os.environ.get("AUTOMIND_REQ_FILE", "")
try:
    lines = open(req, encoding="utf-8").read().splitlines() if req else []
except Exception:
    raise SystemExit(0)
for raw in lines:
    line = raw.split("#", 1)[0].strip()
    if not line:
        continue
    m = re.match(r"^([A-Za-z0-9_.\-]+)\s*(.*)$", line)
    if not m:
        continue
    name, spec = m.group(1), m.group(2)
    try:
        installed = md.version(name)
    except Exception:
        raise SystemExit(1)
    if not ok(installed, spec):
        raise SystemExit(1)
raise SystemExit(0)
PY
        then
            VISUAL_READY=1
        fi
        if [[ "$VISUAL_READY" == "1" ]]; then
            VISUAL_RUNNER="$VISUAL_PY"
        else
            warn "Visual helper packages are missing or no longer satisfy requirements; creating/updating project-local .venv-visual-tools..."
            if ! "$PYTHON_EXEC" "$ORCHESTRATOR" setup-automation-tools visual; then
                warn "Visual helper setup failed; running inspector anyway so it can write a blocked evidence artifact."
            fi
            if [[ -x "$VISUAL_PY" ]]; then
                VISUAL_RUNNER="$VISUAL_PY"
            fi
        fi
        "$VISUAL_RUNNER" "$SCRIPT_DIR/scripts/visual_inspector.py" "$@"
        ;;

    script-command)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME script-command <task-code> [iteration]"
            exit 1
        fi
        $PYTHON_EXEC "$ORCHESTRATOR" script-command "$@"
        ;;

    web-probe-flow)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME web-probe-flow <task-code> [iteration] [--flow probe-flow.web.json] [--dry-run] [--retries N]"
            exit 1
        fi
        $PYTHON_EXEC "$SCRIPT_DIR/scripts/web_probe_flow_runner.py" "$@"
        ;;

    ios-project-probe)
        if [[ $# -lt 1 ]]; then
            error "Please provide an iOS project path"
            echo "Usage: $CLI_NAME ios-project-probe <project-path> [task-code] [--scheme SCHEME] [--device-id UDID]"
            exit 1
        fi
        PROJECT_PATH="$1"
        shift || true
        TASK_CODE="ios_project_probe"
        if [[ $# -gt 0 && "$1" != --* ]]; then
            TASK_CODE="$1"
            shift || true
        fi
        $PYTHON_EXEC "$ORCHESTRATOR" ios-project-probe "$PROJECT_PATH" "$TASK_CODE" "$@"
        ;;

    ios-command-probe)
        if [[ $# -lt 1 ]]; then
            error "Please provide an iOS workspace path"
            echo "Usage: $CLI_NAME ios-command-probe <workspace-path> [task-code]"
            exit 1
        fi
        WORKSPACE_PATH="$1"
        shift || true
        TASK_CODE="ios_command_probe"
        if [[ $# -gt 0 && "$1" != --* ]]; then
            TASK_CODE="$1"
            shift || true
        fi
        $PYTHON_EXEC "$ORCHESTRATOR" ios-command-probe "$WORKSPACE_PATH" "$TASK_CODE" "$@"
        ;;

    ios-preflight)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME ios-preflight <task-code> [iteration] [--device-id CORE_DEVICE_ID]"
            exit 1
        fi
        $PYTHON_EXEC "$SCRIPT_DIR/scripts/ios_preflight.py" "$@"
        ;;

    ios-readiness-analyze)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME ios-readiness-analyze <task-code> --image PATH [--bundle-id BID]"
            exit 1
        fi
        $PYTHON_EXEC "$SCRIPT_DIR/scripts/ios_readiness_analyzer.py" "$@"
        ;;

    ios-action-plan)
        if [[ $# -lt 2 ]]; then
            error "Please provide a task code and action plan path"
            echo "Usage: $CLI_NAME ios-action-plan <task-code> <action-plan.ios.json> [--iteration N]"
            exit 1
        fi
        $PYTHON_EXEC "$SCRIPT_DIR/scripts/ios_action_plan.py" "$@"
        ;;

    ios-app-smoke)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME ios-app-smoke <task-code> --bundle-id BID --device-id CORE_DEVICE_ID [--executable NAME]"
            exit 1
        fi
        $PYTHON_EXEC "$SCRIPT_DIR/scripts/ios_app_smoke.py" "$@"
        ;;

    ios-signing-preflight)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME ios-signing-preflight <task-code> --bundle-id BID --installed-team TEAM [--new-team TEAM] [--device-id UDID]"
            echo "   or: $CLI_NAME ios-signing-preflight <task-code> --discover [--device-id UDID] [--profile-root DIR]"
            exit 1
        fi
        $PYTHON_EXEC "$SCRIPT_DIR/scripts/ios_signing_preflight.py" "$@"
        ;;

    ios-screenshot)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME ios-screenshot <task-code> [iteration] [--device-id UDID] [--output PATH]"
            exit 1
        fi
        $PYTHON_EXEC "$SCRIPT_DIR/scripts/ios_screenshot.py" "$@"
        ;;

    ios-xcuitest)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME ios-xcuitest <task-code> [iteration] [--project-path PATH --scheme SCHEME --device-id UDID ...]"
            exit 1
        fi
        $PYTHON_EXEC "$SCRIPT_DIR/scripts/ios_xcuitest_runner.py" "$@"
        ;;

    ios-probe-flow)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME ios-probe-flow <task-code> [iteration] [--flow probe-flow.ios.json] [--dry-run] [--retries N]"
            exit 1
        fi
        $PYTHON_EXEC "$SCRIPT_DIR/scripts/ios_probe_flow_runner.py" "$@"
        ;;

    ios-probe-flow-materialize)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME ios-probe-flow-materialize <task-code> [iteration] [--flow probe-flow.ios.json] [--target-bundle-id BID]"
            exit 1
        fi
        $PYTHON_EXEC "$SCRIPT_DIR/scripts/ios_probe_flow_materialize.py" "$@"
        ;;

    probe-flow-repair-suggest)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME probe-flow-repair-suggest <task-code> [--apply]"
            exit 1
        fi
        TASK_CODE="$1"
        shift || true
        $PYTHON_EXEC "$SCRIPT_DIR/scripts/probe_flow_repair_suggest.py" "$TASK_CODE" --root "$AUTOMIND_WORKSPACE_ROOT" "$@"
        ;;

    probe-flow-repair-rerun)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME probe-flow-repair-rerun <task-code> [--dry-run]"
            exit 1
        fi
        TASK_CODE="$1"
        shift || true
        log "Apply safe probe-flow patch from failure evidence and rerun when changed: $TASK_CODE"
        $PYTHON_EXEC "$SCRIPT_DIR/scripts/probe_flow_repair_suggest.py" "$TASK_CODE" --root "$AUTOMIND_WORKSPACE_ROOT" --rerun "$@"
        ;;

    export-skill)
        if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
            $PYTHON_EXEC "$SCRIPT_DIR/scripts/export_skill.py" "$@"
            exit $?
        fi
        if [[ $# -gt 0 && "${1:-}" != --* ]]; then
            EXPORT_DIR="$1"
            shift || true
        else
            EXPORT_DIR="$HOME/Downloads/automind-skill"
        fi
        log "Exporting AutoMind skill package to: $EXPORT_DIR"
        $PYTHON_EXEC "$SCRIPT_DIR/scripts/export_skill.py" "$EXPORT_DIR" "$@"
        success "Export complete: $EXPORT_DIR"
        ;;

    export-command)
        if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
            $PYTHON_EXEC "$SCRIPT_DIR/scripts/export_command.py" "$@"
            exit $?
        fi
        if [[ $# -gt 0 && "${1:-}" != --* ]]; then
            EXPORT_DIR="$1"
            shift || true
        else
            EXPORT_DIR="$SCRIPT_DIR/dist/automind-command"
        fi
        log "Exporting AutoMind command package to: $EXPORT_DIR"
        $PYTHON_EXEC "$SCRIPT_DIR/scripts/export_command.py" "$EXPORT_DIR" "$@"
        success "Export complete: $EXPORT_DIR"
        ;;
        
    workflow-contract)
        if [[ $# -lt 1 ]]; then
            error "Please provide a task code"
            echo "Usage: $CLI_NAME workflow-contract <task-code>"
            exit 1
        fi
        $PYTHON_EXEC "$ORCHESTRATOR" workflow-contract "$1"
        ;;

    help|--help|-h|"")
        cat <<EOF
AutoMind - evidence-driven harness loop for coding agents

Usage:
  $CLI_NAME <command> [args]

Entrypoints:
  automind <command>                         # installed runtime
  ./automind.sh <command>                    # source checkout
  $CLI_NAME shell                            # interactive command shell

Main task loop:
  ask <requirement> [agent] [--tui|--detached]
      Create a task and start a CLI-owned harness loop. Agents: auto/codex/claude/trae.
  scaffold <requirement>
      Create task artifacts for current-session skill or slash-command mode.
  resume <code> [agent] [--tui|--detached]
      Resume from persisted runtime-state, evaluation, and artifacts.
  plan <code> [agent]
      Run Phase 2 Refiner/planner without implementation.
  continue [code]
      Print the shared next-step instruction for the active task or code.
  message <code> --text TEXT [--resume agent]
      Record a natural-language user message for a running task/session.
  answer <code> --text TEXT|--option ID|--json JSON
      Record a user answer for a pending ask_user gate.

Status, state, and handoff gates:
  list
      List tasks.
  status <code>
      Show task status, owner, next action, gate summaries, and report manifest.
  workflow-check <code>
      Validate Phase 2/3 artifact continuity before Generator/Build.
  workflow-contract <code>
      Materialize and validate workflow.json executable contract.
  phase-gate <code> [auto|plan|build|verify|finish] [--soft]
      Gate skill/command handoff using automind-workflow-state.json, effective route,
      workflow-check/completion-check signals, and phase-reuse refresh.
  tick-iteration <code> [phase]
      Increment iteration counter and enforce budget in skill/command mode.
  completion-check <code>
      Finish gate: validate required TC/AC/runtime evidence; update evaluation.json,
      completion-report.json, and VerificationLedger.json.
  process-check <code> [--json|--soft|--no-write]
      Diagnose whether the harness process followed required gates.
  trace <code> [--json|--write]
      Show or write formal trace spans for the task.

Reports, reuse, and memory:
  report <code>
      Generate Report.html with critical artifacts, per-TC evidence, screenshots, and logs.
  summary <code> [--ai agent]
      Generate deterministic summary; optionally AI-refine it.
  summary-refine <code> [agent]
      Deprecated alias for summary --ai.
  record-check <code>
      Diagnostic reuse audit; completion-check embeds the finish-time record gate.
  reuse [limit]
      Show local reuse index.
  reuse-ack show <code> [phase]
  reuse-ack <code> <phase> [--read] [--applied A;B] [--ignored C;D]
      Acknowledge generator/evaluator phase-reuse files before proceeding.
  improve-suggestions [--limit N]
      Show suggestions from summary run cards.
  summary-compact [keep_recent] [max_chars]
      Deduplicate and trim global summary stores.
  preloaded-check
      Validate preloaded pack metadata and Reuse.md discovery.
  knowledge check|search|show ...
      Inspect local AutoMind knowledge indexes and raw records.

Diagnostics and task operations:
  logs [code]
      Show task logs.
  notifications <code> [--limit N]
      Tail long-running task notifications.jsonl.
  doctor <code> [--stale-seconds N]
      Diagnose heartbeat/progress/status for a task.
  tui <code>
      Open AutoMind TUI snapshot/watch/interactive view.
  event <code> [--type TYPE] [--message TEXT] [--phase PHASE]
      Append shared event timeline entry.
  checkpoint create|list|plan-restore|restore ...
      Manage task checkpoints. P0 does not auto-restore.
  rollback <code> <checkpoint-id>
      Restore a checkpoint alias.
  smoke <name>
      Run smoke tests such as offline-demo, context-isolation, completion gates,
      reuse-playbook, agent-session-policy, android-self-repair.

Evidence runners and generic checks:
  context-pack <code> [iteration]
      Create Evaluator context pack for isolated verification.
  synthesize-evidence <code>
      Build partial evaluation.testResults from existing summary artifacts.
  dependency-check [task-code] [iteration]
      Read-only web/client/server dependency plan.
  script-command <task-code> [iteration]
      Run generic script-command evaluator.
  quality-check <task-code> [iteration] [--merge]
      Run lightweight quality-check and optionally merge into evaluation.json.
  ui-evidence-check <task-code> [iteration]
      Check UI automation evidence completeness.
  visual-inspect <task-code> --image PATH [--baseline PATH]
      Deterministic screenshot/image inspection fallback.
  setup-automation-tools [android|ios|visual|all]
      Install project-local Python helpers for verification.

Android / Web / iOS helpers:
  android-preflight <task-code> [iteration]
      Check adb/tools/screen/SystemUI readiness.
  android-project-probe <path> [task-code]
      Read-only probe for a real Android project.
  android-apk-probe <apk-path> [task-code] [--uninstall]
      Probe a real Android APK.
  android-probe-flow <task-code> [iteration] [--dry-run] [--retries N]
      Run Android dynamic probe-flow evaluator.
  web-probe-flow <task-code> [iteration] [--flow probe-flow.web.json] [--dry-run] [--retries N]
      Run Web Client UI probe-flow evaluator.
  ios-project-probe <project-path> [task-code]
      Read-only probe for a real iOS project.
  ios-command-probe <workspace-path> [task-code]
      Read-only probe for custom workspace wrapper/Bazel/CocoaPods command surface.
  ios-preflight <task-code> [iteration]
      Check iOS tools/device/Developer Mode/display readiness.
  ios-readiness-analyze <task-code> --image PATH [--bundle-id BID]
      Analyze iOS screenshot readiness blockers with OCR.
  ios-action-plan <task-code> <action-plan.ios.json>
      Validate iOS UI action plan and generate XCUITest draft.
  ios-app-smoke <task-code> --bundle-id BID --device-id CORE_DEVICE_ID
      Launch installed iOS app and collect app-alive evidence.
  ios-signing-preflight <task-code> --bundle-id BID --installed-team TEAM
  ios-signing-preflight <task-code> --discover
      Read-only check/discovery for iOS signing material.
  ios-screenshot <task-code> [iteration]
      Capture iOS device screenshot with pymobiledevice3/tunneld.
  ios-xcuitest <task-code> [iteration]
      Run iOS XCUITest evaluator.
  ios-probe-flow <task-code> [iteration] [--flow probe-flow.ios.json] [--dry-run] [--retries N]
      Run iOS probe-flow evaluator.
  ios-probe-flow-materialize <task-code> [iteration]
      Materialize iOS probe-flow action intent into Swift XCUITest draft.
  probe-flow-repair-suggest <task-code> [--apply]
      Suggest or apply probe-flow repair from failure evidence.
  probe-flow-repair-rerun <task-code> [--dry-run]
      Rerun probe-flow after safe repair.

Install / export:
  init
      Check environment and create AutoMind directories.
  version
      Show AutoMind runtime version.
  update
      Update AutoMind runtime, CLI wrapper, skill package, and /automind command.
  shell
      Open the AutoMind interactive command shell.
  export-skill [dir] [--install auto|claude|codex|trae|trae-cn]
      Export the AutoMind skill package. Default dir: ~/Downloads/automind-skill.
      --install auto installs into the first detected supported local agent skill root.
  export-command [dir] [--install all|auto|claude|codex|trae|trae-cn]
      Export the /automind slash command. This is separate from the skill package.
  help
      Show this help.

Common workflows:
  New CLI-owned task:
    $CLI_NAME ask "Fix login crash and verify" codex
    $CLI_NAME status <task-code>
    $CLI_NAME resume <task-code> codex

  Current-session skill/command task:
    $CLI_NAME scaffold "Fix login crash and verify"
    $CLI_NAME workflow-check <task-code>
    $CLI_NAME phase-gate <task-code> auto

  Resume and route an old task:
    $CLI_NAME status <task-code>
    $CLI_NAME completion-check <task-code>
    $CLI_NAME phase-gate <task-code> auto

  Finish handoff:
    $CLI_NAME completion-check <task-code>
    $CLI_NAME summary <task-code>
    $CLI_NAME record-check <task-code>
    $CLI_NAME report <task-code>

  Update AutoMind:
    $CLI_NAME update

  Install agent integrations:
    $CLI_NAME export-skill --install auto
    $CLI_NAME export-command --install auto

Examples:
  $CLI_NAME ask "Build a calculator with add/subtract/multiply/divide"
  $CLI_NAME scaffold "Fix login crash and verify"
  $CLI_NAME context-pack calculator_1230 1
  $CLI_NAME workflow-check calculator_1230
  $CLI_NAME phase-gate calculator_1230 auto
  $CLI_NAME completion-check calculator_1230
  $CLI_NAME report calculator_1230
  $CLI_NAME reuse
  $CLI_NAME reuse-ack calculator_1230 generator --read --applied "used signed app path"
  $CLI_NAME trace calculator_1230 --write
  $CLI_NAME process-check calculator_1230 --json
  $CLI_NAME android-preflight android_preflight_demo
  $CLI_NAME android-probe-flow android_probe_flow_demo --dry-run
  $CLI_NAME ios-preflight ios_preflight_demo
  $CLI_NAME ios-xcuitest ios_demo_xcuitest 1 --project-path demos/ios-simulator-demo/AutoMindIOSDemo.xcodeproj --scheme AutoMindIOSDemo --device-id <ios-device-udid> --team <development-team-id> --bundle-id ai.openclaw.automind.demo
  $CLI_NAME web-probe-flow web_probe_flow_demo 1 --dry-run
  $CLI_NAME update
  $CLI_NAME export-skill
  $CLI_NAME export-skill --install auto
  $CLI_NAME export-command --install all
EOF
        ;;
        
    *)
        error "Unknown command: $command"
        echo "Run $CLI_NAME help for usage"
        exit 1
        ;;
esac
