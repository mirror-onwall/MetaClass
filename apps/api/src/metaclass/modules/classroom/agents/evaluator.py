from metaclass.core.schemas import SchemaModel
from metaclass.modules.assessment.schemas import Evidence
from metaclass.modules.classroom.schemas import AskQuizAction


class QuizEvaluation(SchemaModel):
    correct: bool
    feedback: str
    evidence: Evidence


class EvaluatorAgent:
    """Evaluates classroom evidence produced by student interactions."""

    def evaluate_quiz(
        self,
        session_id: str,
        quiz_action: AskQuizAction,
        selected_index: int,
        evidence_id: str,
    ) -> QuizEvaluation:
        quiz = quiz_action.payload.quiz
        correct = selected_index == quiz.correct_index
        evidence = Evidence(
            id=evidence_id,
            session_id=session_id,
            action_id=quiz_action.id,
            type="QUIZ",
            knowledge_point=quiz.knowledge_point,
            score=1.0 if correct else 0.0,
            weight=1.0,
            confidence=1.0,
            note=f"selected={selected_index}, correct={quiz.correct_index}",
        )
        feedback = (
            "回答正确。"
            if correct
            else f"回答不正确，正确答案是：{quiz.options[quiz.correct_index]}"
        )
        return QuizEvaluation(correct=correct, feedback=feedback, evidence=evidence)
