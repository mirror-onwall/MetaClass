import { useState } from "react";
import type { PresentationSlideDisplay, TeachingAction } from "../../shared/types";

export function ActionView({
  action,
  presentationSlideImages = {},
  currentSlide,
  slideProgress,
  onAnswer,
  answerDisabled = false,
  quizResult = null,
}: {
  action: TeachingAction | null;
  presentationSlideImages?: Record<number, PresentationSlideDisplay>;
  currentSlide?: { src: string; pageNo: number; generated: boolean } | null;
  slideProgress?: {
    current: number;
    total: number;
    knowledgePoint: string;
  } | null;
  onAnswer: (index: number) => void;
  answerDisabled?: boolean;
  quizResult?: { actionId: string; selectedIndex: number; correct: boolean } | null;
}) {
  const [pendingSelection, setPendingSelection] = useState<{
    actionId: string;
    index: number;
  } | null>(null);

  function renderSlide(slide: { src: string; pageNo: number; generated: boolean }) {
    return (
      <div className="slide-action">
        <img src={slide.src} alt={`PPT 第 ${slide.pageNo} 页`} />
        {slideProgress ? (
          <div
            className="lesson-progress"
            role="progressbar"
            aria-label={`课程进度：第 ${slideProgress.current} 页，共 ${slideProgress.total} 页`}
            aria-valuemin={1}
            aria-valuemax={slideProgress.total}
            aria-valuenow={slideProgress.current}
          >
            <div className="lesson-progress-copy">
              <b>{slideProgress.current}/{slideProgress.total}</b>
              <span><small>当前知识点</small>{slideProgress.knowledgePoint}</span>
            </div>
            <i aria-hidden="true">
              <b style={{ width: `${Math.min(100, Math.max(0, slideProgress.current / slideProgress.total * 100))}%` }} />
            </i>
          </div>
        ) : (
          <span>{slide.generated ? "正在展示生成 PPT" : "正在展示原始页面"} · 第 {slide.pageNo} 页</span>
        )}
      </div>
    );
  }

  if (!action) {
    if (currentSlide) return renderSlide(currentSlide);
    return (
      <div className="action-placeholder">
        <span>READY</span>
        <p>Controller 会严格按计划推进课堂。</p>
      </div>
    );
  }
  if (action.type === "SHOW_PAGE" || action.type === "SHOW_SLIDE") {
    const requestedPage = action.type === "SHOW_SLIDE"
      ? action.payload.slide_no
      : action.payload.slide_no ?? action.payload.source_ref.page_no;
    const slideEntries = Object.entries(presentationSlideImages)
      .map(([pageNo, image]) => ({
        pageNo: Number(pageNo),
        src: image.src,
        generated: image.kind === "generated",
      }))
      .sort((left, right) => left.pageNo - right.pageNo);
    if (slideEntries.length) {
      const exact = slideEntries.find((slide) => slide.pageNo === requestedPage);
      const displayed = exact ?? currentSlide ?? slideEntries.at(-1);
      if (displayed) {
        return renderSlide(displayed);
      }
    }
    if (currentSlide) return renderSlide(currentSlide);
    return (
      <div className="action-placeholder">
        <p>正在等待 PPT 页面。</p>
      </div>
    );
  }
  if (action.type === "ASK_QUIZ") {
    const quiz = action.payload.quiz;
    const resultForAction = quizResult?.actionId === action.id ? quizResult : null;
    const selectedIndex = resultForAction?.selectedIndex
      ?? (pendingSelection?.actionId === action.id ? pendingSelection.index : null);
    return (
      <div className="quiz-action">
        <small>EVALUATION CHECKPOINT</small>
        <h3>{quiz.question}</h3>
        <div>
          {quiz.options.map((option, index) => (
            <button
              aria-pressed={selectedIndex === index}
              className={selectedIndex === index
                ? resultForAction
                  ? resultForAction.correct ? "selected-correct" : "selected-incorrect"
                  : "selected-pending"
                : undefined}
              disabled={answerDisabled}
              onClick={() => {
                setPendingSelection({ actionId: action.id, index });
                onAnswer(index);
              }}
              key={option}
            >
              <span>{String.fromCharCode(65 + index)}</span>
              {option}
            </button>
          ))}
        </div>
      </div>
    );
  }
  if (currentSlide) return renderSlide(currentSlide);
  return (
    <div className="action-placeholder">
      <p>正在等待下一页 PPT。</p>
    </div>
  );
}
