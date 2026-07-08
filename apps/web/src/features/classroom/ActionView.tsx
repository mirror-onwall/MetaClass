import { api } from "../../shared/api";
import type { TeachingAction } from "../../shared/types";

export function ActionView({
  action,
  materialId,
  onAnswer,
}: {
  action: TeachingAction | null;
  materialId?: string;
  onAnswer: (index: number) => void;
}) {
  if (!action) {
    return (
      <div className="action-placeholder">
        <span>READY</span>
        <p>Controller 会严格按计划推进课堂。</p>
      </div>
    );
  }
  if (action.type === "SHOW_PAGE") {
    const source = action.payload.source_ref;
    return (
      <div className="slide-action">
        <img
          src={materialId ? api.pageImage(materialId, source.page_no) : ""}
          alt={`课件第 ${source.page_no} 页`}
        />
        <span>正在展示 · 第 {source.page_no} 页</span>
      </div>
    );
  }
  if (action.type === "ASK_QUIZ") {
    const quiz = action.payload.quiz;
    return (
      <div className="quiz-action">
        <small>EVALUATION CHECKPOINT</small>
        <h3>{quiz.question}</h3>
        <div>
          {quiz.options.map((option, index) => (
            <button onClick={() => onAnswer(index)} key={option}>
              <span>{String.fromCharCode(65 + index)}</span>
              {option}
            </button>
          ))}
        </div>
      </div>
    );
  }
  if (action.type === "EXPLAIN" || action.type === "REMEDIATE") {
    return (
      <div className="script-action">
        <span>{action.type === "EXPLAIN" ? "TEACHER" : "REMEDIATE"}</span>
        <p>{action.payload.text}</p>
      </div>
    );
  }
  if (action.type === "END") {
    return (
      <div className="script-action">
        <span>SECTION END</span>
        <p>{action.payload.summary}</p>
      </div>
    );
  }
  return (
    <div className="action-placeholder">
      <p>Evaluator 正在处理回答。</p>
    </div>
  );
}
