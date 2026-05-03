from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyclaw.storage.memory.embedding import EmbeddingClient


@pytest.fixture
def client() -> EmbeddingClient:
    return EmbeddingClient(
        model="text-embedding-ada-002",
        api_key="test-key",
        api_base="https://api.example.com",
        dimensions=1536,
    )


def test_dimensions(client: EmbeddingClient) -> None:
    assert client.dimensions == 1536


@patch("pyclaw.storage.memory.embedding.aembedding", new_callable=AsyncMock)
async def test_embed(mock_aembedding: AsyncMock, client: EmbeddingClient) -> None:
    mock_aembedding.return_value = MagicMock(data=[{"embedding": [0.1, 0.2, 0.3]}])

    result = await client.embed("hello world")

    mock_aembedding.assert_awaited_once_with(
        model="text-embedding-ada-002",
        input=["hello world"],
        api_key="test-key",
        api_base="https://api.example.com",
    )
    assert result == [0.1, 0.2, 0.3]


@patch("pyclaw.storage.memory.embedding.aembedding", new_callable=AsyncMock)
async def test_embed_batch(mock_aembedding: AsyncMock, client: EmbeddingClient) -> None:
    mock_aembedding.return_value = MagicMock(
        data=[
            {"embedding": [0.1, 0.2, 0.3]},
            {"embedding": [0.4, 0.5, 0.6]},
        ]
    )

    result = await client.embed_batch(["hello", "world"])

    mock_aembedding.assert_awaited_once_with(
        model="text-embedding-ada-002",
        input=["hello", "world"],
        api_key="test-key",
        api_base="https://api.example.com",
    )
    assert result == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
