import json
import os
import subprocess
from pathlib import Path

import pytest

from metaclass.core.schemas import utc_now
from metaclass.modules.paper_workflow.runtime import (
    CodexSkillInvocation,
    CodexSkillRuntime,
    FakeCodexSkillRuntime,
)
from metaclass.modules.paper_workflow.schemas import (
    ComposedStage,
    StageExecutionReport,
    StageStatus,
)

HASH = "sha256:" + "a" * 64


def _workspace(tmp_path: Path, prompt: str = "Produce the required result.") -> tuple[Path, Path]:
    workspace = tmp_path / "job"
    skill = tmp_path / "skills" / "paper-analyze"
    workspace.mkdir()
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Test Skill\nWrite the requested output.\n", encoding="utf-8")
    (workspace / "prompt.md").write_text(prompt, encoding="utf-8")
    (workspace / "source.txt").write_text("immutable evidence", encoding="utf-8")
    return workspace, skill


def _fake_cli(tmp_path: Path) -> Path:
    script = tmp_path / "fake-codex"
    script.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys
import time

if "--version" in sys.argv:
    print("codex-cli 9.9.9-test")
    raise SystemExit(0)
if len(sys.argv) >= 3 and sys.argv[1:3] == ["exec", "--help"]:
    print("--ephemeral --sandbox --output-last-message --output-schema")
    raise SystemExit(0)
prompt = sys.stdin.read()
workspace = pathlib.Path(sys.argv[sys.argv.index("-C") + 1])
output = workspace / "output"
output.mkdir(exist_ok=True)
if "SLEEP" in prompt:
    time.sleep(5)
if "MODIFY_INPUT" in prompt:
    (workspace / "source.txt").write_text("modified", encoding="utf-8")
(output / "result.json").write_text(json.dumps({
    "prompt": prompt,
    "argv": sys.argv,
    "secret_present": "SECRET_TOKEN" in os.environ,
    "codex_home_present": "CODEX_HOME" in os.environ,
}), encoding="utf-8")
print("fake stdout")
print("fake stderr", file=sys.stderr)
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _invocation(workspace: Path, skill: Path, **overrides) -> CodexSkillInvocation:
    values = {
        "stage": ComposedStage.ANALYSIS,
        "skill_name": "paper-analyze",
        "skill_directory": skill,
        "skill_version": "test-v1",
        "prompt_version": "prompt-v1",
        "input_hash": HASH,
        "workspace": workspace,
        "prompt_path": workspace / "prompt.md",
        "input_paths": (workspace / "source.txt",),
        "expected_outputs": ("result.json",),
        "timeout_seconds": 2,
    }
    values.update(overrides)
    return CodexSkillInvocation(**values)


def test_fake_runtime_is_injectable_and_records_invocations(tmp_path: Path) -> None:
    workspace, skill = _workspace(tmp_path)
    invocation = _invocation(workspace, skill)
    now = utc_now()
    expected = StageExecutionReport(
        stage=ComposedStage.ANALYSIS,
        skill_name="paper-analyze",
        skill_version="test-v1",
        prompt_version="prompt-v1",
        runtime_version="fake-v1",
        status=StageStatus.SUCCEEDED,
        started_at=now,
        finished_at=now,
        input_hash=HASH,
        outputs=["output/result.json"],
        validation_passed=True,
    )
    runtime = FakeCodexSkillRuntime(lambda received: expected)

    assert runtime.run(invocation) == expected
    assert runtime.invocations == [invocation]


@pytest.mark.parametrize("_repeat", range(5))
def test_runtime_builds_safe_command_filters_environment_and_writes_report(
    tmp_path: Path,
    _repeat: int,
) -> None:
    workspace, skill = _workspace(tmp_path)
    runtime = CodexSkillRuntime(
        codex_cli=str(_fake_cli(tmp_path)),
        codex_home=tmp_path / "codex-home",
        environment={"PATH": os.environ["PATH"], "SECRET_TOKEN": "must-not-leak"},
    )

    report = runtime.run(_invocation(workspace, skill))

    assert report.status == StageStatus.SUCCEEDED, report.model_dump(mode="json")
    assert report.validation_passed is True
    assert report.runtime_version.endswith("codex-cli 9.9.9-test")
    result = json.loads((workspace / "output" / "result.json").read_text(encoding="utf-8"))
    assert result["secret_present"] is False
    assert result["codex_home_present"] is True
    assert "--ephemeral" in result["argv"]
    assert result["argv"][result["argv"].index("--sandbox") + 1] == "workspace-write"
    assert "--dangerously-bypass-approvals-and-sandbox" not in result["argv"]
    assert "sandbox_workspace_write.network_access=false" in result["argv"]
    assert "paper-analyze" in result["prompt"]
    assert (workspace / report.stdout_path).read_text(encoding="utf-8").strip() == "fake stdout"
    assert (workspace / report.stderr_path).read_text(encoding="utf-8").strip() == "fake stderr"
    report_path = workspace / "runtime_logs" / "analysis_01" / "execution_report.json"
    assert StageExecutionReport.model_validate_json(report_path.read_text()) == report


def test_runtime_fails_closed_when_codex_modifies_an_input(tmp_path: Path) -> None:
    workspace, skill = _workspace(tmp_path, "MODIFY_INPUT")
    runtime = CodexSkillRuntime(
        codex_cli=str(_fake_cli(tmp_path)),
        codex_home=tmp_path / "codex-home",
        environment={"PATH": os.environ["PATH"]},
    )

    report = runtime.run(_invocation(workspace, skill))

    assert report.status == StageStatus.FAILED
    assert report.validation_passed is False
    assert {issue.code for issue in report.validation_issues} == {"immutable_input_modified"}


def test_runtime_terminates_timed_out_process(tmp_path: Path) -> None:
    workspace, skill = _workspace(tmp_path, "SLEEP")
    runtime = CodexSkillRuntime(
        codex_cli=str(_fake_cli(tmp_path)),
        codex_home=tmp_path / "codex-home",
        environment={"PATH": os.environ["PATH"]},
    )

    report = runtime.run(_invocation(workspace, skill, timeout_seconds=0.2))

    assert report.status == StageStatus.FAILED
    assert report.timed_out is True
    assert "timeout" in {issue.code for issue in report.validation_issues}


def test_installed_codex_cli_exposes_required_exec_contract() -> None:
    runtime = CodexSkillRuntime()
    codex_cli = runtime._resolve_codex_cli()
    result = subprocess.run(
        [codex_cli, "exec", "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    for option in (
        "--ephemeral",
        "--sandbox",
        "--output-last-message",
        "--output-schema",
        "--ignore-user-config",
    ):
        assert option in result.stdout


@pytest.mark.codex_contract
@pytest.mark.skipif(
    os.getenv("RUN_CODEX_CONTRACT_TESTS") != "1",
    reason="Set RUN_CODEX_CONTRACT_TESTS=1 to use the authenticated local Codex account",
)
def test_real_codex_cli_can_execute_a_skill_contract(tmp_path: Path) -> None:
    workspace, skill = _workspace(
        tmp_path,
        "Follow the Skill and write output/result.json containing valid JSON with "
        'exactly {"status": "ok"}.',
    )
    runtime = CodexSkillRuntime()

    report = runtime.run(
        _invocation(workspace, skill, timeout_seconds=120, expected_outputs=("result.json",))
    )

    assert report.status == StageStatus.SUCCEEDED
    assert json.loads((workspace / "output" / "result.json").read_text()) == {"status": "ok"}
