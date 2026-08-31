"""Shared interaction planning for every presentation route."""

from metaclass.modules.interaction_planning.service import (
    InteractionBudget,
    InteractionIntensity,
    InteractionPlanningResult,
    InteractionPlanningService,
    SlideCandidate,
    SlideEligibility,
)

__all__ = [
    "InteractionBudget",
    "InteractionIntensity",
    "InteractionPlanningResult",
    "InteractionPlanningService",
    "SlideCandidate",
    "SlideEligibility",
]
from .contracts import (
    InteractionBlueprint,
    InteractionPlanningContext,
    InteractionPolicy,
    ScriptedInteractionBank,
)
from .paper_deck_adapter import PaperDeckInteractionAdapter
from .pipeline import (
    InteractionValidator,
    QuestionGenerator,
    TeachingNodeSelector,
    UnifiedInteractionPlanningPipeline,
)
from .presentation_adapter import PresentationInteractionAdapter

__all__ = [
    "InteractionBlueprint",
    "InteractionPlanningContext",
    "InteractionPolicy",
    "InteractionValidator",
    "PaperDeckInteractionAdapter",
    "PresentationInteractionAdapter",
    "QuestionGenerator",
    "ScriptedInteractionBank",
    "TeachingNodeSelector",
    "UnifiedInteractionPlanningPipeline",
]
