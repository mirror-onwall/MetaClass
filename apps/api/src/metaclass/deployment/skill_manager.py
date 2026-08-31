from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

LOCK_VERSION = 1
RECEIPT_NAME = ".metaclass-skill.json"
PINNED_REVISION = re.compile(r"^[0-9a-f]{40}$")


class SkillDependency(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")


class SkillLockEntry(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    directory_name: str | None = None
    environment_variable: str | None = None
    required: bool = True
    source_url: str | None = None
    revision: str | None = None
    subdirectory: str | None = None
    required_files: list[str] = Field(default_factory=lambda: ["SKILL.md"])
    dependencies: list[SkillDependency] = Field(default_factory=list)
    install_enabled: bool = True

    @model_validator(mode="after")
    def validate_install_source(self) -> SkillLockEntry:
        if self.install_enabled and (not self.source_url or not self.revision):
            raise ValueError("installable Skill requires source_url and revision")
        if self.subdirectory and (Path(self.subdirectory).is_absolute() or ".." in Path(self.subdirectory).parts):
            raise ValueError("Skill subdirectory must stay inside its repository")
        for relative in self.required_files:
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("required_files must be safe relative paths")
        return self

    @property
    def target_name(self) -> str:
        return self.directory_name or self.name


class SkillLock(BaseModel):
    version: int = LOCK_VERSION
    skills: list[SkillLockEntry]

    @model_validator(mode="after")
    def unique_names(self) -> SkillLock:
        names = [item.name for item in self.skills]
        if len(names) != len(set(names)):
            raise ValueError("Skill lock contains duplicate names")
        return self


class SkillReceipt(BaseModel):
    name: str
    source_url: str
    requested_revision: str
    resolved_commit: str
    content_hash: str


class SkillStatus(BaseModel):
    name: str
    required: bool
    install_enabled: bool
    state: str
    path: str | None = None
    content_hash: str | None = None
    resolved_commit: str | None = None
    problems: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class SkillInstallResult:
    status: SkillStatus
    changed: bool


class SkillDeploymentError(RuntimeError):
    pass


class SkillDeploymentManager:
    """Explicit deployment-time Skill installer and integrity checker.

    Application jobs never call this installer. Operators run it during setup,
    then the application consumes the verified, read-only directories.
    """

    def __init__(self, lock_path: Path, install_root: Path) -> None:
        self.lock_path = lock_path.resolve()
        self.install_root = install_root.resolve()
        self.lock = SkillLock.model_validate_json(self.lock_path.read_text(encoding="utf-8"))
        self._entries = {entry.name: entry for entry in self.lock.skills}

    def list(self) -> list[SkillStatus]:
        return [self.check(entry.name) for entry in self.lock.skills]

    def check(self, name: str) -> SkillStatus:
        entry = self._entry(name)
        directory = self.resolve_installed_path(entry)
        if directory is None:
            return SkillStatus(
                name=name,
                required=entry.required,
                install_enabled=entry.install_enabled,
                state="missing",
                problems=["Skill directory was not found"],
            )
        problems = [
            f"Missing required file: {relative}"
            for relative in entry.required_files
            if not (directory / relative).is_file()
        ]
        receipt = self._read_receipt(directory)
        content_hash = self.directory_hash(directory)
        resolved_commit = receipt.resolved_commit if receipt else None
        if entry.install_enabled and receipt is None:
            problems.append("Installable Skill has no deployment receipt")
        if receipt and receipt.name != entry.name:
            problems.append("Installation receipt belongs to a different Skill")
        if receipt and receipt.content_hash != content_hash:
            problems.append("Installed Skill content differs from its receipt")
        if (
            receipt
            and PINNED_REVISION.fullmatch(entry.revision or "")
            and receipt.resolved_commit != entry.revision
        ):
            problems.append("Installed commit differs from lockfile revision")
        for dependency in entry.dependencies:
            dependency_path = self.resolve_installed_path(self._entry(dependency.name))
            if dependency_path is None:
                problems.append(f"Missing dependency: {dependency.name}")
        return SkillStatus(
            name=name,
            required=entry.required,
            install_enabled=entry.install_enabled,
            state="unmanaged" if problems == ["Installable Skill has no deployment receipt"] else (
                "invalid" if problems else "ready"
            ),
            path=str(directory),
            content_hash=content_hash,
            resolved_commit=resolved_commit,
            problems=problems,
        )

    def doctor(self, *, required_only: bool = False) -> tuple[bool, list[SkillStatus]]:
        statuses = self.list()
        considered = [item for item in statuses if item.required or not required_only]
        return all(item.state == "ready" for item in considered), statuses

    def install(self, name: str, *, force: bool = False) -> SkillInstallResult:
        return self._install(name, force=force, chain=())

    def _install(
        self,
        name: str,
        *,
        force: bool,
        chain: tuple[str, ...],
    ) -> SkillInstallResult:
        if name in chain:
            cycle = " -> ".join((*chain, name))
            raise SkillDeploymentError(f"Skill dependency cycle: {cycle}")
        entry = self._entry(name)
        if not entry.install_enabled:
            raise SkillDeploymentError(
                f"{name} has no trusted install source; provide it manually and run check"
            )
        for dependency in entry.dependencies:
            dependency_status = self.check(dependency.name)
            if dependency_status.state != "ready" or force:
                self._install(
                    dependency.name,
                    force=force,
                    chain=(*chain, name),
                )
        self._validate_source_url(entry.source_url or "")
        current = self.check(name)
        if current.state == "ready" and not force:
            return SkillInstallResult(status=current, changed=False)
        self.install_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="metaclass-skill-") as temporary:
            checkout = Path(temporary) / "repository"
            self._git("clone", "--filter=blob:none", "--no-checkout", entry.source_url or "", str(checkout))
            self._git("-C", str(checkout), "checkout", "--detach", entry.revision or "")
            resolved_commit = self._git(
                "-C", str(checkout), "rev-parse", "HEAD", capture=True
            ).strip()
            if PINNED_REVISION.fullmatch(entry.revision or "") and resolved_commit != entry.revision:
                raise SkillDeploymentError("Resolved commit does not match pinned revision")
            source = checkout / entry.subdirectory if entry.subdirectory else checkout
            if not source.is_dir():
                raise SkillDeploymentError(f"Skill subdirectory does not exist: {entry.subdirectory}")
            staged = self.install_root / f".{entry.target_name}.{uuid4().hex}.staged"
            shutil.copytree(source, staged, ignore=shutil.ignore_patterns(".git"))
            self._validate_required_files(entry, staged)
            receipt = SkillReceipt(
                name=entry.name,
                source_url=entry.source_url or "",
                requested_revision=entry.revision or "",
                resolved_commit=resolved_commit,
                content_hash=self.directory_hash(staged),
            )
            (staged / RECEIPT_NAME).write_text(receipt.model_dump_json(indent=2), encoding="utf-8")
            target = self.install_root / entry.target_name
            backup = self.install_root / f".{entry.target_name}.{uuid4().hex}.backup"
            try:
                if target.exists():
                    os.replace(target, backup)
                os.replace(staged, target)
                shutil.rmtree(backup, ignore_errors=True)
            except Exception:
                if not target.exists() and backup.exists():
                    os.replace(backup, target)
                shutil.rmtree(staged, ignore_errors=True)
                raise
        return SkillInstallResult(status=self.check(name), changed=True)

    def install_all(self, *, required_only: bool = False, force: bool = False) -> list[SkillInstallResult]:
        results = []
        for entry in self.lock.skills:
            if required_only and not entry.required:
                continue
            if not entry.install_enabled:
                results.append(SkillInstallResult(status=self.check(entry.name), changed=False))
                continue
            results.append(self.install(entry.name, force=force))
        return results

    def resolve_installed_path(self, entry: SkillLockEntry) -> Path | None:
        candidates = []
        if entry.environment_variable and os.getenv(entry.environment_variable):
            candidates.append(Path(os.environ[entry.environment_variable]))
        candidates.extend(
            [
                self.install_root / entry.target_name,
                Path.cwd() / "skills" / entry.target_name,
                Path.home() / ".codex" / "skills" / entry.target_name,
                Path.home() / ".agents" / "skills" / entry.target_name,
            ]
        )
        for candidate in candidates:
            if candidate.is_dir():
                return candidate.resolve()
        return None

    @staticmethod
    def directory_hash(directory: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(item for item in directory.rglob("*") if item.is_file()):
            if path.name == RECEIPT_NAME or ".git" in path.parts:
                continue
            digest.update(path.relative_to(directory).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
        return f"sha256:{digest.hexdigest()}"

    def _entry(self, name: str) -> SkillLockEntry:
        entry = self._entries.get(name)
        if entry is None:
            raise SkillDeploymentError(f"Unknown Skill: {name}")
        return entry

    @staticmethod
    def _read_receipt(directory: Path) -> SkillReceipt | None:
        path = directory / RECEIPT_NAME
        if not path.is_file():
            return None
        try:
            return SkillReceipt.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError:
            return None

    @staticmethod
    def _validate_required_files(entry: SkillLockEntry, directory: Path) -> None:
        missing = [relative for relative in entry.required_files if not (directory / relative).is_file()]
        if missing:
            raise SkillDeploymentError(f"Installed Skill is incomplete: {', '.join(missing)}")

    @staticmethod
    def _validate_source_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in {"github.com", "www.github.com"}:
            raise SkillDeploymentError("Skill source must be an HTTPS GitHub repository")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise SkillDeploymentError("Skill source URL must not contain credentials or query data")

    @staticmethod
    def _git(*arguments: str, capture: bool = False) -> str:
        try:
            completed = subprocess.run(
                ["git", *arguments],
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            detail = getattr(exc, "stderr", "") or str(exc)
            raise SkillDeploymentError(f"Git operation failed: {detail.strip()}") from exc
        return completed.stdout if capture else ""
