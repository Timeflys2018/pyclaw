from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import httpx
import respx

from pyclaw.skills.clawhub_client import (
    ClawHubClient,
    SkillDetail,
    SkillSearchResult,
    _resolve_token,
    create_client,
)
from pyclaw.skills.models import ClawHubError

import pytest

BASE = "https://clawhub.ai"


# ---------------------------------------------------------------------------
# 1. search success
# ---------------------------------------------------------------------------


async def test_search_success() -> None:
    payload = [
        {
            "slug": "github",
            "name": "GitHub",
            "description": "GitHub CLI skill",
            "version": "1.0.0",
        },
        {
            "slug": "docker",
            "name": "Docker",
            "description": "Docker management",
            "version": "2.1.0",
        },
    ]
    with respx.mock:
        respx.get(f"{BASE}/api/v1/search", params={"q": "git"}).mock(
            return_value=httpx.Response(200, json=payload),
        )
        client = ClawHubClient(base_url=BASE)
        results = await client.search("git")
        await client.close()

    assert len(results) == 2
    assert isinstance(results[0], SkillSearchResult)
    assert results[0].slug == "github"
    assert results[0].name == "GitHub"
    assert results[0].description == "GitHub CLI skill"
    assert results[0].version == "1.0.0"
    assert results[1].slug == "docker"


# ---------------------------------------------------------------------------
# 2. search empty results
# ---------------------------------------------------------------------------


async def test_search_empty_results() -> None:
    with respx.mock:
        respx.get(f"{BASE}/api/v1/search", params={"q": "nonexistent"}).mock(
            return_value=httpx.Response(200, json=[]),
        )
        client = ClawHubClient(base_url=BASE)
        results = await client.search("nonexistent")
        await client.close()

    assert results == []


# ---------------------------------------------------------------------------
# 3. search API error
# ---------------------------------------------------------------------------


async def test_search_api_error() -> None:
    with respx.mock:
        respx.get(f"{BASE}/api/v1/search", params={"q": "broken"}).mock(
            return_value=httpx.Response(500, json={"error": "Internal Server Error"}),
        )
        client = ClawHubClient(base_url=BASE)
        with pytest.raises(ClawHubError) as exc_info:
            await client.search("broken")
        await client.close()

    assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# 4. get_detail success
# ---------------------------------------------------------------------------


async def test_get_detail_success() -> None:
    payload = {
        "skill": {
            "slug": "github",
            "displayName": "GitHub",
            "summary": "GitHub CLI skill",
        },
        "latestVersion": {
            "version": "1.2.0",
            "sha256hash": "abc123",
        },
        "owner": {
            "handle": "openclaw",
            "displayName": "OpenClaw",
        },
    }
    with respx.mock:
        respx.get(f"{BASE}/api/v1/skills/github").mock(
            return_value=httpx.Response(200, json=payload),
        )
        client = ClawHubClient(base_url=BASE)
        detail = await client.get_detail("github")
        await client.close()

    assert isinstance(detail, SkillDetail)
    assert detail.slug == "github"
    assert detail.name == "GitHub"
    assert detail.latest_version == "1.2.0"
    assert detail.author == "OpenClaw"
    assert detail.sha256hash == "abc123"


# ---------------------------------------------------------------------------
# 5. get_detail 404
# ---------------------------------------------------------------------------


async def test_get_detail_not_found() -> None:
    with respx.mock:
        respx.get(f"{BASE}/api/v1/skills/missing").mock(
            return_value=httpx.Response(404, json={"error": "Not found"}),
        )
        client = ClawHubClient(base_url=BASE)
        with pytest.raises(ClawHubError) as exc_info:
            await client.get_detail("missing")
        await client.close()

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# 6. download success
# ---------------------------------------------------------------------------


async def test_download_success() -> None:
    content = b"skill-archive-bytes-here"
    with respx.mock:
        respx.get(
            f"{BASE}/api/v1/download",
            params={"slug": "github", "version": "1.0.0"},
        ).mock(
            return_value=httpx.Response(200, content=content),
        )
        client = ClawHubClient(base_url=BASE)
        data = await client.download("github", "1.0.0")
        await client.close()

    assert data == content


# ---------------------------------------------------------------------------
# 7. download version not found
# ---------------------------------------------------------------------------


async def test_download_version_not_found() -> None:
    with respx.mock:
        respx.get(
            f"{BASE}/api/v1/download",
            params={"slug": "github", "version": "99.0.0"},
        ).mock(
            return_value=httpx.Response(404, json={"error": "Version not found"}),
        )
        client = ClawHubClient(base_url=BASE)
        with pytest.raises(ClawHubError) as exc_info:
            await client.download("github", "99.0.0")
        await client.close()

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# 8. token from env var → Authorization header sent
# ---------------------------------------------------------------------------


async def test_token_from_env_var() -> None:
    with respx.mock:
        route = respx.get(f"{BASE}/api/v1/search", params={"q": "test"}).mock(
            return_value=httpx.Response(200, json=[]),
        )
        client = ClawHubClient(base_url=BASE, token="my-secret-token")
        await client.search("test")
        await client.close()

    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer my-secret-token"


# ---------------------------------------------------------------------------
# 9. no token → no Authorization header
# ---------------------------------------------------------------------------


async def test_no_token_no_auth_header() -> None:
    with patch.dict(os.environ, {}, clear=True):
        with patch(
            "pyclaw.skills.clawhub_client._resolve_token", return_value=None
        ):
            with respx.mock:
                route = respx.get(
                    f"{BASE}/api/v1/search", params={"q": "test"}
                ).mock(
                    return_value=httpx.Response(200, json=[]),
                )
                client = ClawHubClient(base_url=BASE, token=None)
                await client.search("test")
                await client.close()

    assert "authorization" not in route.calls[0].request.headers


# ---------------------------------------------------------------------------
# 10. token resolution priority: OPENCLAW_CLAWHUB_TOKEN > CLAWHUB_TOKEN
# ---------------------------------------------------------------------------


async def test_token_resolution_priority() -> None:
    env = {
        "OPENCLAW_CLAWHUB_TOKEN": "primary-token",
        "CLAWHUB_TOKEN": "secondary-token",
        "CLAWHUB_AUTH_TOKEN": "tertiary-token",
    }
    with patch.dict(os.environ, env, clear=True):
        token = _resolve_token()
    assert token == "primary-token"


# ---------------------------------------------------------------------------
# 11. token resolution: falls through to CLAWHUB_TOKEN
# ---------------------------------------------------------------------------


async def test_token_resolution_fallback_clawhub_token() -> None:
    env = {
        "CLAWHUB_TOKEN": "secondary-token",
        "CLAWHUB_AUTH_TOKEN": "tertiary-token",
    }
    with patch.dict(os.environ, env, clear=True):
        token = _resolve_token()
    assert token == "secondary-token"


# ---------------------------------------------------------------------------
# 12. token resolution: falls through to CLAWHUB_AUTH_TOKEN
# ---------------------------------------------------------------------------


async def test_token_resolution_fallback_auth_token() -> None:
    env = {"CLAWHUB_AUTH_TOKEN": "tertiary-token"}
    with patch.dict(os.environ, env, clear=True):
        token = _resolve_token()
    assert token == "tertiary-token"


# ---------------------------------------------------------------------------
# 13. token resolution: reads from config file
# ---------------------------------------------------------------------------


async def test_token_resolution_from_config_file(tmp_path: Path) -> None:
    config_dir = tmp_path / ".config" / "clawhub"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "config.json"
    config_file.write_text(json.dumps({"auth": {"accessToken": "file-token"}}))

    with patch.dict(os.environ, {}, clear=True):
        with patch(
            "pyclaw.skills.clawhub_client._config_file_path",
            return_value=config_file,
        ):
            token = _resolve_token()
    assert token == "file-token"


# ---------------------------------------------------------------------------
# 14. token resolution: no env var, no config file → None
# ---------------------------------------------------------------------------


async def test_token_resolution_returns_none() -> None:
    with patch.dict(os.environ, {}, clear=True):
        with patch(
            "pyclaw.skills.clawhub_client._config_file_path",
            return_value=Path("/nonexistent/config.json"),
        ):
            token = _resolve_token()
    assert token is None


# ---------------------------------------------------------------------------
# 15. create_client convenience function
# ---------------------------------------------------------------------------


async def test_create_client() -> None:
    with patch(
        "pyclaw.skills.clawhub_client._resolve_token", return_value=None
    ):
        client = await create_client(base_url="https://custom.hub")
    assert isinstance(client, ClawHubClient)
    await client.close()


# ---------------------------------------------------------------------------
# 16. token from nested config key: credentials.token
# ---------------------------------------------------------------------------


async def test_token_resolution_nested_credentials(tmp_path: Path) -> None:
    config_dir = tmp_path / ".config" / "clawhub"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "config.json"
    config_file.write_text(
        json.dumps({"credentials": {"token": "nested-token"}})
    )

    with patch.dict(os.environ, {}, clear=True):
        with patch(
            "pyclaw.skills.clawhub_client._config_file_path",
            return_value=config_file,
        ):
            token = _resolve_token()
    assert token == "nested-token"
