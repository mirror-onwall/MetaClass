from __future__ import annotations

import json
import math
import ssl
from typing import Protocol
from urllib import error, request

import certifi


class EmbeddingProvider(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAICompatibleEmbeddingProvider:
    name = "openai-compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        dimension: int | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dimension = dimension
        self.timeout_seconds = timeout_seconds
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload: dict[str, object] = {"model": self.model, "input": texts}
        if self.dimension:
            payload["dimensions"] = self.dimension
        req = request.Request(
            f"{self.base_url}/embeddings",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(
                req, timeout=self.timeout_seconds, context=self.ssl_context
            ) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Embedding request failed: {exc}") from exc
        try:
            rows = sorted(body["data"], key=lambda row: row["index"])
            vectors = [[float(value) for value in row["embedding"]] for row in rows]
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Unexpected embedding response shape: {body}") from exc
        if len(vectors) != len(texts):
            raise RuntimeError("Embedding response count does not match input count")
        return vectors


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


def build_embedding_provider(
    *, provider: str, base_url: str, api_key: str, model: str, dimension: int
) -> EmbeddingProvider | None:
    normalized = provider.strip().lower()
    if normalized in {"openai", "openai-compatible", "remote"} and api_key.strip():
        return OpenAICompatibleEmbeddingProvider(
            base_url=base_url,
            api_key=api_key,
            model=model,
            dimension=dimension,
        )
    return None
