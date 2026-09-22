import pytest
from pydantic import ValidationError

from metaclass.modules.content.schemas import (
    SourceDeckLearningContentDraft,
    SourceDeckOutlineDraft,
)


@pytest.fixture(params=[SourceDeckOutlineDraft, SourceDeckLearningContentDraft])
def draft_case(request):
    payload = {
        "title": "聚类分析",
        "sections": [
            {
                "title": "课程概述",
                "page_refs": [{"material_id": "mat_001", "page_no": 1}],
            }
        ],
    }
    if request.param is SourceDeckLearningContentDraft:
        payload["page_flow"] = [{"page_no": 1, "chapter_title": "课程概述"}]
    return request.param, payload


def test_agenda_accepts_mixed_titles_and_llm_objects(draft_case):
    model, payload = draft_case
    payload["detected_agenda"] = [
        "课程概述",
        {"section_title": "层次聚类", "page_range": [2, 9]},
    ]
    draft = model.model_validate(payload)
    assert draft.detected_agenda == ["课程概述", "层次聚类"]
    assert draft.model_dump(mode="json")["detected_agenda"] == ["课程概述", "层次聚类"]
    assert draft.sections[0].page_refs[0].page_no == 1


@pytest.mark.parametrize("agenda", [[], ["课程概述", "层次聚类"]])
def test_agenda_preserves_string_contract(draft_case, agenda):
    model, payload = draft_case
    assert model.model_validate(payload).detected_agenda == []
    payload["detected_agenda"] = agenda
    assert model.model_validate(payload).detected_agenda == agenda


@pytest.mark.parametrize("item", [{"page_range": [1, 2]}, {"section_title": None}, 123])
def test_agenda_rejects_invalid_titles(draft_case, item):
    model, payload = draft_case
    payload["detected_agenda"] = [item]
    with pytest.raises(ValidationError) as exc_info:
        model.model_validate(payload)
    assert exc_info.value.errors()[0]["loc"] == ("detected_agenda", 0)
