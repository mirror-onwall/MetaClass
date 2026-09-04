import json
from pathlib import Path

import pytest

from metaclass.deployment.skill_manager import (
    RECEIPT_NAME,
    SkillDeploymentError,
    SkillDeploymentManager,
)


def write_lock(path: Path, skills: list[dict]) -> Path:
    path.write_text(json.dumps({"version": 1, "skills": skills}), encoding="utf-8")
    return path


def test_check_accepts_manually_installed_skill(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    directory = root / "paper-analyze"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("# Paper Analyze", encoding="utf-8")
    lock = write_lock(
        tmp_path / "skills.lock.json",
        [{"name": "paper-analyze", "install_enabled": False}],
    )

    status = SkillDeploymentManager(lock, root).check("paper-analyze")

    assert status.state == "ready"
    assert status.content_hash.startswith("sha256:")


def test_installable_manual_copy_is_reported_as_unmanaged(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    directory = root / "example"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("# Example", encoding="utf-8")
    lock = write_lock(
        tmp_path / "skills.lock.json",
        [
            {
                "name": "example",
                "source_url": "https://github.com/example/skills.git",
                "revision": "a" * 40,
            }
        ],
    )

    status = SkillDeploymentManager(lock, root).check("example")

    assert status.state == "unmanaged"
    assert status.problems == ["Installable Skill has no deployment receipt"]


def test_check_reports_missing_dependency(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    directory = root / "nature-paper2ppt"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("# Nature", encoding="utf-8")
    lock = write_lock(
        tmp_path / "skills.lock.json",
        [
            {"name": "shared-fixture", "required": False, "install_enabled": False},
            {
                "name": "nature-paper2ppt",
                "required": False,
                "install_enabled": False,
                "dependencies": [{"name": "shared-fixture"}],
            },
        ],
    )

    status = SkillDeploymentManager(lock, root).check("nature-paper2ppt")

    assert status.state == "invalid"
    assert status.problems == ["Missing dependency: shared-fixture"]


def test_install_clones_subdirectory_and_writes_receipt(tmp_path: Path, monkeypatch) -> None:
    commit = "a" * 40
    root = tmp_path / "skills"
    lock = write_lock(
        tmp_path / "skills.lock.json",
        [
            {
                "name": "example",
                "source_url": "https://github.com/example/skills.git",
                "revision": commit,
                "subdirectory": "skills/example",
                "required_files": ["SKILL.md", "manifest.yaml"],
            }
        ],
    )

    def fake_git(*arguments: str, capture: bool = False) -> str:
        if arguments[0] == "clone":
            checkout = Path(arguments[-1])
            source = checkout / "skills" / "example"
            source.mkdir(parents=True)
            (source / "SKILL.md").write_text("# Example", encoding="utf-8")
            (source / "manifest.yaml").write_text("version: 1", encoding="utf-8")
        return commit if capture else ""

    monkeypatch.setattr(SkillDeploymentManager, "_git", staticmethod(fake_git))
    manager = SkillDeploymentManager(lock, root)

    result = manager.install("example")

    assert result.changed is True
    assert result.status.state == "ready"
    assert (root / "example" / RECEIPT_NAME).is_file()
    assert not list(root.glob(".*.staged"))


def test_install_installs_dependencies_first(tmp_path: Path, monkeypatch) -> None:
    commit = "b" * 40
    root = tmp_path / "skills"
    lock = write_lock(
        tmp_path / "skills.lock.json",
        [
            {
                "name": "pptx",
                "source_url": "https://github.com/anthropics/skills.git",
                "revision": commit,
                "subdirectory": "skills/pptx",
            },
            {
                "name": "academic-pptx",
                "source_url": "https://github.com/example/academic-pptx.git",
                "revision": commit,
                "dependencies": [{"name": "pptx"}],
            },
        ],
    )
    installed: list[str] = []

    def fake_git(*arguments: str, capture: bool = False) -> str:
        if arguments[0] == "clone":
            checkout = Path(arguments[-1])
            if "anthropics/skills" in arguments[-2]:
                source = checkout / "skills" / "pptx"
                installed.append("pptx")
            else:
                source = checkout
                installed.append("academic-pptx")
            source.mkdir(parents=True)
            (source / "SKILL.md").write_text("# Skill", encoding="utf-8")
        return commit if capture else ""

    monkeypatch.setattr(SkillDeploymentManager, "_git", staticmethod(fake_git))

    result = SkillDeploymentManager(lock, root).install("academic-pptx")

    assert result.status.state == "ready"
    assert installed == ["pptx", "academic-pptx"]
    assert (root / "pptx" / RECEIPT_NAME).is_file()


def test_install_rejects_untrusted_source(tmp_path: Path) -> None:
    lock = write_lock(
        tmp_path / "skills.lock.json",
        [
            {
                "name": "unsafe",
                "source_url": "http://example.com/skill.git",
                "revision": "a" * 40,
            }
        ],
    )

    with pytest.raises(SkillDeploymentError, match="HTTPS GitHub"):
        SkillDeploymentManager(lock, tmp_path / "skills").install("unsafe")


def test_verify_only_skill_cannot_be_downloaded(tmp_path: Path) -> None:
    lock = write_lock(
        tmp_path / "skills.lock.json",
        [{"name": "manual", "install_enabled": False}],
    )

    with pytest.raises(SkillDeploymentError, match="no trusted install source"):
        SkillDeploymentManager(lock, tmp_path / "skills").install("manual")
