import hashlib
import os
import re
import shutil
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Thread
from typing import Protocol, TextIO

from metaclass.core.schemas import utc_now
from metaclass.modules.paper_workflow.schemas import (
    ComposedStage,
    StageExecutionReport,
    StageStatus,
    ValidationIssue,
    ValidationSeverity,
)

RUNTIME_VERSION = "codex-skill-runtime-v1"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class CodexSkillRuntimeError(RuntimeError):
    pass


class SkillRuntime(Protocol):
    def run(self, invocation: "CodexSkillInvocation") -> StageExecutionReport: ...


@dataclass(frozen=True)
class CodexSkillInvocation:
    stage: ComposedStage
    skill_name: str
    skill_directory: Path
    skill_version: str
    prompt_version: str
    input_hash: str
    workspace: Path
    prompt_path: Path
    input_paths: tuple[Path, ...] = field(default_factory=tuple)
    output_directory: Path | None = None
    expected_outputs: tuple[str, ...] = field(default_factory=tuple)
    output_schema_path: Path | None = None
    attempt: int = 1
    timeout_seconds: float = 600
    network_enabled: bool = False
    cancel_event: Event | None = None


class FakeCodexSkillRuntime:
    """Injectable deterministic runtime for provider/orchestrator unit tests."""

    def __init__(
        self,
        handler: Callable[[CodexSkillInvocation], StageExecutionReport],
    ) -> None:
        self.handler = handler
        self.invocations: list[CodexSkillInvocation] = []

    def run(self, invocation: CodexSkillInvocation) -> StageExecutionReport:
        self.invocations.append(invocation)
        return self.handler(invocation)


class CodexSkillRuntime:
    """Fail-closed, auditable boundary around non-interactive `codex exec`."""

    def __init__(
        self,
        *,
        codex_cli: str | None = None,
        model: str | None = None,
        codex_home: Path | None = None,
        environment: dict[str, str] | None = None,
    ) -> None:
        self.codex_cli = codex_cli
        self.model = model
        self.codex_home = codex_home
        self.environment = environment

    def run(self, invocation: CodexSkillInvocation) -> StageExecutionReport:
        workspace, output = self._validate_invocation(invocation)
        output.mkdir(parents=True, exist_ok=True)
        logs = workspace / "runtime_logs" / f"{invocation.stage.value}_{invocation.attempt:02d}"
        logs.mkdir(parents=True, exist_ok=True)
        stdout_path = logs / "stdout.log"
        stderr_path = logs / "stderr.log"
        result_path = logs / "last_message.json"
        started_at = utc_now()
        started_clock = time.monotonic()
        immutable_before = self._immutable_snapshot(invocation, output, logs)
        codex_cli = self._resolve_codex_cli()
        runtime_version = self._runtime_version(codex_cli)
        command = self._command(invocation, codex_cli, workspace, result_path)
        process: subprocess.Popen[str] | None = None
        stdout = ""
        stderr = ""
        timed_out = False
        canceled = False
        exit_code: int | None = None
        issues: list[ValidationIssue] = []
        execution_started_clock: float | None = None
        try:
            process = subprocess.Popen(
                command,
                cwd=workspace,
                env=self._environment(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=os.name != "nt",
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
            )
            execution_started_clock = time.monotonic()
            prompt = self._prompt(invocation, output)
            stdout_chunks: list[str] = []
            stderr_chunks: list[str] = []
            stdout_reader = Thread(
                target=self._drain_stream,
                args=(process.stdout, stdout_chunks),
                daemon=True,
            )
            stderr_reader = Thread(
                target=self._drain_stream,
                args=(process.stderr, stderr_chunks),
                daemon=True,
            )
            stdout_reader.start()
            stderr_reader.start()
            if process.stdin is None:
                raise CodexSkillRuntimeError("Codex stdin pipe is unavailable")
            try:
                process.stdin.write(prompt)
                process.stdin.flush()
            except BrokenPipeError:
                pass
            finally:
                process.stdin.close()
            while process.poll() is None:
                if invocation.cancel_event and invocation.cancel_event.is_set():
                    canceled = True
                    self._terminate_process_tree(process)
                    break
                if (
                    execution_started_clock is not None
                    and time.monotonic() - execution_started_clock > invocation.timeout_seconds
                ):
                    timed_out = True
                    self._terminate_process_tree(process)
                    break
                time.sleep(0.05)
            exit_code = process.wait(timeout=5)
            stdout_reader.join(timeout=5)
            stderr_reader.join(timeout=5)
            stdout = "".join(stdout_chunks)
            stderr = "".join(stderr_chunks)
        except (OSError, subprocess.SubprocessError) as exc:
            issues.append(self._issue("runtime_error", str(exc)))
            if process:
                self._terminate_process_tree(process)
                exit_code = process.poll()
        finally:
            stdout_path.write_text(stdout, encoding="utf-8")
            stderr_path.write_text(stderr, encoding="utf-8")

        immutable_after = self._immutable_snapshot(invocation, output, logs)
        if immutable_after != immutable_before:
            issues.append(
                self._issue(
                    "immutable_input_modified",
                    "Codex modified an input, prompt, or Skill file outside the output directory",
                )
            )
        outputs = self._output_files(output, workspace)
        present_output_names = {
            str(path.relative_to(output)) for path in output.rglob("*") if path.is_file()
        }
        missing = sorted(set(invocation.expected_outputs) - present_output_names)
        if missing:
            issues.append(
                self._issue("missing_output", f"Required outputs were not created: {missing}")
            )
        if timed_out:
            issues.append(
                self._issue("timeout", f"Codex exceeded {invocation.timeout_seconds:g} seconds")
            )
        if canceled:
            issues.append(self._issue("canceled", "Codex execution was canceled"))
        if exit_code not in {0, None}:
            issues.append(self._issue("nonzero_exit", f"Codex exited with status {exit_code}"))
        if not outputs:
            issues.append(self._issue("empty_output", "Codex produced no stage output files"))

        succeeded = exit_code == 0 and not issues and bool(outputs)
        status = (
            StageStatus.CANCELED
            if canceled
            else StageStatus.SUCCEEDED
            if succeeded
            else StageStatus.FAILED
        )
        finished_at = utc_now()
        report = StageExecutionReport(
            stage=invocation.stage,
            skill_name=invocation.skill_name,
            skill_version=invocation.skill_version,
            prompt_version=invocation.prompt_version,
            runtime_version=f"{RUNTIME_VERSION}/{runtime_version}",
            status=status,
            attempt=invocation.attempt,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=max(time.monotonic() - started_clock, 0),
            input_hash=invocation.input_hash,
            exit_code=exit_code,
            timed_out=timed_out,
            outputs=outputs,
            validation_passed=succeeded,
            validation_issues=issues,
            stdout_path=str(stdout_path.relative_to(workspace)),
            stderr_path=str(stderr_path.relative_to(workspace)),
            skill_commit=self._git_commit(invocation.skill_directory),
            model=self.model,
        )
        (logs / "execution_report.json").write_text(
            report.model_dump_json(indent=2), encoding="utf-8"
        )
        return report

    @staticmethod
    def _validate_invocation(invocation: CodexSkillInvocation) -> tuple[Path, Path]:
        if not _SAFE_NAME.fullmatch(invocation.skill_name):
            raise ValueError("skill_name contains unsupported characters")
        workspace = invocation.workspace.resolve()
        if not workspace.is_dir():
            raise ValueError(f"Workspace does not exist: {workspace}")
        skill_directory = invocation.skill_directory.resolve()
        if not skill_directory.is_dir() or not (skill_directory / "SKILL.md").is_file():
            raise ValueError("Skill directory must exist and contain SKILL.md")
        prompt = invocation.prompt_path.resolve()
        if not prompt.is_file():
            raise ValueError(f"Prompt does not exist: {prompt}")
        prompt.relative_to(workspace)
        for path in invocation.input_paths:
            resolved = path.resolve()
            if not resolved.exists():
                raise ValueError(f"Skill input does not exist: {resolved}")
            resolved.relative_to(workspace)
        output = (invocation.output_directory or workspace / "output").resolve()
        output.relative_to(workspace)
        if output == workspace:
            raise ValueError("output_directory must not be the workspace root")
        if invocation.output_schema_path:
            schema = invocation.output_schema_path.resolve()
            if not schema.is_file():
                raise ValueError(f"Output schema does not exist: {schema}")
            schema.relative_to(workspace)
        for relative in invocation.expected_outputs:
            candidate = Path(relative)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError("expected_outputs must contain safe relative paths")
        if invocation.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if invocation.attempt < 1:
            raise ValueError("attempt must be positive")
        if not re.fullmatch(r"(sha256:)?[0-9a-fA-F]{64}", invocation.input_hash):
            raise ValueError("input_hash must be a SHA-256 value")
        return workspace, output

    def _command(
        self,
        invocation: CodexSkillInvocation,
        codex_cli: str,
        workspace: Path,
        result_path: Path,
    ) -> list[str]:
        command = [
            codex_cli,
            "exec",
            "--ephemeral",
            "--sandbox",
            "workspace-write",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--color",
            "never",
            "--config",
            'shell_environment_policy.inherit="none"',
            "--config",
            f"sandbox_workspace_write.network_access={str(invocation.network_enabled).lower()}",
            "--config",
            f'web_search="{"live" if invocation.network_enabled else "disabled"}"',
            "--output-last-message",
            str(result_path),
            "-C",
            str(workspace),
        ]
        if invocation.output_schema_path:
            command.extend(["--output-schema", str(invocation.output_schema_path.resolve())])
        if self.model:
            command.extend(["--model", self.model])
        command.append("-")
        return command

    @staticmethod
    def _prompt(invocation: CodexSkillInvocation, output: Path) -> str:
        inputs = "\n".join(f"- {path.resolve()}" for path in invocation.input_paths)
        return (
            "You are executing one controlled MetaClass paper-workflow stage.\n"
            f"You must use the `{invocation.skill_name}` Skill. First read its complete "
            f"instructions at {invocation.skill_directory.resolve() / 'SKILL.md'}.\n"
            "Do not modify the Skill directory, prompt, or input files. "
            f"Write files only under {output}. Do not install dependencies.\n"
            f"Network access is {'enabled' if invocation.network_enabled else 'disabled'}.\n"
            f"Inputs:\n{inputs or '- none'}\n\n"
            "Stage instructions:\n"
            f"{invocation.prompt_path.read_text(encoding='utf-8')}\n"
        )

    def _environment(self) -> dict[str, str]:
        source = self.environment if self.environment is not None else os.environ
        allowed = {
            key: source[key]
            for key in (
                "PATH",
                "LANG",
                "LC_ALL",
                "SSL_CERT_FILE",
                "SSL_CERT_DIR",
                "REQUESTS_CA_BUNDLE",
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "NO_PROXY",
            )
            if source.get(key)
        }
        codex_home = self.codex_home or (
            Path(source["CODEX_HOME"]) if source.get("CODEX_HOME") else Path.home() / ".codex"
        )
        allowed["CODEX_HOME"] = str(codex_home.resolve())
        allowed["PYTHONUTF8"] = "1"
        return allowed

    def _resolve_codex_cli(self) -> str:
        if self.codex_cli:
            configured = Path(self.codex_cli).expanduser()
            if configured.is_file():
                return str(configured.resolve())
            discovered = shutil.which(self.codex_cli)
            if discovered:
                return discovered
            raise CodexSkillRuntimeError(f"Configured Codex CLI was not found: {self.codex_cli}")
        discovered = shutil.which("codex")
        if discovered:
            return discovered
        try:
            from codex_cli_bin import bundled_codex_path
        except ImportError as exc:
            raise CodexSkillRuntimeError(
                "Codex CLI is unavailable; install Codex or the openai-codex package"
            ) from exc
        return str(Path(bundled_codex_path()).resolve())

    @staticmethod
    def _runtime_version(codex_cli: str) -> str:
        try:
            result = subprocess.run(
                [codex_cli, "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return "unknown"
        return (result.stdout or result.stderr).strip()[:200] or "unknown"

    @staticmethod
    def _immutable_snapshot(
        invocation: CodexSkillInvocation,
        output: Path,
        logs: Path,
    ) -> dict[str, str]:
        paths = [invocation.workspace, invocation.skill_directory]
        snapshot: dict[str, str] = {}
        for root in paths:
            resolved = root.resolve()
            files = [resolved] if resolved.is_file() else sorted(resolved.rglob("*"))
            for path in files:
                if not path.is_file() or path.is_relative_to(output) or path.is_relative_to(logs):
                    continue
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                snapshot[str(path)] = digest
        return snapshot

    @staticmethod
    def _output_files(output: Path, workspace: Path) -> list[str]:
        return [
            str(path.relative_to(workspace)) for path in sorted(output.rglob("*")) if path.is_file()
        ]

    @staticmethod
    def _drain_stream(stream: TextIO | None, chunks: list[str]) -> None:
        if stream is None:
            return
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return
            chunks.append(chunk)

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
            )
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    @staticmethod
    def _git_commit(skill_directory: Path) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(skill_directory), "rev-parse", "HEAD"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        value = result.stdout.strip()
        return value if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40,64}", value) else None

    @staticmethod
    def _issue(code: str, message: str) -> ValidationIssue:
        return ValidationIssue(
            severity=ValidationSeverity.ERROR,
            code=code,
            message=message[:2000],
        )
