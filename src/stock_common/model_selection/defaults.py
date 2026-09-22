"""Adapters from project config to model-selection contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from stock_common.config import MODEL_PROFILES_CONFIG, MODEL_SELECTION_WEIGHTS_CONFIG
from stock_common.model_selection.schemas import (
    ModelCapability,
    ModelFunction,
    ModelProfile,
    TaskDifficulty,
)


def build_model_profiles(
    raw_profiles: Sequence[Mapping[str, Any]] = MODEL_PROFILES_CONFIG,
) -> tuple[ModelProfile, ...]:
    """Convert config dictionaries into validated ModelProfile objects."""

    return tuple(_build_model_profile(raw_profile) for raw_profile in raw_profiles)


def build_selection_weights(
    raw_weights: Mapping[str, Mapping[str, float]] = MODEL_SELECTION_WEIGHTS_CONFIG,
) -> dict[TaskDifficulty, dict[str, float]]:
    """Convert config dictionaries into enum-keyed selection weights."""

    return {
        TaskDifficulty(difficulty): {
            "quality": weights["quality"],
            "latency": weights["latency"],
            "cost": weights["cost"],
        }
        for difficulty, weights in raw_weights.items()
    }


def _build_model_profile(raw_profile: Mapping[str, Any]) -> ModelProfile:
    return ModelProfile(
        model_id=str(raw_profile["model_id"]),
        provider=str(raw_profile.get("provider", "configured")),
        display_name=_optional_str(raw_profile.get("display_name")),
        model_name_env=_optional_str(raw_profile.get("model_name_env")),
        base_url_env=_optional_str(raw_profile.get("base_url_env")),
        api_key_env=_optional_str(raw_profile.get("api_key_env")),
        timeout_seconds_env=_optional_str(raw_profile.get("timeout_seconds_env")),
        max_retries_env=_optional_str(raw_profile.get("max_retries_env")),
        model_path_env=_optional_str(raw_profile.get("model_path_env")),
        model_version_env=_optional_str(raw_profile.get("model_version_env")),
        supported_functions=frozenset(
            ModelFunction(function)
            for function in raw_profile["supported_functions"]
        ),
        supported_difficulties=frozenset(
            TaskDifficulty(difficulty)
            for difficulty in raw_profile["supported_difficulties"]
        ),
        capabilities=frozenset(
            ModelCapability(capability)
            for capability in raw_profile.get("capabilities", ())
        ),
        quality_score=float(raw_profile.get("quality_score", 0.5)),
        latency_score=float(raw_profile.get("latency_score", 0.5)),
        cost_score=float(raw_profile.get("cost_score", 0.5)),
        max_input_tokens=_optional_int(raw_profile.get("max_input_tokens")),
        metadata=dict(raw_profile.get("metadata", {})),
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


DEFAULT_MODEL_PROFILES: tuple[ModelProfile, ...] = build_model_profiles()
SELECTION_WEIGHTS: dict[TaskDifficulty, dict[str, float]] = build_selection_weights()
