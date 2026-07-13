from __future__ import annotations

import subprocess
from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


def test_export_skill_contains_skill_mode_json_handoff_contract(tmp_path: Path) -> None:
    out_dir = tmp_path / "codeautonomy-skill"
    run(["./automind.sh", "export-skill", str(out_dir), "--clean"])

    skill = (out_dir / "SKILL.md").read_text()
    readme = (out_dir / "README.md").read_text()

    assert "JSON handoff protocol for Skill mode" in skill
    assert "workflow.json" in skill
    assert "phase sidecars" in skill
    assert "evaluation.json" in skill
    assert "completion-report.json" in skill
    assert "templates/phase2_planner_prompt.md" in skill
    assert "Skill-mode loop driver protocol" in skill
    assert "TestCase -> verifier operation protocol" in skill
    assert "demand digestion" in skill
    assert "web-probe-flow" in skill
    assert "probe-flow.web.json" in skill
    assert "docs/phases/demand-definition.md" in skill
    assert "docs/phases/verification-execution-planning.md" in skill
    assert "phase-gate <task-code> auto" in skill
    assert "checklist[]" in skill
    assert "run a CLI command only when that checklist item explicitly names" in skill
    assert "phase-reuse/<phase>.md" in skill
    assert "docs/references/skill-command-driver-checklist.md" in skill
    assert "Updating CodeAutonomy itself" in skill
    assert "<AUTOMIND_CLI> update" in skill
    assert "do not run `scaffold`" in skill
    assert (out_dir / "docs" / "phases" / "demand-definition.md").exists()
    assert (out_dir / "docs" / "phases" / "verification-execution-planning.md").exists()
    alias = (out_dir / "templates" / "test_planner_prompt.md").read_text()
    assert "Deprecated alias" in alias
    assert "templates/phase2_planner_prompt.md" in alias

    assert "workflow control-state, runtime-state, phase sidecar, trace/process/run-card, evaluation, probe-flow contracts" in readme
    assert (out_dir / "templates" / "phase2_planner_prompt.md").exists()
    assert (out_dir / "docs" / "tui-session-observability.md").exists()
    assert (out_dir / "docs" / "references" / "skill-command-driver-checklist.md").exists()
    assert (out_dir / "docs" / "references" / "app-use-verification.md").exists()
    frontmatter = skill.split("---", 2)[1]
    assert "name: codeautonomy-skill" in frontmatter
    description_line = next(line for line in frontmatter.splitlines() if line.startswith("description: "))
    assert description_line.startswith('description: "')
    assert description_line.endswith('"')
    assert "high automation with keeping looping" in description_line
    assert (out_dir / "schemas" / "workflow.schema.json").exists()
    assert (out_dir / "schemas" / "runtime-state.schema.json").exists()
    assert (out_dir / "schemas" / "automind-workflow-state.schema.json").exists()
    assert (out_dir / "schemas" / "automind-stage-state.schema.json").exists()
    assert (out_dir / "schemas" / "automind-workflow-event.schema.json").exists()
    assert (out_dir / "schemas" / "process-eval.schema.json").exists()
    assert (out_dir / "schemas" / "run-card.schema.json").exists()
    artifact_shape = (out_dir / "examples" / "offline-script-demo" / "artifact-shape.md").read_text()
    assert artifact_shape.count("runtime-state.json") == 1


def test_export_command_contains_json_handoff_contract(tmp_path: Path) -> None:
    out_dir = tmp_path / "codeautonomy-command"
    run(["./automind.sh", "export-command", str(out_dir), "--clean"])

    command = (out_dir / "commands" / "codeautonomy.md").read_text()
    assert "JSON sidecars as the structured handoff protocol" in command
    assert "workflow-check" in command
    assert "docs/phases/demand-definition.md" in command
    assert "docs/phases/verification-execution-planning.md" in command
    assert "completion-report.json" in command
    assert "VerificationLedger.json" in command
    assert "concrete verifier operation" in command
    assert "Web probe-flow" in command
    assert "Client UI action evidence" in command
    assert "phase-gate <task-code> auto" in command
    assert "checkboxMarkdown[]" in command
    assert "phaseReuseRefresh" in command
    assert "phase-reuse/generator.md" in command
    assert "run a CLI command only when that item explicitly provides one" in command
    assert "automind-workflow-state.json" in command
    assert "ask|resume|status|summary|verify|detached|cli-ask|update|help" in command
    assert "Update intent" in command
    assert "<AUTOMIND_CLI> update" in command
    assert "Do not scaffold a task" in command
    frontmatter = command.split("---", 2)[1]
    description_line = next(line for line in frontmatter.splitlines() if line.startswith("description: "))
    assert description_line.startswith('description: "')
    assert description_line.endswith('"')
    assert "high automation: keep looping" in description_line
    artifact_block = command.split("```text\n.automind/tasks/<task>/", 1)[1].split("```", 1)[0]
    assert artifact_block.count("runtime-state.json") == 1


def test_export_skill_default_goes_to_downloads_with_temp_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run(
        ["./automind.sh", "export-skill"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        env={**__import__("os").environ, "HOME": str(home)},
    )
    out_dir = home / "Downloads" / "codeautonomy-skill"
    assert out_dir.exists()
    assert (out_dir / "SKILL.md").exists()
    assert str(out_dir) in result.stdout


def test_export_skill_auto_installs_all_detected_agent_roots(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".codex").mkdir(parents=True)
    # Trae root intentionally absent: auto should skip it and not create it.
    out_dir = tmp_path / "skill-export"
    result = subprocess.run(
        ["./automind.sh", "export-skill", str(out_dir), "--install", "auto"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        env={**__import__("os").environ, "HOME": str(home)},
    )
    assert (home / ".claude" / "skills" / "codeautonomy-skill" / "SKILL.md").exists()
    assert (home / ".codex" / "skills" / "codeautonomy-skill" / "SKILL.md").exists()
    assert not (home / ".trae").exists()
    assert '"claude:user"' in result.stdout
    assert '"codex:user"' in result.stdout
    assert '"trae:user"' in result.stdout
    assert 'agent root not found' in result.stdout


def test_legacy_automind_skill_and_command_aliases_remain_exportable(tmp_path: Path) -> None:
    skill_dir = tmp_path / "legacy-skill"
    command_dir = tmp_path / "legacy-command"

    run([
        "./automind.sh",
        "export-skill",
        str(skill_dir),
        "--clean",
        "--install-name",
        "automind-skill",
    ])
    run([
        "./automind.sh",
        "export-command",
        str(command_dir),
        "--clean",
        "--command-name",
        "automind",
    ])

    assert (skill_dir / "SKILL.md").exists()
    assert (command_dir / "commands" / "automind.md").exists()


def test_export_skill_includes_public_preloaded_packs(tmp_path: Path) -> None:
    out = tmp_path / "skill"
    run(["./automind.sh", "export-skill", str(out), "--clean"])

    exported = {p.name for p in (out / "summaries" / "preloaded").glob("*.md")}
    assert "android-bytecode-transform-cache.md" in exported
    assert "ios-signing-install.md" in exported
