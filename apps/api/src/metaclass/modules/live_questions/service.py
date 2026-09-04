from __future__ import annotations

import json
import re
from pathlib import Path
from threading import Lock
from uuid import uuid4

from metaclass.infrastructure.providers.llm import LLMMessage, LLMProvider

from .schemas import (
    LiveQuestionAnswer,
    LiveQuestionIndex,
    RetrievedLiveEvidence,
)


class LiveQuestionService:
    """Durable evidence retrieval for user questions, independent of scripted QA."""

    _number = re.compile(r"(?<![\w.])-?\d+(?:[.,]\d+)*(?:\s*(?:%|×|x|k|K|M|B))?")

    def __init__(self, data_dir: Path, llm: LLMProvider | None = None) -> None:
        self.directory = data_dir / "runtime" / "live_question_indexes"
        self.llm = llm
        self._indexes: dict[str, LiveQuestionIndex] = {}
        self._lock = Lock()

    def register(self, index: LiveQuestionIndex) -> LiveQuestionIndex:
        target = self.directory / f"{index.presentation_plan_id}.json"
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary.write_text(index.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(target)
            self._indexes[index.presentation_plan_id] = index
        return index

    def has_index(self, presentation_plan_id: str) -> bool:
        return presentation_plan_id in self._indexes or (
            self.directory / f"{presentation_plan_id}.json"
        ).is_file()

    def get_index(self, presentation_plan_id: str) -> LiveQuestionIndex:
        with self._lock:
            cached = self._indexes.get(presentation_plan_id)
            if cached:
                return cached
            path = self.directory / f"{presentation_plan_id}.json"
            if not path.is_file():
                raise KeyError(f"live question index not found: {presentation_plan_id}")
            index = LiveQuestionIndex.model_validate_json(path.read_text(encoding="utf-8"))
            self._indexes[presentation_plan_id] = index
            return index

    def answer(
        self,
        *,
        presentation_plan_id: str,
        question: str,
        current_slide_id: str | None,
        taught_slide_ids: list[str],
        limit: int = 8,
    ) -> LiveQuestionAnswer:
        query = question.strip()
        if not query:
            raise ValueError("live question must not be empty")
        index = self.get_index(presentation_plan_id)
        retrieved = self.search(
            index,
            query,
            current_slide_id=current_slide_id,
            taught_slide_ids=taught_slide_ids,
            limit=limit,
        )
        if not retrieved:
            return LiveQuestionAnswer(
                answer="现有论文证据中没有找到足以可靠回答这个问题的内容。",
                classroom_note="未命中可引用的论文证据。",
            )
        try:
            answer = self._compose_answer(
                index=index,
                question=query,
                retrieved=retrieved,
                current_slide_id=current_slide_id,
                taught_slide_ids=taught_slide_ids,
            )
        except (RuntimeError, TypeError, ValueError, json.JSONDecodeError):
            answer = self._fallback_answer(retrieved)
        self._validate_answer(answer, retrieved)
        return answer

    def search(
        self,
        index: LiveQuestionIndex,
        query: str,
        *,
        current_slide_id: str | None,
        taught_slide_ids: list[str],
        limit: int = 8,
    ) -> list[RetrievedLiveEvidence]:
        tokens = self._tokens(query)
        taught = set(taught_slide_ids)
        order = {slide_id: position for position, slide_id in enumerate(index.slide_order, start=1)}
        current_order = order.get(current_slide_id or "")
        scored = []
        for document in index.documents:
            haystack = f"{document.title} {document.text}".casefold()
            overlap = sum(token in haystack for token in tokens)
            exact = 3.0 if query.casefold() in haystack else 0.0
            if not overlap and not exact:
                continue
            status = self._status(
                document.slide_ids,
                current_slide_id=current_slide_id,
                taught=taught,
                current_order=current_order,
                order=order,
            )
            classroom_boost = {"current": 2.0, "already_taught": 1.0, "future": 0.0,
                               "paper_reference": 0.3}[status]
            scored.append(
                RetrievedLiveEvidence(
                    document=document,
                    score=exact + float(overlap) + classroom_boost,
                    classroom_status=status,
                )
            )
        return sorted(scored, key=lambda item: (-item.score, item.document.id))[:limit]

    def _compose_answer(
        self,
        *,
        index: LiveQuestionIndex,
        question: str,
        retrieved: list[RetrievedLiveEvidence],
        current_slide_id: str | None,
        taught_slide_ids: list[str],
    ) -> LiveQuestionAnswer:
        future_ids = list(
            dict.fromkeys(
                slide_id
                for item in retrieved
                if item.classroom_status == "future"
                for slide_id in item.document.slide_ids
            )
        )
        if self.llm is None:
            return self._fallback_answer(retrieved)
        payload = {
            "question": question,
            "classroom_state": {
                "current_slide_id": current_slide_id,
                "taught_slide_ids": taught_slide_ids,
                "slide_order": index.slide_order,
            },
            "evidence": [item.model_dump(mode="json") for item in retrieved],
        }
        raw = self.llm.complete_json(
            [
                LLMMessage(
                    role="system",
                    content=(
                        "LIVE_QUESTION_ANSWER_V1\nAnswer the user's free-form classroom question "
                        "using only supplied evidence documents. Distinguish already taught, current, "
                        "future, and paper-reference evidence. Future evidence may answer an explicit "
                        "paper-detail question, but say it has not yet appeared in class. Return JSON "
                        "{answer,used_document_ids}. Do not create numbers, claims, sources, or IDs."
                    ),
                ),
                LLMMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
            ],
            temperature=0.2,
        )
        parsed = json.loads(raw)
        used_ids = [str(item) for item in parsed.get("used_document_ids", [])]
        by_id = {item.document.id: item for item in retrieved}
        if not used_ids or any(item not in by_id for item in used_ids):
            raise ValueError("live answer references unknown evidence documents")
        used = [by_id[item] for item in used_ids]
        future_ids = list(
            dict.fromkeys(
                slide_id
                for item in used
                if item.classroom_status == "future"
                for slide_id in item.document.slide_ids
            )
        )
        answer = str(parsed.get("answer") or "").strip()
        note = "其中部分证据来自课堂尚未讲到的后续页面。" if future_ids else None
        if note and note not in answer:
            answer = f"{note}{answer}"
        return LiveQuestionAnswer(
            answer=answer,
            source_refs=self._refs(used),
            used_document_ids=used_ids,
            future_slide_ids=future_ids,
            classroom_note=note,
        )

    def _fallback_answer(
        self,
        retrieved: list[RetrievedLiveEvidence],
    ) -> LiveQuestionAnswer:
        selected = []
        seen_text: set[str] = set()
        for item in retrieved:
            normalized = " ".join(item.document.text.casefold().split())
            if normalized in seen_text:
                continue
            seen_text.add(normalized)
            selected.append(item)
            if len(selected) >= 5:
                break
        future_ids = list(
            dict.fromkeys(
                slide_id
                for item in selected
                if item.classroom_status == "future"
                for slide_id in item.document.slide_ids
            )
        )
        evidence_text = " ".join(item.document.text for item in selected)
        note = "其中部分证据来自课堂尚未讲到的后续页面。" if future_ids else None
        return LiveQuestionAnswer(
            answer=f"{note or ''}{evidence_text}",
            source_refs=self._refs(selected),
            used_document_ids=[item.document.id for item in selected],
            future_slide_ids=future_ids,
            classroom_note=note,
        )

    def _validate_answer(
        self,
        answer: LiveQuestionAnswer,
        retrieved: list[RetrievedLiveEvidence],
    ) -> None:
        by_id = {item.document.id: item.document for item in retrieved}
        used = [by_id[item] for item in answer.used_document_ids if item in by_id]
        authorized = {
            self._normalize_number(number)
            for document in used
            for number in self._number.findall(document.text)
        }
        observed = {
            self._normalize_number(number) for number in self._number.findall(answer.answer)
        }
        if observed - authorized:
            raise ValueError("live answer contains numbers absent from retrieved evidence")

    @staticmethod
    def _status(slide_ids, *, current_slide_id, taught, current_order, order):
        if current_slide_id and current_slide_id in slide_ids:
            return "current"
        if taught.intersection(slide_ids):
            return "already_taught"
        if slide_ids and current_order is not None and any(
            order.get(item, 0) > current_order for item in slide_ids
        ):
            return "future"
        return "paper_reference"

    @staticmethod
    def _refs(items: list[RetrievedLiveEvidence]):
        refs = [ref for item in items for ref in item.document.source_refs]
        return list(
            {
                (ref.material_id, ref.page_id, ref.page_no, ref.text_span, ref.image_path): ref
                for ref in refs
            }.values()
        )

    @staticmethod
    def _tokens(value: str) -> set[str]:
        latin = set(re.findall(r"[a-z0-9_]+", value.casefold()))
        chinese = re.findall(r"[\u4e00-\u9fff]+", value)
        return latin | {
            token
            for chunk in chinese
            for token in [*chunk, *(chunk[index : index + 2] for index in range(len(chunk) - 1))]
        }

    @staticmethod
    def _normalize_number(value: str) -> str:
        return re.sub(r"[\s,]", "", value).casefold().replace("×", "x")
