from __future__ import annotations

import logging

from litellm import aembedding

logger = logging.getLogger(__name__)


class EmbeddingClient:
    def __init__(self, model: str, api_key: str, api_base: str, dimensions: int) -> None:
        self._model = model
        self._api_key = api_key
        self._api_base = api_base
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, text: str) -> list[float]:
        response = await aembedding(
            model=self._model,
            input=[text],
            api_key=self._api_key,
            api_base=self._api_base,
        )
        return response.data[0]["embedding"]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        response = await aembedding(
            model=self._model,
            input=texts,
            api_key=self._api_key,
            api_base=self._api_base,
        )
        return [item["embedding"] for item in response.data]
