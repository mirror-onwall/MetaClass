from metaclass.modules.assessment.schemas import Evidence, MasteryEstimate


def estimate_mastery(session_id: str, evidence: list[Evidence]) -> list[MasteryEstimate]:
    grouped: dict[str, list[Evidence]] = {}
    for item in evidence:
        grouped.setdefault(item.knowledge_point, []).append(item)
    estimates = []
    for point, items in grouped.items():
        denominator = sum(item.weight * item.confidence for item in items)
        value = (
            sum(item.score * item.weight * item.confidence for item in items) / denominator
            if denominator
            else 0.5
        )
        estimates.append(
            MasteryEstimate(
                session_id=session_id,
                knowledge_point=point,
                value=round(value, 3),
                evidence_count=len(items),
            )
        )
    return estimates
