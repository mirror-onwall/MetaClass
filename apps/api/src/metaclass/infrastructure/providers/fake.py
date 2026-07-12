import math
import struct
import wave
from pathlib import Path

from metaclass.modules.content.schemas import PageUnderstandingDraft, QuizItemDraft


class FakeLearningProvider:
    """Deterministic provider used for local development and tests."""

    name = "fake"
    model = None

    def understand_page(self, title: str, raw_text: str, page_no: int) -> PageUnderstandingDraft:
        clean = " ".join(raw_text.split())
        summary = clean[:180] or f"第 {page_no} 页暂无可提取文本"
        tokenized = clean
        for separator in "，。,:：;；()（）\n":
            tokenized = tokenized.replace(separator, " ")
        words = [word.strip() for word in tokenized.split()]
        points = [word for word in words if 2 <= len(word) <= 12][:3]
        knowledge_points = points or [title or f"第 {page_no} 页"]
        return PageUnderstandingDraft(
            summary=summary,
            knowledge_points=knowledge_points,
            teaching_focus=knowledge_points[:2],
            possible_questions=[f"{knowledge_points[0]}是什么意思？"],
            quiz_items=[
                QuizItemDraft(
                    question=f"下面哪一项最能帮助判断是否理解了“{knowledge_points[0]}”？",
                    options=[
                        f"能说明{knowledge_points[0]}的适用条件或例子",
                        "只记住它在第几页出现",
                        "把所有术语都背下来但不区分含义",
                    ],
                    correct_index=0,
                    explanation=f"理解{knowledge_points[0]}不只是识别词语，还要知道它如何使用。",
                    knowledge_point=knowledge_points[0],
                )
            ],
        )


class FakeTTSProvider:
    """Creates a short WAV tone so the complete video pipeline needs no API key."""

    sample_rate = 16_000

    def synthesize(self, text: str, output: Path) -> float:
        duration = min(max(len(text) * 0.035, 1.2), 8.0)
        frame_count = int(duration * self.sample_rate)
        output.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            for index in range(frame_count):
                envelope = min(1.0, index / 800, (frame_count - index) / 800)
                value = int(
                    1800 * envelope * math.sin(2 * math.pi * 330 * index / self.sample_rate)
                )
                wav.writeframesraw(struct.pack("<h", value))
        return duration
