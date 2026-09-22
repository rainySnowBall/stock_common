"""Adaptive model selector."""

from __future__ import annotations

from dataclasses import dataclass

from stock_common.model_selection.defaults import DEFAULT_MODEL_PROFILES, SELECTION_WEIGHTS
from stock_common.model_selection.difficulty import DifficultyEstimator
from stock_common.model_selection.schemas import (
    ModelFunction,
    ModelProfile,
    ModelSelection,
    ModelSelectionError,
    ModelSelectionRequest,
    TaskDifficulty,
    coerce_function,
    function_from_route_label,
)


@dataclass(frozen=True)
class ModelSelector:
    """Choose a model by function, difficulty, capabilities, and policy weights."""

    registry: tuple[ModelProfile, ...] = DEFAULT_MODEL_PROFILES
    difficulty_estimator: DifficultyEstimator = DifficultyEstimator()
    selection_weights: dict[TaskDifficulty, dict[str, float]] | None = None

    def select(self, request: ModelSelectionRequest) -> ModelSelection:
        function = self.resolve_function(request)
        difficulty = self.difficulty_estimator.estimate(request, function)
        estimated_tokens = self.estimate_input_tokens(request.text)
        weights = self._weights_for(difficulty)

        candidates = [
            model
            for model in self.registry
            if model.supports(
                function=function,
                difficulty=difficulty,
                required_capabilities=request.required_capabilities,
                estimated_input_tokens=estimated_tokens,
            )
        ]

        fallback_used = False
        if not candidates:
            candidates = self._fallback_candidates(request, function, estimated_tokens)
            fallback_used = True

        if not candidates:
            raise ModelSelectionError(
                f"No model profile can satisfy function={function.value}, "
                f"difficulty={difficulty.value}"
            )

        ranked = sorted(
            ((self._score(model, weights, fallback_used), model) for model in candidates),
            key=lambda item: (item[0], item[1].quality_score, item[1].latency_score),
            reverse=True,
        )
        score, model = ranked[0]
        alternatives = tuple(candidate.model_id for _, candidate in ranked[1:4])

        return ModelSelection(
            model=model,
            function=function,
            difficulty=difficulty,
            score=round(score, 4),
            reason=self._reason(function, difficulty, model, fallback_used),
            alternatives=alternatives,
            metadata={
                "estimated_input_tokens": estimated_tokens,
                "required_capabilities": [
                    capability.value for capability in request.required_capabilities
                ],
                "fallback_used": fallback_used,
            },
        )

    @staticmethod
    def resolve_function(request: ModelSelectionRequest) -> ModelFunction:
        if request.function is not None:
            return coerce_function(request.function)

        if request.route_decision is None:
            return ModelFunction.CHAT

        if request.route_decision.requires_llm_parser:
            return ModelFunction.TASK_PARSER

        return function_from_route_label(request.route_decision.label)

    @staticmethod
    def estimate_input_tokens(text: str) -> int:
        if not text:
            return 0

        ascii_chars = sum(1 for char in text if ord(char) < 128)
        non_ascii_chars = len(text) - ascii_chars
        return max(1, ascii_chars // 4 + non_ascii_chars // 2)

    def _fallback_candidates(
        self,
        request: ModelSelectionRequest,
        function: ModelFunction,
        estimated_tokens: int,
    ) -> list[ModelProfile]:
        same_function = [
            model
            for model in self.registry
            if function in model.supported_functions
            and request.required_capabilities.issubset(model.capabilities)
            and (
                model.max_input_tokens is None
                or estimated_tokens <= model.max_input_tokens
            )
        ]
        if same_function:
            return same_function

        if request.function is not None:
            return []

        if function is not ModelFunction.TASK_PARSER:
            return [
                model
                for model in self.registry
                if ModelFunction.TASK_PARSER in model.supported_functions
                and request.required_capabilities.issubset(model.capabilities)
                and (
                    model.max_input_tokens is None
                    or estimated_tokens <= model.max_input_tokens
                )
            ]

        return []

    def _weights_for(self, difficulty: TaskDifficulty) -> dict[str, float]:
        weights = self.selection_weights or SELECTION_WEIGHTS
        return weights[difficulty]

    @staticmethod
    def _score(
        model: ModelProfile,
        weights: dict[str, float],
        fallback_used: bool,
    ) -> float:
        score = (
            model.quality_score * weights["quality"]
            + model.latency_score * weights["latency"]
            + model.cost_score * weights["cost"]
        )
        if fallback_used:
            score *= 0.92
        return score

    @staticmethod
    def _reason(
        function: ModelFunction,
        difficulty: TaskDifficulty,
        model: ModelProfile,
        fallback_used: bool,
    ) -> str:
        reason = (
            f"selected {model.model_id} for {function.value} "
            f"with {difficulty.value} difficulty"
        )
        if fallback_used:
            reason += " using relaxed difficulty matching"
        return reason
