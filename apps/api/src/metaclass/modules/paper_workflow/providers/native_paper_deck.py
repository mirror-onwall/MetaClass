from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import ClassVar

from metaclass.core.config import settings
from metaclass.modules.paper_workflow.paper_deck_artifact_adapter import (
    PaperDeckArtifactAdapter,
    PaperDeckArtifactError,
)
from metaclass.modules.paper_workflow.providers.base import PaperProviderContext
from metaclass.modules.paper_workflow.runtime import (
    CodexSkillInvocation,
    CodexSkillRuntime,
    SkillRuntime,
)
from metaclass.modules.paper_workflow.schemas import (
    ComposedStage,
    PaperPresentationArtifact,
    StageExecutionReport,
    StageStatus,
)
from metaclass.modules.paper_workflow.stage_cache import StageCache


class NativePaperDeckError(RuntimeError):
    """Raised when native paper-deck inputs or raster-first outputs are invalid."""


class NativePaperDeckProvider:
    """Run the complete upstream paper-deck raster-first workflow as one stage."""

    name = "native_paper_deck"
    skill_name = "paper-deck"
    prompt_version = "native-paper-deck-v1"
    schema_version = "native-paper-deck-artifacts-v1"
    timeout_seconds = 3600

    _required_files: ClassVar[set[str]] = {
        "analysis.md",
        "deck-brief.md",
        "outline.md",
        "generation-log.md",
        "presentation.pdf",
    }

    def __init__(
        self,
        runtime: SkillRuntime | None = None,
        *,
        skill_directory: Path | None = None,
    ) -> None:
        self.runtime = runtime or CodexSkillRuntime()
        self.skill_directory = skill_directory

    def run(self, context: PaperProviderContext) -> PaperPresentationArtifact:
        skill_directory = self.skill_directory or self._resolve_skill_directory()
        skill_version = self._directory_hash(skill_directory)
        provider_input = context.workspace / "provider_input"
        provider_output = context.workspace / "provider_output"
        prompt_path = context.workspace / "native_paper_deck_prompt.md"

        input_paths = self._prepare_inputs(context, provider_input)
        self._write_prompt(prompt_path)
        cache = StageCache(context.workspace)
        input_hash = cache.input_hash(
            skill_version=skill_version,
            prompt_version=self.prompt_version,
            schema_version=self.schema_version,
            request_subset=context.request.model_dump(mode="json"),
            input_files=[*input_paths, prompt_path],
        )

        def validate_cached(outputs: dict[str, str]) -> bool:
            if not cache.outputs_exist(outputs):
                return False
            try:
                artifact = self._artifact(context.workspace, provider_output)
                PaperDeckArtifactAdapter().adapt(artifact, workspace=context.workspace)
            except (OSError, ValueError, NativePaperDeckError, PaperDeckArtifactError):
                return False
            return True

        def restore(_: dict[str, str]) -> PaperPresentationArtifact:
            return self._artifact(context.workspace, provider_output)

        def execute() -> tuple[PaperPresentationArtifact, dict[str, str]]:
            provider_output.mkdir(parents=True, exist_ok=True)
            recovered_report = self._successful_runtime_report(
                context.workspace,
                input_hash=input_hash,
            )
            if recovered_report is not None:
                try:
                    artifact = self._artifact(context.workspace, provider_output)
                    PaperDeckArtifactAdapter().adapt(artifact, workspace=context.workspace)
                except (OSError, ValueError, PaperDeckArtifactError):
                    pass
                else:
                    execution_report = provider_output / "execution_report.json"
                    execution_report.write_text(
                        recovered_report.model_dump_json(indent=2), encoding="utf-8"
                    )
                    return artifact, self._artifact_outputs(
                        artifact,
                        execution_report=execution_report,
                        workspace=context.workspace,
                    )
            report = self.runtime.run(
                CodexSkillInvocation(
                    stage=ComposedStage.GENERATION,
                    skill_name=self.skill_name,
                    skill_directory=skill_directory,
                    skill_version=skill_version,
                    prompt_version=self.prompt_version,
                    input_hash=input_hash,
                    workspace=context.workspace,
                    prompt_path=prompt_path,
                    input_paths=tuple(input_paths),
                    output_directory=provider_output,
                    expected_outputs=tuple(sorted(self._required_files)),
                    attempt=context.current_attempt(ComposedStage.GENERATION),
                    timeout_seconds=self.timeout_seconds,
                    network_enabled=False,
                    cancel_event=None,
                )
            )
            if report.status != StageStatus.SUCCEEDED:
                raise NativePaperDeckError("paper-deck runtime execution failed")
            artifact = self._artifact(context.workspace, provider_output)
            PaperDeckArtifactAdapter().adapt(artifact, workspace=context.workspace)
            execution_report = provider_output / "execution_report.json"
            execution_report.write_text(report.model_dump_json(indent=2), encoding="utf-8")
            return artifact, self._artifact_outputs(
                artifact,
                execution_report=execution_report,
                workspace=context.workspace,
            )

        return context.run_stage(
            stage=ComposedStage.GENERATION,
            input_hash=input_hash,
            skill_name=self.skill_name,
            skill_version=skill_version,
            prompt_version=self.prompt_version,
            execute=execute,
            validate_cached=validate_cached,
            restore=restore,
        )

    @staticmethod
    def _artifact_outputs(
        artifact: PaperPresentationArtifact,
        *,
        execution_report: Path,
        workspace: Path,
    ) -> dict[str, str]:
        outputs = {
                "analysis": artifact.analysis_path,
                "deck_brief": artifact.deck_brief_path,
                "outline": artifact.outline_path,
                "generation_log": artifact.generation_log_path,
                "presentation_pdf": artifact.presentation_pdf_path,
            "execution_report": str(execution_report.relative_to(workspace)),
        }
        if artifact.debug_pptx_path:
            outputs["debug_presentation_pptx"] = artifact.debug_pptx_path
        return outputs

    @staticmethod
    def _successful_runtime_report(
        workspace: Path,
        *,
        input_hash: str,
    ) -> StageExecutionReport | None:
        log_root = workspace / "runtime_logs"
        candidates = sorted(
            log_root.glob("generation_*/execution_report.json"),
            reverse=True,
        )
        for path in candidates:
            try:
                report = StageExecutionReport.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                continue
            if (
                report.status == StageStatus.SUCCEEDED
                and report.validation_passed is True
                and report.input_hash.removeprefix("sha256:")
                == input_hash.removeprefix("sha256:")
            ):
                return report
        return None

    def repair_slides(
        self,
        context: PaperProviderContext,
        *,
        directives: list[dict[str, object]],
    ) -> PaperPresentationArtifact:
        """Ask the upstream Skill to repair only named raster pages and rebuild the PDF."""
        if not directives:
            raise NativePaperDeckError("targeted repair requires at least one directive")
        output = context.workspace / "provider_output"
        artifact = self._artifact(context.workspace, output)
        skill_directory = self.skill_directory or self._resolve_skill_directory()
        skill_version = self._directory_hash(skill_directory)
        repair_path = context.workspace / "native_paper_deck_targeted_repair.md"
        repair_path.write_text(
            """继续执行同一个 paper-deck raster-first 任务，只返修指定页面。

读取下方 JSON directives。对每个页面：修改其现有 prompt，删除或替换无法由论文原文、
Figure 或 Table 验证的内容；只重新生成这些页面的 image。不得改变其他页面的图片、顺序、
标题或叙事。随后用完整 images/ 重新合成 presentation.pdf，并更新对应 prompt 与
generation-log.md。保留 analysis.md、deck-brief.md、outline.md。不要生成 MetaClass
PresentationPlan，不要进入课堂阶段。

directives:
"""
            + json.dumps(directives, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        digest = hashlib.sha256()
        digest.update(repair_path.read_bytes())
        for relative in (
            artifact.presentation_pdf_path,
            artifact.outline_path,
            artifact.generation_log_path,
        ):
            digest.update((context.workspace / relative).read_bytes())
        checkpoint = context.checkpoint.stages.get(ComposedStage.GENERATION)
        attempt = (checkpoint.attempt if checkpoint else 1) + 1
        report = self.runtime.run(
            CodexSkillInvocation(
                stage=ComposedStage.GENERATION,
                skill_name=self.skill_name,
                skill_directory=skill_directory,
                skill_version=skill_version,
                prompt_version=f"{self.prompt_version}-targeted-repair-v1",
                input_hash=digest.hexdigest(),
                workspace=context.workspace,
                prompt_path=repair_path,
                input_paths=(
                    repair_path,
                    context.workspace / artifact.presentation_pdf_path,
                    context.workspace / artifact.outline_path,
                    context.workspace / artifact.generation_log_path,
                ),
                output_directory=output,
                expected_outputs=tuple(sorted(self._required_files)),
                attempt=attempt,
                timeout_seconds=self.timeout_seconds,
                network_enabled=False,
                cancel_event=None,
            )
        )
        if report.status != StageStatus.SUCCEEDED:
            raise NativePaperDeckError("paper-deck targeted page repair failed")
        repaired = self._artifact(context.workspace, output)
        PaperDeckArtifactAdapter().adapt(repaired, workspace=context.workspace)
        (output / "targeted_repair_execution_report.json").write_text(
            report.model_dump_json(indent=2), encoding="utf-8"
        )
        return repaired

    def _prepare_inputs(
        self,
        context: PaperProviderContext,
        provider_input: Path,
    ) -> list[Path]:
        source_root = context.workspace / "source"
        analysis = self._resolve_analysis(context.workspace)
        resolved_request = context.workspace / "resolved_request.json"
        sources = {
            "paper.pdf": self._source_file(source_root, context.source_bundle.pdf_path),
            "paper_content.md": self._source_file(
                source_root, context.source_bundle.paper_content_path
            ),
            "paper_source.json": self._source_file(
                source_root, context.source_bundle.paper_source_path
            ),
            "paper_analysis.json": analysis,
            "resolved_request.json": resolved_request,
        }
        missing = [name for name, path in sources.items() if not path.is_file()]
        if missing:
            raise NativePaperDeckError(f"paper-deck inputs are missing: {sorted(missing)}")

        provider_input.mkdir(parents=True, exist_ok=True)
        prepared: list[Path] = []
        for name, source in sources.items():
            destination = provider_input / name
            self._copy_file(source, destination)
            prepared.append(destination)

        source_assets = self._source_assets(context, source_root)
        target_assets = provider_input / "assets"
        self._copy_tree_fail_closed(source_assets, target_assets)
        prepared.extend(sorted(path for path in target_assets.rglob("*") if path.is_file()))
        return prepared

    @staticmethod
    def _artifact(
        workspace: Path,
        output: Path,
    ) -> PaperPresentationArtifact:
        relative = output.relative_to(workspace)
        debug_pptx = output / "presentation.pptx"
        return PaperPresentationArtifact(
            provider=NativePaperDeckProvider.name,
            presentation_pdf_path=(relative / "presentation.pdf").as_posix(),
            source_images_dir=(relative / "images").as_posix(),
            analysis_path=(relative / "analysis.md").as_posix(),
            deck_brief_path=(relative / "deck-brief.md").as_posix(),
            outline_path=(relative / "outline.md").as_posix(),
            prompts_dir=(relative / "prompts").as_posix(),
            generation_log_path=(relative / "generation-log.md").as_posix(),
            debug_pptx_path=(
                debug_pptx.relative_to(workspace).as_posix() if debug_pptx.is_file() else None
            ),
        )

    @staticmethod
    def _write_prompt(path: Path) -> None:
        path.write_text(
            """完整执行 paper-deck Skill 的原生 raster-first 工作流。

输入目录是 provider_input/，包含 paper.pdf、paper_content.md、paper_source.json、
paper_analysis.json、assets/ 和 resolved_request.json。

要求：
1. 原样遵循 paper-deck 的分析、deck brief、outline、逐页 prompt、真实 raster
   生图、质量检查和合成流程。
2. 允许并优先使用论文原始 Figure；不得凭空改写论文图表、数字或结论。
3. 不要请求用户确认；resolved_request.json 已代表本次生成授权和设置。
4. 所有结果只能写入 provider_output/。必须生成：analysis.md、deck-brief.md、
   outline.md、prompts/、images/、generation-log.md、presentation.pdf。
5. 正常合成流程若生成 presentation.pptx，则将它作为调试产物保留；平台不会消费它，
   不要求其中的页面元素可编辑，缺失也不应导致任务失败。
6. 不要创建或消费 MetaClass PresentationPlan，不要调用 CodexPPTProvider，
   不要执行可编辑层适配，不要进入课堂、知识树、讲稿或题库阶段。
7. 最终 PDF 页数、images 数量、outline 页数和 prompt 数量必须一致；
   generation-log.md 必须覆盖每一页及其真实生图后端。
""",
            encoding="utf-8",
        )

    @staticmethod
    def _resolve_analysis(workspace: Path) -> Path:
        candidates = [
            workspace / "stages/01_analysis/output/paper_analysis.json",
            workspace / "paper_analysis.json",
            workspace / "provider_input" / "paper_analysis.json",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        raise NativePaperDeckError("paper_analysis.json is required before native paper-deck")

    @staticmethod
    def _source_file(source_root: Path, value: str) -> Path:
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise NativePaperDeckError("paper source paths must stay inside source/")
        direct = source_root / relative
        nested = source_root / relative.name
        return direct if direct.is_file() else nested

    @staticmethod
    def _source_assets(context: PaperProviderContext, source_root: Path) -> Path:
        relative = Path(context.source_bundle.asset_directory)
        if relative.is_absolute() or ".." in relative.parts:
            raise NativePaperDeckError("paper asset path must stay inside source/")
        source = source_root / relative
        if not source.is_dir():
            raise NativePaperDeckError("paper source assets directory is missing")
        return source

    @staticmethod
    def _copy_file(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file() and NativePaperDeckProvider._file_hash(source) == (
            NativePaperDeckProvider._file_hash(destination)
        ):
            return
        shutil.copy2(source, destination)

    @staticmethod
    def _copy_tree_fail_closed(source: Path, destination: Path) -> None:
        source_files = {
            path.relative_to(source): path for path in source.rglob("*") if path.is_file()
        }
        if destination.exists():
            destination_files = {
                path.relative_to(destination): path
                for path in destination.rglob("*")
                if path.is_file()
            }
            unexpected = destination_files.keys() - source_files.keys()
            if unexpected:
                raise NativePaperDeckError(
                    f"provider_input/assets contains stale files: {sorted(map(str, unexpected))}"
                )
        destination.mkdir(parents=True, exist_ok=True)
        for relative, path in source_files.items():
            NativePaperDeckProvider._copy_file(path, destination / relative)

    @staticmethod
    def _resolve_skill_directory() -> Path:
        configured = os.getenv("METACLASS_PAPER_DECK_SKILL_DIR")
        managed_root = Path(os.getenv("METACLASS_SKILL_ROOT", settings.skill_root))
        candidates = [
            managed_root / "paper-deck",
            Path.cwd() / ".agents/skills/paper-deck",
            Path.cwd() / "skills/paper-deck",
            Path.home() / ".codex/skills/paper-deck",
            Path.home() / ".agents/skills/paper-deck",
        ]
        if configured:
            candidates.insert(0, Path(configured).expanduser())
        for candidate in candidates:
            if (candidate / "SKILL.md").is_file():
                return candidate.resolve()
        raise NativePaperDeckError(
            "paper-deck Skill is not installed; configure METACLASS_PAPER_DECK_SKILL_DIR"
        )

    @staticmethod
    def _directory_hash(directory: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(item for item in directory.rglob("*") if item.is_file()):
            digest.update(str(path.relative_to(directory)).encode("utf-8"))
            digest.update(path.read_bytes())
        return f"sha256:{digest.hexdigest()}"

    @staticmethod
    def _file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
