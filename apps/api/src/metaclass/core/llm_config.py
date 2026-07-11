"""LLM and embedding model configuration.

Keep real API keys out of commits when possible. Values in this file can be
overridden by environment variables from the project root `.env`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from metaclass.core.config import settings


VLLM_CONFIG: dict[str, Any] = {
    "provider": settings.llm_provider,
    "base_url": settings.llm_base_url,
    "api_key": settings.llm_api_key or "....",
    "model_name": settings.llm_model,
    "timeout_seconds": settings.llm_timeout_seconds,
}


# Embedding model configuration. The current MVP does not call embeddings yet,
# but retrieval/vector search can read this config later.
EMBEDDING_CONFIG: dict[str, Any] = {
    "provider": settings.embedding_provider,
    "model_path": settings.embedding_model_path,
    "base_url": settings.embedding_base_url,
    "api_key": settings.embedding_api_key,
    "model_name": settings.embedding_model,
    "dimension": settings.embedding_dimension,
}


# Generation parameters. `max_tokens` can be adjusted to 1024, 2048, or up to
# 8192 when the upstream OpenAI-compatible API supports it.
GENERATION_CONFIG: dict[str, Any] = {
    "temperature": settings.llm_temperature,
    "max_tokens": settings.llm_max_tokens,
}


@dataclass(frozen=True)
class LLMRuntimeConfig:
    provider: str
    base_url: str
    api_key: str | None
    model: str
    timeout_seconds: float
    temperature: float
    max_tokens: int


def get_llm_runtime_config() -> LLMRuntimeConfig:
    api_key = str(VLLM_CONFIG.get("api_key") or "").strip()
    provider = str(VLLM_CONFIG.get("provider") or "auto").strip().lower()
    if provider == "auto":
        provider = "openai-compatible" if _looks_like_real_api_key(api_key) else "fake"

    return LLMRuntimeConfig(
        provider=provider,
        base_url=str(VLLM_CONFIG["base_url"]),
        api_key=api_key if _looks_like_real_api_key(api_key) else None,
        model=str(VLLM_CONFIG["model_name"]),
        timeout_seconds=float(VLLM_CONFIG["timeout_seconds"]),
        temperature=float(GENERATION_CONFIG["temperature"]),
        max_tokens=int(GENERATION_CONFIG["max_tokens"]),
    )


def _looks_like_real_api_key(api_key: str) -> bool:
    if not api_key or api_key in {"....", "...", "your-api-key", "your key", "你的 key"}:
        return False
    try:
        api_key.encode("ascii")
    except UnicodeEncodeError:
        return False
    return len(api_key) >= 12
