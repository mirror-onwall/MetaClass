"""Production orchestration for the native paper-deck PDF classroom route."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import TypeAdapter

from metaclass.core.schemas import utc_now
from metaclass.infrastructure.providers.llm import LLMProvider
from metaclass.modules.classroom.agent_schemas import StudentAgentType
from metaclass.modules.content.schemas import (
    CourseKnowledgeTree,
    KnowledgeUnit,
    LearningContent,
    LearningSection,
    PageRef,
)
from metaclass.modules.content.service import ContentService
from metaclass.modules.interaction_planning import (
    InteractionPolicy,
    PaperDeckInteractionAdapter,
    QuestionGenerator,
    ScriptedInteractionBank,
    TeachingNodeSelector,
    UnifiedInteractionPlanningPipeline,
)
from metaclass.modules.live_questions import LiveQuestionService, PaperDeckLiveQuestionAdapter
from metaclass.modules.materials.schemas import SourceRef
from metaclass.modules.materials.service import MaterialService
from metaclass.modules.paper_workflow.final_paper_deck_page_analyzer import (
    FinalPaperDeckPageAnalyzer,
    FinalSlideObservation,
    ImageJSONProvider,
)
from metaclass.modules.paper_workflow.grounded_slide_packet import (
    GroundedSlidePacket,
    GroundedSlidePacketBuilder,
)
from metaclass.modules.paper_workflow.native_job import (
    NativePaperDeckCheckpointStore,
    NativePaperDeckStage,
    NativeStageCheckpoint,
)
from metaclass.modules.paper_workflow.paper_classroom import (
    GroundedNarrationPacketBuilder,
    LLMPaperClassroomComposer,
    SlideNarration,
)
from metaclass.modules.paper_workflow.paper_deck_artifact_adapter import PaperDeckArtifactAdapter
from metaclass.modules.paper_workflow.paper_deck_pdf_registration import (
    PaperDeckPDFRegistrationService,
)
from metaclass.modules.paper_workflow.paper_deck_playback_plan import (
    PaperDeckPlaybackPlanBuilder,
)
from metaclass.modules.paper_workflow.paper_knowledge import (
    PaperKnowledgeProjection,
    PaperKnowledgeTreeBuilder,
)
from metaclass.modules.paper_workflow.providers.base import PaperProviderContext
from metaclass.modules.paper_workflow.providers.composed_skills import ComposedSkillsProvider
from metaclass.modules.paper_workflow.providers.native_paper_deck import NativePaperDeckProvider
from metaclass.modules.paper_workflow.schemas import (
    ArtifactFile,
    PaperArtifactBundle,
    PaperDeckCourseResult,
    StageStatus,
)
from metaclass.modules.presentation.repository import PresentationRepository
from metaclass.modules.presentation.service import PresentationService
from metaclass.modules.question_bank.repository import QuestionBankRepository
from metaclass.modules.question_bank.schemas import ClassroomQA


class NativePaperDeckWorkflowRunner:
    """Run the native raster deck and register a fully grounded classroom."""

    name = "native_paper_deck"

    def __init__(
        self,
        *,
        data_dir: Path,
        materials: MaterialService,
        contents: ContentService,
        presentations: PresentationService,
        presentation_repository: PresentationRepository,
        question_banks: QuestionBankRepository,
        live_questions: LiveQuestionService,
        llm: LLMProvider,
        vision: ImageJSONProvider,
        analysis_provider: ComposedSkillsProvider | None = None,
        deck_provider: NativePaperDeckProvider | None = None,
    ) -> None:
        self.data_dir = data_dir.resolve()
        self.materials = materials
        self.contents = contents
        self.presentations = presentations
        self.presentation_repository = presentation_repository
        self.question_banks = question_banks
        self.live_questions = live_questions
        self.llm = llm
        self.vision = vision
        self.analysis_provider = analysis_provider or ComposedSkillsProvider()
        self.deck_provider = deck_provider or NativePaperDeckProvider()

    def run(self, context: PaperProviderContext) -> PaperArtifactBundle:
        checkpoints = NativePaperDeckCheckpointStore(context.workspace)
        context.raise_if_paused()
        self._record(
            checkpoints,
            context,
            NativePaperDeckStage.PREPARING_SOURCE,
            {"source": f"source/{context.source_bundle.paper_source_path}"},
            input_paths=[context.workspace / "resolved_request.json"],
        )
        context.report_progress(0.10, NativePaperDeckStage.ANALYZING_PAPER.value)
        context.raise_if_paused()
        analysis = self.analysis_provider.run_analysis(context)
        analysis_path = context.workspace / "stages/01_analysis/output/paper_analysis.json"
        self._record(
            checkpoints,
            context,
            NativePaperDeckStage.ANALYZING_PAPER,
            {"analysis": str(analysis_path.relative_to(context.workspace))},
            input_paths=[
                context.workspace / "source/paper_source.json",
                context.workspace / "source/paper_content.md",
            ],
        )

        context.report_progress(0.25, NativePaperDeckStage.GENERATING_NATIVE_PAPER_DECK.value)
        context.raise_if_paused()
        artifact = self.deck_provider.run(context)
        self._record(
            checkpoints,
            context,
            NativePaperDeckStage.GENERATING_NATIVE_PAPER_DECK,
            {"artifact": "provider_output/presentation.pdf"},
            input_paths=[analysis_path, context.workspace / "resolved_request.json"],
        )
        manifest = PaperDeckArtifactAdapter().adapt(artifact, workspace=context.workspace)
        self._write_json(context.workspace / "provider_output/native_deck.json", manifest)
        self._record(
            checkpoints,
            context,
            NativePaperDeckStage.VALIDATING_PDF,
            {"manifest": "provider_output/native_deck.json"},
            input_paths=[context.workspace / artifact.presentation_pdf_path],
        )

        plan_id = f"presentation_plan_native_{context.job_id.removeprefix('paper_job_')}"
        observations: list[FinalSlideObservation] = []
        packets: list[GroundedSlidePacket] = []
        for repair_attempt in range(3):
            manifest = PaperDeckArtifactAdapter().adapt(artifact, workspace=context.workspace)
            reconciliation_path = context.workspace / "provider_output/slide_reconciliation.json"
            reconciliation_inputs = [
                context.workspace / artifact.presentation_pdf_path,
                context.workspace / "provider_output/native_deck.json",
                context.workspace / "source/paper_content.md",
            ]
            reconciliation_hash = self._input_hash(
                checkpoints,
                context,
                NativePaperDeckStage.RECONCILING_SLIDES,
                reconciliation_inputs,
            )
            context.report_progress(0.45, NativePaperDeckStage.RECONCILING_SLIDES.value)
            context.raise_if_paused()
            if checkpoints.can_resume(
                NativePaperDeckStage.RECONCILING_SLIDES,
                input_hash=reconciliation_hash,
            ):
                observations = TypeAdapter(list[FinalSlideObservation]).validate_json(
                    reconciliation_path.read_text(encoding="utf-8")
                )
            else:
                observations = FinalPaperDeckPageAnalyzer(vision=self.vision).analyze(
                    manifest,
                    workspace=context.workspace,
                    paper_content_path=context.workspace / "source/paper_content.md",
                    check_cancelled=context.raise_if_paused,
                )
                self._write_json(reconciliation_path, observations)
                self._record(
                    checkpoints,
                    context,
                    NativePaperDeckStage.RECONCILING_SLIDES,
                    {"observations": "provider_output/slide_reconciliation.json"},
                    input_paths=reconciliation_inputs,
                )

            grounding_path = context.workspace / "provider_output/evidence_grounding.json"
            grounding_inputs = [reconciliation_path, analysis_path, context.workspace / "source/paper_content.md"]
            grounding_hash = self._input_hash(
                checkpoints,
                context,
                NativePaperDeckStage.GROUNDING_EVIDENCE,
                grounding_inputs,
            )
            context.report_progress(0.58, NativePaperDeckStage.GROUNDING_EVIDENCE.value)
            context.raise_if_paused()
            if checkpoints.can_resume(
                NativePaperDeckStage.GROUNDING_EVIDENCE,
                input_hash=grounding_hash,
            ):
                packets = TypeAdapter(list[GroundedSlidePacket]).validate_json(
                    grounding_path.read_text(encoding="utf-8")
                )
            else:
                packets = GroundedSlidePacketBuilder(self.llm).build(
                    source_bundle=context.source_bundle,
                    paper_content=(context.workspace / "source/paper_content.md").read_text(
                        encoding="utf-8"
                    ),
                    analysis=analysis,
                    manifest=manifest,
                    observations=observations,
                    knowledge_units=[],
                )
                self._write_json(grounding_path, packets)
                self._record(
                    checkpoints,
                    context,
                    NativePaperDeckStage.GROUNDING_EVIDENCE,
                    {"packets": "provider_output/evidence_grounding.json"},
                    input_paths=grounding_inputs,
                )
            directives = [
                item.repair_directive.model_dump(mode="json")
                for item in packets
                if item.repair_directive
            ]
            if not directives:
                break
            if repair_attempt == 2:
                raise ValueError(
                    "native paper-deck targeted repair did not remove unverified content: "
                    f"{[item['slide_id'] for item in directives]}"
                )
            context.raise_if_paused()
            artifact = self.deck_provider.repair_slides(context, directives=directives)
            PaperDeckArtifactAdapter().adapt(artifact, workspace=context.workspace)
            checkpoints.invalidate_slide_regeneration(
                [str(item["slide_id"]) for item in directives]
            )
            self._write_json(
                context.workspace / "provider_output/native_deck.json",
                PaperDeckArtifactAdapter().adapt(artifact, workspace=context.workspace),
            )

        # Only publish a PDF Material after every visible number has passed grounding.
        manifest = PaperDeckArtifactAdapter().adapt(artifact, workspace=context.workspace)
        self._write_json(context.workspace / "provider_output/native_deck.json", manifest)
        self._record(
            checkpoints,
            context,
            NativePaperDeckStage.VALIDATING_PDF,
            {"manifest": "provider_output/native_deck.json"},
            input_paths=[context.workspace / artifact.presentation_pdf_path],
        )
        generation_hash = self._file_hash(context.workspace / artifact.presentation_pdf_path)
        registration = PaperDeckPDFRegistrationService(
            materials=self.materials,
            presentations=self.presentation_repository,
        ).register(
            artifact=artifact,
            manifest=manifest,
            workspace=context.workspace,
            original_paper_material_id=context.request.material_id,
            presentation_plan_id=plan_id,
            paper_pdf_hash=context.source_bundle.file_hash.removeprefix("sha256:"),
            paper_deck_skill_version=self.deck_provider._directory_hash(
                self.deck_provider.skill_directory or self.deck_provider._resolve_skill_directory()
            ),
            resolved_request_hash=self._file_hash(context.workspace / "resolved_request.json"),
            generation_output_hash=generation_hash,
            persist_resource=False,
        )

        context.report_progress(0.68, NativePaperDeckStage.BUILDING_KNOWLEDGE_TREE.value)
        context.raise_if_paused()
        knowledge_path = context.workspace / "provider_output/knowledge_tree.json"
        knowledge_inputs = [context.workspace / "provider_output/evidence_grounding.json", analysis_path]
        knowledge_hash = self._input_hash(
            checkpoints, context, NativePaperDeckStage.BUILDING_KNOWLEDGE_TREE, knowledge_inputs
        )
        if checkpoints.can_resume(
            NativePaperDeckStage.BUILDING_KNOWLEDGE_TREE, input_hash=knowledge_hash
        ):
            projection = self._load_projection(knowledge_path)
        else:
            projection = PaperKnowledgeTreeBuilder().build(
                job_id=context.job_id,
                source_paper_material_id=context.request.material_id,
                deck_material_id=registration.material.id,
                analysis=analysis,
                grounded_packets=packets,
            )
            self._write_json(knowledge_path, self._projection_payload(projection))
            self._record(
                checkpoints,
                context,
                NativePaperDeckStage.BUILDING_KNOWLEDGE_TREE,
                {"knowledge_tree": "provider_output/knowledge_tree.json"},
                input_paths=knowledge_inputs,
            )

        context.report_progress(0.76, NativePaperDeckStage.GENERATING_NARRATION.value)
        context.raise_if_paused()
        narration_path = context.workspace / "provider_output/narration.json"
        narration_inputs = [
            context.workspace / "provider_output/evidence_grounding.json",
            knowledge_path,
            context.workspace / artifact.analysis_path,
        ]
        narration_hash = self._input_hash(
            checkpoints, context, NativePaperDeckStage.GENERATING_NARRATION, narration_inputs
        )
        if checkpoints.can_resume(
            NativePaperDeckStage.GENERATING_NARRATION, input_hash=narration_hash
        ):
            narrations = TypeAdapter(list[SlideNarration]).validate_json(
                narration_path.read_text(encoding="utf-8")
            )
        else:
            narration_packets = GroundedNarrationPacketBuilder().build(
                packets=packets,
                knowledge_units=projection.knowledge_units,
                analysis=analysis,
                source_bundle=context.source_bundle,
                paper_deck_analysis=(context.workspace / artifact.analysis_path).read_text(
                    encoding="utf-8"
                ),
            )
            narrations = LLMPaperClassroomComposer(self.llm).compose_grounded(
                narration_packets,
                audience=context.request.audience or "具备基础专业背景的高校学生和研究生",
                language=context.request.language,
                check_cancelled=context.raise_if_paused,
            )
            self._write_json(narration_path, narrations)
            self._record(
                checkpoints,
                context,
                NativePaperDeckStage.GENERATING_NARRATION,
                {"narration": "provider_output/narration.json"},
                input_paths=narration_inputs,
            )

        content = self._content(context, analysis, packets, narrations, projection, registration.material.id)
        content = self.contents.save_paper_deck_content(content)
        plan = PaperDeckPlaybackPlanBuilder().build(
            content_id=content.id,
            title=content.title,
            generated_pdf_material_id=registration.material.id,
            original_paper_material_id=context.request.material_id,
            paper_artifact_bundle_id=f"paper_bundle_native_{context.job_id}",
            presentation_resource_id=registration.presentation_resource.id,
            packets=packets,
            narrations=narrations,
            knowledge_units=projection.knowledge_units,
            interaction_intensity=context.request.interaction_intensity,
            plan_id=plan_id,
        )
        plan = self.presentations.save_paper_deck_plan(plan)

        context.report_progress(0.88, NativePaperDeckStage.PLANNING_INTERACTIONS.value)
        context.raise_if_paused()
        interaction_context = PaperDeckInteractionAdapter().adapt(
            plan=plan,
            packets=packets,
            knowledge_units=projection.knowledge_units,
            original_paper_material_id=context.request.material_id,
            duration_minutes=context.request.duration_minutes or 15,
        )
        policy = self._policy(context.request.interaction_intensity)
        interaction_path = context.workspace / "provider_output/interaction.json"
        interaction_inputs = [narration_path, knowledge_path, context.workspace / "provider_output/evidence_grounding.json"]
        interaction_hash = self._input_hash(
            checkpoints, context, NativePaperDeckStage.PLANNING_INTERACTIONS, interaction_inputs
        )
        if checkpoints.can_resume(
            NativePaperDeckStage.PLANNING_INTERACTIONS, input_hash=interaction_hash
        ):
            blueprints = TeachingNodeSelector().select(interaction_context, policy)
            bank = ScriptedInteractionBank.model_validate_json(
                interaction_path.read_text(encoding="utf-8")
            )
        else:
            blueprints, bank = UnifiedInteractionPlanningPipeline(
                generator=QuestionGenerator(self.llm)
            ).plan(
                interaction_context,
                policy,
            )
            self._write_json(interaction_path, bank)
            self._record(
                checkpoints,
                context,
                NativePaperDeckStage.PLANNING_INTERACTIONS,
                {"interaction": "provider_output/interaction.json"},
                input_paths=interaction_inputs,
            )
        # Merge the workflow's original bank without deleting later user-created
        # QA generations that belong to the same presentation plan.
        self.question_banks.append_for_plan(self._qa_items(context, plan, blueprints, bank))

        context.report_progress(0.96, NativePaperDeckStage.REGISTERING_CLASSROOM.value)
        context.raise_if_paused()
        live_index = PaperDeckLiveQuestionAdapter().adapt(
            plan=plan,
            source_bundle=context.source_bundle,
            analysis=analysis,
            knowledge_units=projection.knowledge_units,
            packets=packets,
        )
        self.live_questions.register(live_index)
        course = PaperDeckCourseResult(
            paper_job_id=context.job_id,
            derived_material_id=registration.material.id,
            source_paper_material_id=context.request.material_id,
            artifact_bundle_id=f"paper_bundle_native_{context.job_id}",
            content_id=content.id,
            presentation_plan_id=plan.id,
        )
        self._write_json(context.workspace / "provider_output/classroom.json", course)
        self._record(
            checkpoints,
            context,
            NativePaperDeckStage.REGISTERING_CLASSROOM,
            {"classroom": "provider_output/classroom.json"},
            input_paths=[interaction_path, narration_path, knowledge_path],
        )
        return self._bundle(context, artifact, course)

    def _content(self, context, analysis, packets, narrations, projection, deck_material_id):
        narration_by_id = {item.slide_id: item for item in narrations}
        sections = []
        for packet in packets:
            refs = [
                SourceRef(
                    material_id=context.request.material_id,
                    page_id=ref.block_id or ref.asset_id or f"paper_page_{ref.page_no:03d}",
                    page_no=ref.page_no,
                    text_span=ref.quote,
                )
                for ref in packet.source_refs
            ]
            sections.append(
                LearningSection(
                    id=packet.slide_id,
                    title=packet.final_page.visible_title or packet.authoring_intent.title_hint,
                    role=packet.authoring_intent.role,
                    content_goal=packet.authoring_intent.message,
                    summary=packet.authoring_intent.message,
                    key_points=packet.authoring_intent.planned_text,
                    teaching_narrative=narration_by_id[packet.slide_id].speaker_script,
                    knowledge_points=packet.knowledge_unit_ids,
                    source_refs=refs or [
                        SourceRef(
                            material_id=deck_material_id,
                            page_id=f"page_{packet.order:03d}",
                            page_no=packet.order,
                        )
                    ],
                    page_refs=[PageRef(material_id=deck_material_id, page_no=packet.order)],
                    tree_node_ids=projection.section_node_ids.get(packet.slide_id, []),
                    page_nos=[packet.order],
                    teaching_script=narration_by_id[packet.slide_id].speaker_script,
                    visual_summary=packet.final_page.visual_summary,
                    transition_to_next=narration_by_id[packet.slide_id].transition,
                )
            )
        identity = context.job_id.removeprefix("paper_job_")
        return LearningContent(
            id=f"content_paper_native_{identity}",
            material_id=deck_material_id,
            material_ids=[context.request.material_id, deck_material_id],
            organization_mode="paper_deck",
            title=str(analysis.paper.get("title") or analysis.main_claim),
            subtitle=analysis.central_question,
            audience={"description": context.request.audience or "高校学生和研究生"},
            teaching_intent={"mode": "paper_deck", "main_claim": analysis.main_claim},
            material_overview={"source_paper_material_id": context.request.material_id},
            knowledge_units=projection.knowledge_units,
            knowledge_tree=projection.knowledge_tree,
            objectives=[analysis.central_question, analysis.main_claim],
            sections=sections,
            quality={"evidence_grounded": True, "native_raster_deck": True},
        )

    @staticmethod
    def _policy(intensity):
        limits = {"none": (0, 0), "light": (1, 2), "standard": (1, 4), "rich": (2, 6)}
        minimum, maximum = limits[intensity]
        return InteractionPolicy(intensity=intensity, minimum_nodes=minimum, maximum_nodes=maximum)

    @staticmethod
    def _qa_items(context, plan, blueprints, bank):
        order = {item.id: item.order for item in plan.slides}
        blueprint_by_id = {item.id: item for item in blueprints}
        return [
            ClassroomQA(
                id=item.id,
                presentation_plan_id=plan.id,
                content_id=plan.content_id,
                slide_id=item.slide_id,
                slide_order=order[item.slide_id],
                agent_type=StudentAgentType.RESEARCHER,
                student_profile_id="paper_researcher",
                knowledge_point=(blueprint_by_id[item.blueprint_id].teaching_goal),
                canonical_question=item.question,
                student_question=item.question,
                canonical_answer=item.answer,
                teacher_answer=item.answer,
                moment="after_explanation",
                placement_reason="Unified paper-deck interaction blueprint",
                source_refs=item.source_refs,
            )
            for item in bank.items
        ]

    def _bundle(self, context, artifact, course):
        root = context.workspace / "provider_output"
        specs = [
            ("presentation", "presentation.pdf", "application/pdf"),
            ("analysis", "analysis.md", "text/markdown"),
            ("outline", "outline.md", "text/markdown"),
            ("slide_evidence", "evidence_grounding.json", "application/json"),
            ("asset_manifest", "native_deck.json", "application/json"),
            ("speaker_notes", "narration.json", "application/json"),
            ("generation_report", "generation-log.md", "text/markdown"),
            ("qa_report", "interaction.json", "application/json"),
        ]
        files = [
            ArtifactFile(
                role=role,
                path=name,
                sha256=self._file_hash(root / name),
                media_type=media_type,
            )
            for role, name, media_type in specs
        ]
        return PaperArtifactBundle(
            id=course.artifact_bundle_id,
            job_id=context.job_id,
            source_material_id=context.request.material_id,
            provider=self.name,
            root_path=str(root.relative_to(self.data_dir)),
            files=files,
            presentation_artifact=artifact,
            validation_status="passed",
            derived_material_id=course.derived_material_id,
        )

    @staticmethod
    def _write_json(path: Path, value) -> None:
        if hasattr(value, "model_dump"):
            payload = value.model_dump(mode="json")
        elif isinstance(value, dict):
            payload = value
        else:
            payload = [item.model_dump(mode="json") for item in value]
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _record(self, store, context, stage, outputs, *, input_paths):
        input_hash = self._input_hash(store, context, stage, input_paths)
        checkpoint = NativeStageCheckpoint(
            job_id=context.job_id,
            stage=stage,
            status=StageStatus.SUCCEEDED,
            input_hash=input_hash,
            output_hash=store.hash_outputs(outputs),
            attempt=1,
            outputs=outputs,
            validation={"passed": True},
            updated_at=utc_now(),
        )
        store.save(checkpoint)

    @staticmethod
    def _input_hash(store, context, stage, input_paths):
        metadata = {
            "request": context.request.model_dump(mode="json"),
            "stage": stage.value,
        }
        if stage == NativePaperDeckStage.GROUNDING_EVIDENCE:
            # Bump when deterministic numeric-grounding semantics change so failed
            # jobs do not resume a previously accepted but now-invalid packet cache.
            metadata["grounding_rules_version"] = "v2-source-figure-numbers"
        if stage == NativePaperDeckStage.PLANNING_INTERACTIONS:
            metadata["interaction_rules_version"] = "v2-llm-grounded-questions"
        return store.hash_inputs(
            metadata=metadata,
            paths=input_paths,
        )

    @staticmethod
    def _projection_payload(projection: PaperKnowledgeProjection) -> dict[str, object]:
        return {
            "knowledge_units": [item.model_dump(mode="json") for item in projection.knowledge_units],
            "knowledge_tree": projection.knowledge_tree.model_dump(mode="json"),
            "section_node_ids": projection.section_node_ids,
            "evidence_index_unit_ids": projection.evidence_index_unit_ids,
        }

    @staticmethod
    def _load_projection(path: Path) -> PaperKnowledgeProjection:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return PaperKnowledgeProjection(
            knowledge_units=TypeAdapter(list[KnowledgeUnit]).validate_python(
                payload["knowledge_units"]
            ),
            knowledge_tree=CourseKnowledgeTree.model_validate(payload["knowledge_tree"]),
            section_node_ids={
                str(key): [str(item) for item in value]
                for key, value in payload["section_node_ids"].items()
            },
            evidence_index_unit_ids=[
                str(item) for item in (payload.get("evidence_index_unit_ids") or [])
            ],
        )

    @staticmethod
    def _file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
