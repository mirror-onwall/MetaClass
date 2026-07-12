import { api } from "../../shared/api";
import type { TeachingAction } from "../../shared/types";

export function ActionView({
  action,
  materialId,
  presentationSlideImages = {},
  currentSlide,
  onAnswer,
  answerDisabled = false,
}: {
  action: TeachingAction | null;
  materialId?: string;
  presentationSlideImages?: Record<number, string>;
  currentSlide?: { src: string; pageNo: number; generated: boolean } | null;
  onAnswer: (index: number) => void;
  answerDisabled?: boolean;
}) {
  function renderSlide(slide: { src: string; pageNo: number; generated: boolean }) {
    return (
      <div className="slide-action">
        <img src={slide.src} alt={`PPT 第 ${slide.pageNo} 页`} />
        <span>{slide.generated ? "正在展示生成 PPT" : "正在展示原始页面"} · 第 {slide.pageNo} 页</span>
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
  if (action.type === "SHOW_PAGE") {
    const source = action.payload.source_ref;
    const generatedImage = presentationSlideImages[source.page_no];
    const imageSrc = generatedImage ?? (materialId ? api.pageImage(materialId, source.page_no) : "");
    return renderSlide({ src: imageSrc, pageNo: source.page_no, generated: Boolean(generatedImage) });
  }
  if (action.type === "ASK_QUIZ") {
    const quiz = action.payload.quiz;
    return (
      <div className="quiz-action">
        <small>EVALUATION CHECKPOINT</small>
        <h3>{quiz.question}</h3>
        <div>
          {quiz.options.map((option, index) => (
            <button disabled={answerDisabled} onClick={() => onAnswer(index)} key={option}>
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
