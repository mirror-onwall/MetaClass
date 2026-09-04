from pathlib import Path

from metaclass.modules.paper_workflow.native_job import (
    NATIVE_CHECKPOINT_FILENAMES,
    NATIVE_PAPER_DECK_STAGE_ORDER,
    NativePaperDeckCheckpointStore,
    NativePaperDeckStage,
    NativeStageCheckpoint,
)
from metaclass.modules.paper_workflow.schemas import StageStatus


def _complete(
    store: NativePaperDeckCheckpointStore,
    stage: NativePaperDeckStage,
    *,
    output_name: str,
) -> NativeStageCheckpoint:
    output = store.workspace / output_name
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(stage.value, encoding="utf-8")
    outputs = {"artifact": output_name}
    checkpoint = NativeStageCheckpoint(
        job_id="paper_job_native",
        stage=stage,
        status=StageStatus.SUCCEEDED,
        input_hash=store.hash_payload({"stage": stage.value}),
        output_hash=store.hash_outputs(outputs),
        attempt=1,
        outputs=outputs,
        validation={"passed": True},
    )
    store.save(checkpoint)
    return checkpoint


def test_native_job_writes_all_named_atomic_checkpoints(tmp_path: Path) -> None:
    store = NativePaperDeckCheckpointStore(tmp_path)
    for index, stage in enumerate(NATIVE_PAPER_DECK_STAGE_ORDER, start=1):
        checkpoint = _complete(store, stage, output_name=f"outputs/{index}.json")
        assert store.path_for(stage).name == NATIVE_CHECKPOINT_FILENAMES[stage]
        assert store.load(stage) == checkpoint
        assert store.can_resume(stage, input_hash=checkpoint.input_hash)

    assert sorted(path.name for path in (tmp_path / "checkpoints").glob("*.json")) == sorted(
        NATIVE_CHECKPOINT_FILENAMES.values()
    )
    assert not list((tmp_path / "checkpoints").glob(".*.tmp"))


def test_resume_rejects_checkpoint_when_output_bytes_change(tmp_path: Path) -> None:
    store = NativePaperDeckCheckpointStore(tmp_path)
    checkpoint = _complete(
        store,
        NativePaperDeckStage.RECONCILING_SLIDES,
        output_name="outputs/observations.json",
    )
    assert store.can_resume(checkpoint.stage, input_hash=checkpoint.input_hash)

    (tmp_path / "outputs/observations.json").write_text("changed", encoding="utf-8")

    assert not store.can_resume(checkpoint.stage, input_hash=checkpoint.input_hash)


def test_narration_edit_does_not_invalidate_native_deck(tmp_path: Path) -> None:
    store = NativePaperDeckCheckpointStore(tmp_path)
    checkpoints = {
        stage: _complete(store, stage, output_name=f"outputs/{stage.value}.json")
        for stage in NATIVE_PAPER_DECK_STAGE_ORDER
    }

    invalidated = store.invalidate_narration(["paper_deck_slide_007"])

    assert invalidated == list(
        NATIVE_PAPER_DECK_STAGE_ORDER[
            NATIVE_PAPER_DECK_STAGE_ORDER.index(NativePaperDeckStage.GENERATING_NARRATION) :
        ]
    )
    assert store.can_resume(
        NativePaperDeckStage.GENERATING_NATIVE_PAPER_DECK,
        input_hash=checkpoints[NativePaperDeckStage.GENERATING_NATIVE_PAPER_DECK].input_hash,
    )
    narration = store.load(NativePaperDeckStage.GENERATING_NARRATION)
    assert narration is not None
    assert narration.status == StageStatus.PENDING
    assert narration.affected_slide_ids == ["paper_deck_slide_007"]


def test_single_slide_repair_invalidates_pdf_and_only_scopes_semantic_stages(
    tmp_path: Path,
) -> None:
    store = NativePaperDeckCheckpointStore(tmp_path)
    checkpoints = {
        stage: _complete(store, stage, output_name=f"outputs/{stage.value}.json")
        for stage in NATIVE_PAPER_DECK_STAGE_ORDER
    }

    store.invalidate_slide_regeneration(["paper_deck_slide_004"])

    assert store.can_resume(
        NativePaperDeckStage.GENERATING_NATIVE_PAPER_DECK,
        input_hash=checkpoints[NativePaperDeckStage.GENERATING_NATIVE_PAPER_DECK].input_hash,
    )
    pdf = store.load(NativePaperDeckStage.VALIDATING_PDF)
    reconciliation = store.load(NativePaperDeckStage.RECONCILING_SLIDES)
    grounding = store.load(NativePaperDeckStage.GROUNDING_EVIDENCE)
    knowledge = store.load(NativePaperDeckStage.BUILDING_KNOWLEDGE_TREE)
    narration = store.load(NativePaperDeckStage.GENERATING_NARRATION)
    assert pdf and pdf.affected_slide_ids == []
    assert reconciliation and reconciliation.affected_slide_ids == ["paper_deck_slide_004"]
    assert grounding and grounding.affected_slide_ids == ["paper_deck_slide_004"]
    assert knowledge and knowledge.affected_slide_ids == []
    assert narration and narration.affected_slide_ids == ["paper_deck_slide_004"]
