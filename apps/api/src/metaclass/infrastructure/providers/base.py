from pathlib import Path
from typing import Protocol

from metaclass.modules.content.schemas import PageUnderstandingDraft


class LearningProvider(Protocol):
    name: str
    model: str | None

    def understand_page(
        self, title: str, raw_text: str, page_no: int
    ) -> PageUnderstandingDraft: ...


class TTSProvider(Protocol):
    def synthesize(self, text: str, output: Path, voice: str | None = None) -> float: ...
