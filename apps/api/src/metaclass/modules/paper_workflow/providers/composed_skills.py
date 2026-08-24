import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path

import fitz
from pydantic import ValidationError

from metaclass.core.schemas import utc_now
from metaclass.modules.paper_workflow.artifact_normalizer import (
    PaperArtifactNormalizer,
    StagedPaperArtifacts,
)
from metaclass.modules.paper_workflow.figure_catalog import (
    ExtractPaperImagesAdapter,
    FigureCatalogMerger,
    MinerUFigureCatalogBuilder,
    validate_figure_catalog,
    write_figure_catalog,
)
from metaclass.modules.paper_workflow.presentation_stages import (
    AcademicOutlineAdapter,
    AcademicPptxGenerateAdapter,
    OutlineEvidenceError,
    OutlineNarrativeError,
    OutlineStructureError,
    RepairableGenerationError,
    file_hash,
    validate_and_render_pptx,
    validate_outline_contract,
)
from metaclass.modules.paper_workflow.providers.base import PaperProviderContext
from metaclass.modules.paper_workflow.runtime import (
    CodexSkillInvocation,
    CodexSkillRuntime,
    SkillRuntime,
)
from metaclass.modules.paper_workflow.schemas import (
    ComposedStage,
    FigureCatalog,
    PaperAnalysis,
    PaperArtifactBundle,
    PresentationOutline,
    SlideEvidence,
    StageExecutionReport,
    StageStatus,
    ValidationIssue,
    ValidationSeverity,
)
from metaclass.modules.paper_workflow.stage_cache import StageCache
from metaclass.modules.paper_workflow.validators import (
    PaperArtifactValidationError,
    PaperArtifactValidator,
)


class PaperAnalysisStageError(RuntimeError):
    pass


class _AnalysisStructureError(ValueError):
    pass


class _AnalysisEvidenceError(ValueError):
    pass


class ComposedSkillsProvider:
    """Run the checkpointed analysis and figure-catalog milestones."""

    name = "composed_skills"
    skill_name = "paper-analyze"
    figure_skill_name = "extract-paper-images"
    prompt_version = "paper-analyze-v1"
    schema_version = "paper-analysis-v1"
    pdf_fallback_warning = "source_pdf_unreadable_used_paper_content_markdown"

    def __init__(
        self,
        runtime: SkillRuntime | None = None,
        *,
        skill_directory: Path | None = None,
        figure_skill_directory: Path | None = None,
        outline_skill_directory: Path | None = None,
        generation_skill_directory: Path | None = None,
        register_presentation: Callable[[Path, str, str], str] | None = None,
    ) -> None:
        self.runtime = runtime or CodexSkillRuntime()
        self.skill_directory = skill_directory
        self.figure_skill_directory = figure_skill_directory
        self.outline_skill_directory = outline_skill_directory
        self.generation_skill_directory = generation_skill_directory
        self.register_presentation = register_presentation
        self.validator = PaperArtifactValidator()
        self.prompt_directory = Path(__file__).resolve().parents[1] / "prompts"

    def run(self, context: PaperProviderContext) -> PaperArtifactBundle:
        skill_directory = self.skill_directory or self._resolve_skill_directory()
        skill_version = self._directory_hash(skill_directory)
        stage_root = context.workspace / "stages" / "01_analysis"
        output = stage_root / "output"
        output.mkdir(parents=True, exist_ok=True)
        runtime_schema_path = stage_root / "runtime_result_schema.json"
        analysis_schema_path = stage_root / "paper_analysis_schema.json"
        self._write_runtime_result_schema(runtime_schema_path)
        analysis_schema_path.write_text(
            json.dumps(PaperAnalysis.model_json_schema(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        source_dir = context.workspace / "source"
        resolved_request = context.workspace / "resolved_request.json"
        required_inputs = [
            source_dir / "paper.pdf",
            source_dir / "paper_source.json",
            source_dir / "paper_content.md",
            resolved_request,
        ]
        cache = StageCache(context.workspace)
        input_hash = cache.input_hash(
            skill_version=skill_version,
            prompt_version=self.prompt_version,
            schema_version=self.schema_version,
            request_subset=context.request.model_dump(mode="json"),
            input_files=required_inputs,
        )

        def validate_cached(outputs: dict[str, str]) -> bool:
            if not cache.outputs_exist(outputs):
                return False
            try:
                analysis = self._read_analysis(context.workspace / outputs["analysis_json"])
                self._validate_final(context, analysis, skill_directory, output)
                report = StageExecutionReport.model_validate_json(
                    (context.workspace / outputs["execution_report"]).read_text(encoding="utf-8")
                )
                return report.status == StageStatus.SUCCEEDED
            except (KeyError, OSError, ValueError):
                return False

        def restore(outputs: dict[str, str]) -> PaperAnalysis:
            return self._read_analysis(context.workspace / outputs["analysis_json"])

        def execute() -> tuple[PaperAnalysis, dict[str, str]]:
            analysis = self._execute_analysis(
                context=context,
                skill_directory=skill_directory,
                skill_version=skill_version,
                stage_root=stage_root,
                output=output,
                runtime_schema_path=runtime_schema_path,
                analysis_schema_path=analysis_schema_path,
                input_hash=input_hash,
            )
            outputs = {
                "analysis_markdown": str(
                    (output / "paper_analysis.md").relative_to(context.workspace)
                ),
                "analysis_json": str(
                    (output / "paper_analysis.json").relative_to(context.workspace)
                ),
                "execution_report": str(
                    (output / "execution_report.json").relative_to(context.workspace)
                ),
            }
            return analysis, outputs

        analysis = context.run_stage(
            stage=ComposedStage.ANALYSIS,
            input_hash=input_hash,
            skill_name=self.skill_name,
            skill_version=skill_version,
            prompt_version=self.prompt_version,
            execute=execute,
            validate_cached=validate_cached,
            restore=restore,
        )
        figures = self._run_figures(context, analysis)
        outline, evidence = self._run_outline(context, analysis, figures)
        return self._run_generation(context, analysis, figures, outline, evidence)

    def _run_outline(
        self,
        context: PaperProviderContext,
        analysis: PaperAnalysis,
        figures: FigureCatalog,
    ) -> tuple[PresentationOutline, SlideEvidence]:
        stage_root = context.workspace / "stages/03_outline"
        output = stage_root / "output"
        output.mkdir(parents=True, exist_ok=True)
        outline_schema, evidence_schema, runtime_schema = (
            AcademicOutlineAdapter.write_contract_schemas(stage_root)
        )
        skill_directory = self.outline_skill_directory or self._resolve_skill_directory_for(
            "academic-pptx", "METACLASS_ACADEMIC_PPTX_SKILL_DIR"
        )
        skill_version = self._directory_hash(skill_directory)
        inputs = [
            context.workspace / "stages/01_analysis/output/paper_analysis.json",
            context.workspace / "stages/02_figures/output/figures.json",
            context.workspace / "resolved_request.json",
            context.workspace / "source/paper_source.json",
            outline_schema,
            evidence_schema,
        ]
        cache = StageCache(context.workspace)
        input_hash = cache.input_hash(
            skill_version=skill_version,
            prompt_version=AcademicOutlineAdapter.prompt_version,
            schema_version="presentation-outline-v2",
            request_subset=context.request.model_dump(mode="json"),
            input_files=inputs,
        )

        def load() -> tuple[PresentationOutline, SlideEvidence]:
            return (
                PresentationOutline.model_validate_json(
                    (output / "presentation_outline.json").read_text(encoding="utf-8")
                ),
                SlideEvidence.model_validate_json(
                    (output / "slide_evidence.json").read_text(encoding="utf-8")
                ),
            )

        def validate_cached(outputs: dict[str, str]) -> bool:
            try:
                if not cache.outputs_exist(outputs):
                    return False
                current_outline, current_evidence = load()
                validate_outline_contract(
                    current_outline,
                    current_evidence,
                    analysis=analysis,
                    figures=figures,
                    duration_minutes=context.request.duration_minutes or 15,
                )
                return True
            except (OSError, ValueError):
                return False

        def execute():
            adapter = AcademicOutlineAdapter(self.runtime, skill_directory)
            invocation_attempt = context.current_attempt(ComposedStage.OUTLINE)
            structure_repair_used = False
            evidence_repair_used = False
            last_narrative_error: OutlineNarrativeError | None = None

            def invoke(mode):
                nonlocal invocation_attempt
                report = adapter.run(
                    workspace=context.workspace,
                    output=output,
                    input_hash=input_hash,
                    skill_version=skill_version,
                    attempt=invocation_attempt,
                    outline_schema=outline_schema,
                    evidence_schema=evidence_schema,
                    runtime_schema=runtime_schema,
                    mode=mode,
                )
                invocation_attempt += 1
                if report.status != StageStatus.SUCCEEDED:
                    raise RuntimeError(f"academic-pptx {mode} execution failed")
                return report

            for full_attempt in range(2):
                if full_attempt:
                    self._clear_outline_outputs(output)
                report = invoke("initial" if full_attempt == 0 else "rerun")
                while True:
                    try:
                        current_outline, current_evidence = load()
                    except (OSError, ValueError) as exc:
                        if structure_repair_used:
                            raise OutlineStructureError(
                                "Stage 3 output remains structurally invalid after repair"
                            ) from exc
                        structure_repair_used = True
                        self._write_outline_validation_error(
                            stage_root, category="structure", error=exc
                        )
                        report = invoke("structure_repair")
                        continue

                    try:
                        validate_outline_contract(
                            current_outline,
                            current_evidence,
                            analysis=analysis,
                            figures=figures,
                            duration_minutes=context.request.duration_minutes or 15,
                        )
                    except OutlineStructureError as exc:
                        if structure_repair_used:
                            raise
                        structure_repair_used = True
                        self._write_outline_validation_error(
                            stage_root, category="structure", error=exc
                        )
                        report = invoke("structure_repair")
                        continue
                    except OutlineEvidenceError as exc:
                        if evidence_repair_used:
                            raise
                        evidence_repair_used = True
                        self._write_outline_validation_error(
                            stage_root, category="evidence", error=exc
                        )
                        report = invoke("evidence_repair")
                        continue
                    except OutlineNarrativeError as exc:
                        last_narrative_error = exc
                        self._write_outline_validation_error(
                            stage_root, category="narrative", error=exc
                        )
                        break

                    (output / "execution_report.json").write_text(
                        report.model_dump_json(indent=2), encoding="utf-8"
                    )
                    return (current_outline, current_evidence), {
                        "outline_markdown": str(
                            (output / "outline.md").relative_to(context.workspace)
                        ),
                        "presentation_outline": str(
                            (output / "presentation_outline.json").relative_to(context.workspace)
                        ),
                        "slide_evidence": str(
                            (output / "slide_evidence.json").relative_to(context.workspace)
                        ),
                    }

                if full_attempt == 1:
                    assert last_narrative_error is not None
                    raise last_narrative_error

            raise RuntimeError("academic-pptx outline execution exhausted retries")

        return context.run_stage(
            stage=ComposedStage.OUTLINE,
            input_hash=input_hash,
            skill_name="academic-pptx",
            skill_version=skill_version,
            prompt_version=AcademicOutlineAdapter.prompt_version,
            execute=execute,
            validate_cached=validate_cached,
            restore=lambda _: load(),
        )

    @staticmethod
    def _clear_outline_outputs(output: Path) -> None:
        for name in (
            "outline.md",
            "presentation_outline.json",
            "slide_evidence.json",
            "execution_report.json",
        ):
            (output / name).unlink(missing_ok=True)

    @staticmethod
    def _write_outline_validation_error(
        stage_root: Path,
        *,
        category: str,
        error: Exception,
    ) -> Path:
        path = stage_root / "validation_errors.json"
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "stage": ComposedStage.OUTLINE.value,
                    "category": category,
                    "error_type": error.__class__.__name__,
                    "message": str(error),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        return path

    def _run_generation(
        self,
        context: PaperProviderContext,
        analysis: PaperAnalysis,
        figures: FigureCatalog,
        outline: PresentationOutline,
        evidence: SlideEvidence,
    ) -> PaperArtifactBundle:
        stage_root = context.workspace / "stages/04_generation"
        output = stage_root / "output"
        output.mkdir(parents=True, exist_ok=True)
        skill_directory = self.generation_skill_directory or self._resolve_skill_directory_for(
            "academic-pptx-generate", "METACLASS_ACADEMIC_PPTX_GENERATE_SKILL_DIR"
        )
        skill_version = self._directory_hash(skill_directory)
        inputs = [
            context.workspace / "stages/03_outline/output/outline.md",
            context.workspace / "stages/03_outline/output/presentation_outline.json",
            context.workspace / "stages/03_outline/output/slide_evidence.json",
            context.workspace / "stages/02_figures/output/figures.json",
            *sorted(
                path
                for path in (context.workspace / "stages/02_figures/output/assets").rglob("*")
                if path.is_file()
            ),
        ]
        cache = StageCache(context.workspace)
        input_hash = cache.input_hash(
            skill_version=skill_version,
            prompt_version=AcademicPptxGenerateAdapter.prompt_version,
            schema_version="paper-pptx-generation-v1",
            request_subset={"outline_hash": file_hash(inputs[1])},
            input_files=inputs,
        )

        def validate_cached(outputs: dict[str, str]) -> bool:
            try:
                if not cache.outputs_exist(outputs):
                    return False
                validate_and_render_pptx(output, outline=outline, evidence=evidence)
                return True
            except (OSError, ValueError):
                return False

        def execute():
            adapter = AcademicPptxGenerateAdapter(self.runtime, skill_directory)
            attempt = context.current_attempt(ComposedStage.GENERATION)
            report = adapter.run(
                workspace=context.workspace,
                output=output,
                input_hash=input_hash,
                skill_version=skill_version,
                attempt=attempt,
            )
            if report.status != StageStatus.SUCCEEDED:
                raise RuntimeError("academic-pptx-generate runtime execution failed")
            try:
                validate_and_render_pptx(output, outline=outline, evidence=evidence)
            except RepairableGenerationError:
                repaired = adapter.run(
                    workspace=context.workspace,
                    output=output,
                    input_hash=input_hash,
                    skill_version=skill_version,
                    attempt=attempt + 1,
                    repair=True,
                )
                if repaired.status != StageStatus.SUCCEEDED:
                    raise RuntimeError("academic-pptx-generate repair pass failed")
                report = repaired
                validate_and_render_pptx(output, outline=outline, evidence=evidence)
            (output / "execution_report.json").write_text(
                report.model_dump_json(indent=2), encoding="utf-8"
            )
            return True, {
                "presentation": str((output / "presentation.pptx").relative_to(context.workspace)),
                "slide_plan": str((output / "slide_plan.json").relative_to(context.workspace)),
                "speaker_notes": str(
                    (output / "speaker_notes.json").relative_to(context.workspace)
                ),
                "qa_report": str((output / "qa_report.json").relative_to(context.workspace)),
                "slide_evidence": str(
                    (output / "slide_evidence.json").relative_to(context.workspace)
                ),
                "execution_report": str(
                    (output / "execution_report.json").relative_to(context.workspace)
                ),
            }

        context.run_stage(
            stage=ComposedStage.GENERATION,
            input_hash=input_hash,
            skill_name="academic-pptx-generate",
            skill_version=skill_version,
            prompt_version=AcademicPptxGenerateAdapter.prompt_version,
            execute=execute,
            validate_cached=validate_cached,
            restore=lambda _: True,
        )
        bundle = PaperArtifactNormalizer(self.validator).normalize(
            job_id=context.job_id,
            source_material_id=context.request.material_id,
            request=context.request,
            raw=StagedPaperArtifacts(
                provider=self.name,
                root_path=context.workspace,
            ),
            mode="strict",
        )
        final_pptx = context.workspace / "final" / "presentation.pptx"
        derived_id = (
            self.register_presentation(
                final_pptx,
                "paper-presentation.pptx",
                context.job_id,
            )
            if self.register_presentation
            else f"generated_{context.job_id}"
        )
        return bundle.model_copy(update={"derived_material_id": derived_id})

    def _run_figures(
        self,
        context: PaperProviderContext,
        analysis: PaperAnalysis,
    ) -> FigureCatalog:
        stage_root = context.workspace / "stages" / "02_figures"
        output = stage_root / "output"
        assets = output / "assets"
        output.mkdir(parents=True, exist_ok=True)
        builder = MinerUFigureCatalogBuilder()
        mineru_catalog, initial_decision = builder.build(
            workspace=context.workspace,
            source=context.source_bundle,
            analysis=analysis,
            output_assets=assets,
        )
        duration = context.request.duration_minutes or 15
        decision = builder.decision_for_duration(initial_decision, duration)
        skill_directory: Path | None = None
        skill_version = "deterministic-mineru-selector-v1"
        if decision.needs_extraction:
            skill_directory = self.figure_skill_directory or self._resolve_skill_directory_for(
                self.figure_skill_name,
                "METACLASS_EXTRACT_PAPER_IMAGES_SKILL_DIR",
            )
            skill_version = self._directory_hash(skill_directory)

        source_dir = context.workspace / "source"
        analysis_json = (
            context.workspace / "stages" / "01_analysis" / "output" / "paper_analysis.json"
        )
        input_files = [
            source_dir / "paper.pdf",
            source_dir / "paper_source.json",
            analysis_json,
            context.workspace / "resolved_request.json",
            *sorted(
                path
                for path in (source_dir / context.source_bundle.asset_directory).rglob("*")
                if path.is_file()
            ),
        ]
        cache = StageCache(context.workspace)
        input_hash = cache.input_hash(
            skill_version=skill_version,
            prompt_version=ExtractPaperImagesAdapter.prompt_version,
            schema_version="figure-catalog-v1",
            request_subset={
                "duration_minutes": duration,
                "slide_target": decision.slide_target,
                "required_asset_count": decision.required,
            },
            input_files=input_files,
        )

        def validate_cached(outputs: dict[str, str]) -> bool:
            try:
                catalog = FigureCatalog.model_validate_json(
                    (context.workspace / outputs["figures_json"]).read_text(encoding="utf-8")
                )
                validate_figure_catalog(catalog, output=output, analysis=analysis)
                report = StageExecutionReport.model_validate_json(
                    (context.workspace / outputs["execution_report"]).read_text(encoding="utf-8")
                )
                return report.status == StageStatus.SUCCEEDED and report.validation_passed
            except (KeyError, OSError, ValueError):
                return False

        def restore(outputs: dict[str, str]) -> FigureCatalog:
            return FigureCatalog.model_validate_json(
                (context.workspace / outputs["figures_json"]).read_text(encoding="utf-8")
            )

        def execute() -> tuple[FigureCatalog, dict[str, str]]:
            catalogs: list[tuple[FigureCatalog, Path]] = [(mineru_catalog, output)]
            runtime_report: StageExecutionReport | None = None
            if decision.needs_extraction:
                assert skill_directory is not None
                adapter = ExtractPaperImagesAdapter(self.runtime, skill_directory)
                extracted, runtime_report, extracted_root = adapter.extract(
                    workspace=context.workspace,
                    stage_root=stage_root,
                    input_hash=input_hash,
                    skill_version=skill_version,
                    attempt=context.current_attempt(ComposedStage.FIGURES),
                )
                catalogs.append((extracted, extracted_root))
            catalog = FigureCatalogMerger().merge(
                workspace=context.workspace,
                output=output,
                catalogs=catalogs,
            )
            validate_figure_catalog(catalog, output=output, analysis=analysis)
            write_figure_catalog(output / "figures.json", catalog)
            self._write_figure_report(
                output,
                input_hash=input_hash,
                skill_version=skill_version,
                decision=decision,
                runtime_report=runtime_report,
                figure_count=len(catalog.figures),
                attempt=context.current_attempt(ComposedStage.FIGURES),
            )
            return catalog, {
                "figures_json": str((output / "figures.json").relative_to(context.workspace)),
                "execution_report": str(
                    (output / "execution_report.json").relative_to(context.workspace)
                ),
            }

        return context.run_stage(
            stage=ComposedStage.FIGURES,
            input_hash=input_hash,
            skill_name=(self.figure_skill_name if decision.needs_extraction else "mineru-selector"),
            skill_version=skill_version,
            prompt_version=ExtractPaperImagesAdapter.prompt_version,
            execute=execute,
            validate_cached=validate_cached,
            restore=restore,
        )

    @staticmethod
    def _write_figure_report(
        output: Path,
        *,
        input_hash: str,
        skill_version: str,
        decision,
        runtime_report: StageExecutionReport | None,
        figure_count: int,
        attempt: int,
    ) -> None:
        now = utc_now()
        base = (
            runtime_report.model_dump(mode="python")
            if runtime_report
            else {
                "stage": ComposedStage.FIGURES,
                "skill_name": "mineru-selector",
                "skill_version": skill_version,
                "prompt_version": ExtractPaperImagesAdapter.prompt_version,
                "runtime_version": "deterministic-figure-selector-v1",
                "status": StageStatus.SUCCEEDED,
                "attempt": attempt,
                "started_at": now,
                "finished_at": now,
                "input_hash": input_hash,
                "exit_code": 0,
            }
        )
        report = StageExecutionReport.model_validate(
            {
                **base,
                "status": StageStatus.SUCCEEDED,
                "finished_at": now,
                "validation_passed": True,
                "outputs": [
                    str((output / "figures.json").relative_to(output.parent.parent.parent)),
                    str(
                        (output / "execution_report.json").relative_to(output.parent.parent.parent)
                    ),
                ],
                "metadata": {
                    "slide_target": decision.slide_target,
                    "required_asset_count": decision.required,
                    "usable_mineru_assets": decision.usable,
                    "needs_extraction": decision.needs_extraction,
                    "selection_reasons": list(decision.reasons),
                    "final_figure_count": figure_count,
                },
            }
        )
        (output / "execution_report.json").write_text(
            report.model_dump_json(indent=2), encoding="utf-8"
        )

    def _execute_analysis(
        self,
        *,
        context: PaperProviderContext,
        skill_directory: Path,
        skill_version: str,
        stage_root: Path,
        output: Path,
        runtime_schema_path: Path,
        analysis_schema_path: Path,
        input_hash: str,
    ) -> PaperAnalysis:
        pdf_path = context.workspace / context.source_bundle.pdf_path
        if not pdf_path.is_file():
            pdf_path = context.workspace / "source" / context.source_bundle.pdf_path
        pdf_readable = self._pdf_readable(pdf_path)
        structure_repair_used = False
        last_report: StageExecutionReport | None = None
        last_error: Exception | None = None
        stage_attempt = context.current_attempt(ComposedStage.ANALYSIS)

        for full_attempt in range(1, 3):
            self._clear_analysis_outputs(output)
            prompt_path = self._write_initial_prompt(
                stage_root,
                pdf_readable=pdf_readable,
                rerun=full_attempt > 1,
            )
            inputs = [
                context.workspace / "source" / "paper_source.json",
                context.workspace / "source" / "paper_content.md",
                context.workspace / "resolved_request.json",
            ]
            if pdf_readable:
                inputs.insert(0, context.workspace / "source" / "paper.pdf")
            last_report = self.runtime.run(
                CodexSkillInvocation(
                    stage=ComposedStage.ANALYSIS,
                    skill_name=self.skill_name,
                    skill_directory=skill_directory,
                    skill_version=skill_version,
                    prompt_version=self.prompt_version,
                    input_hash=input_hash,
                    workspace=context.workspace,
                    prompt_path=prompt_path,
                    input_paths=tuple(inputs),
                    output_directory=output,
                    expected_outputs=("paper_analysis.md", "paper_analysis.json"),
                    output_schema_path=runtime_schema_path,
                    attempt=stage_attempt + full_attempt - 1 + int(structure_repair_used),
                    network_enabled=False,
                )
            )
            if last_report.status != StageStatus.SUCCEEDED:
                self._write_final_report(output, last_report, passed=False)
                raise PaperAnalysisStageError("paper-analyze runtime execution failed")

            try:
                analysis = self._read_analysis(output / "paper_analysis.json")
            except _AnalysisStructureError as exc:
                last_error = exc
                if structure_repair_used:
                    break
                structure_repair_used = True
                repair_prompt = self._write_repair_prompt(stage_root)
                last_report = self.runtime.run(
                    CodexSkillInvocation(
                        stage=ComposedStage.ANALYSIS,
                        skill_name=self.skill_name,
                        skill_directory=skill_directory,
                        skill_version=skill_version,
                        prompt_version=f"{self.prompt_version}-structure-repair",
                        input_hash=input_hash,
                        workspace=context.workspace,
                        prompt_path=repair_prompt,
                        input_paths=(
                            output / "paper_analysis.json",
                            context.workspace / "source" / "paper_source.json",
                            analysis_schema_path,
                        ),
                        output_directory=output,
                        expected_outputs=("paper_analysis.md", "paper_analysis.json"),
                        output_schema_path=runtime_schema_path,
                        attempt=stage_attempt + full_attempt,
                        network_enabled=False,
                    )
                )
                if last_report.status != StageStatus.SUCCEEDED:
                    break
                try:
                    analysis = self._read_analysis(output / "paper_analysis.json")
                except (_AnalysisStructureError, _AnalysisEvidenceError) as repair_error:
                    last_error = repair_error
                    if isinstance(repair_error, _AnalysisEvidenceError) and full_attempt == 1:
                        continue
                    break
            except _AnalysisEvidenceError as exc:
                last_error = exc
                if full_attempt == 1:
                    continue
                break

            try:
                self._validate_final(context, analysis, skill_directory, output)
            except PaperArtifactValidationError as exc:
                last_error = exc
                if full_attempt == 1:
                    continue
                break

            if not pdf_readable and self.pdf_fallback_warning not in analysis.warnings:
                analysis.warnings.append(self.pdf_fallback_warning)
                (output / "paper_analysis.json").write_text(
                    analysis.model_dump_json(indent=2), encoding="utf-8"
                )
                markdown_path = output / "paper_analysis.md"
                markdown_path.write_text(
                    f"> Warning: {self.pdf_fallback_warning}\n\n"
                    + markdown_path.read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            self._write_final_report(output, last_report, passed=True)
            return analysis

        if last_report:
            self._write_final_report(
                output,
                last_report,
                passed=False,
                error=str(last_error or "paper analysis validation failed"),
            )
        raise PaperAnalysisStageError(str(last_error or "paper analysis validation failed"))

    def _validate_final(
        self,
        context: PaperProviderContext,
        analysis: PaperAnalysis,
        skill_directory: Path,
        output: Path,
    ) -> None:
        self.validator.validate_analysis(context.source_bundle, analysis)
        self.validator.validate_no_forbidden_paths(
            [output / "paper_analysis.md", output / "paper_analysis.json"],
            forbidden_roots=[Path.home(), skill_directory],
        )

    @staticmethod
    def _read_analysis(path: Path) -> PaperAnalysis:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise _AnalysisStructureError(str(exc)) from exc
        try:
            return PaperAnalysis.model_validate(payload)
        except ValidationError as exc:
            evidence_error = any(
                "source_refs" in error["loc"]
                or error["loc"] == ("claims",)
                or "core claim" in error["msg"]
                for error in exc.errors()
            )
            error_type = _AnalysisEvidenceError if evidence_error else _AnalysisStructureError
            raise error_type(str(exc)) from exc

    def _write_initial_prompt(
        self,
        stage_root: Path,
        *,
        pdf_readable: bool,
        rerun: bool = False,
    ) -> Path:
        common = (self.prompt_directory / "common_constraints.md").read_text(encoding="utf-8")
        contract = (self.prompt_directory / "paper_analyze_contract.md").read_text(encoding="utf-8")
        runtime_note = (
            "\n运行说明：source/paper.pdf 已通过预检，请同时使用 PDF 与结构化文本。\n"
            if pdf_readable
            else "\n运行说明：source/paper.pdf 预检失败。只使用 paper_content.md 和 "
            "paper_source.json，并在 warnings 中记录降级。\n"
        )
        if rerun:
            runtime_note += (
                "这是一次完整重跑：上一次结果为空或证据验证失败。重新分析全部输入，"
                "不要复用上一次分析文件。\n"
            )
        path = stage_root / ("rerun_prompt.md" if rerun else "prompt.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(common + "\n" + contract + runtime_note, encoding="utf-8")
        return path

    def _write_repair_prompt(self, stage_root: Path) -> Path:
        common = (self.prompt_directory / "common_constraints.md").read_text(encoding="utf-8")
        repair = (self.prompt_directory / "paper_analyze_structure_repair.md").read_text(
            encoding="utf-8"
        )
        path = stage_root / "repair_prompt.md"
        path.write_text(common + "\n" + repair, encoding="utf-8")
        return path

    @staticmethod
    def _write_runtime_result_schema(path: Path) -> None:
        path.write_text(
            json.dumps(
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["status", "summary", "warnings"],
                    "properties": {
                        "status": {"type": "string", "enum": ["completed", "blocked"]},
                        "summary": {"type": "string"},
                        "warnings": {"type": "array", "items": {"type": "string"}},
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _write_final_report(
        output: Path,
        report: StageExecutionReport,
        *,
        passed: bool,
        error: str | None = None,
    ) -> None:
        issues = list(report.validation_issues)
        if error:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="analysis_validation_failed",
                    message=error[:2000],
                )
            )
        outputs = [item for item in report.outputs if not item.endswith("execution_report.json")]
        if passed:
            outputs.append(
                str((output / "execution_report.json").relative_to(output.parent.parent.parent))
            )
        final = StageExecutionReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "status": StageStatus.SUCCEEDED if passed else StageStatus.FAILED,
                "outputs": outputs,
                "validation_passed": passed,
                "validation_issues": issues,
            }
        )
        (output / "execution_report.json").write_text(
            final.model_dump_json(indent=2), encoding="utf-8"
        )

    @staticmethod
    def _clear_analysis_outputs(output: Path) -> None:
        for name in ("paper_analysis.md", "paper_analysis.json", "execution_report.json"):
            (output / name).unlink(missing_ok=True)

    @staticmethod
    def _pdf_readable(path: Path) -> bool:
        try:
            document = fitz.open(path)
            readable = document.page_count > 0
            document.close()
            return readable
        except (OSError, RuntimeError, ValueError, fitz.FileDataError):
            return False

    def _resolve_skill_directory(self) -> Path:
        return self._resolve_skill_directory_for(
            self.skill_name,
            "METACLASS_PAPER_ANALYZE_SKILL_DIR",
        )

    @staticmethod
    def _resolve_skill_directory_for(skill_name: str, environment_name: str) -> Path:
        candidates = []
        configured = os.getenv(environment_name)
        if configured:
            candidates.append(Path(configured))
        installed_names = [skill_name]
        # The upstream academic outline Skill is distributed in a directory
        # named ``academic-pptx-skill`` while its public Skill name remains
        # ``academic-pptx``. Accept both layouts so a normal installation works
        # without a machine-specific environment variable.
        if skill_name == "academic-pptx":
            installed_names.append("academic-pptx-skill")
        for installed_name in installed_names:
            candidates.extend(
                [
                    Path.cwd() / "skills" / installed_name,
                    Path.home() / ".codex" / "skills" / installed_name,
                    Path.home() / ".agents" / "skills" / installed_name,
                ]
            )
        for candidate in candidates:
            if (candidate / "SKILL.md").is_file():
                return candidate.resolve()
        raise PaperAnalysisStageError(
            f"{skill_name} Skill is not installed; configure {environment_name} "
            "or install it under ~/.codex/skills"
        )

    @staticmethod
    def _directory_hash(directory: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(item for item in directory.rglob("*") if item.is_file()):
            digest.update(str(path.relative_to(directory)).encode("utf-8"))
            digest.update(path.read_bytes())
        return f"sha256:{digest.hexdigest()}"
