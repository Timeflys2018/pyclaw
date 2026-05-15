from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from pyclaw.skills.models import ClawHubError

logger = logging.getLogger(__name__)

_TOKEN_ENV_VARS = (
    "OPENCLAW_CLAWHUB_TOKEN",
    "CLAWHUB_TOKEN",
    "CLAWHUB_AUTH_TOKEN",
)

_TOKEN_KEYS = {"accessToken", "authToken", "apiToken", "token"}
_NESTED_KEYS = {"auth", "session", "credentials", "user"}

_TIMEOUT = 30.0


def _config_file_path() -> Path:
    return Path.home() / ".config" / "clawhub" / "config.json"


def _find_token_in_dict(data: dict[str, Any]) -> str | None:
    for key in _TOKEN_KEYS:
        val = data.get(key)
        if isinstance(val, str) and val:
            return val

    for key in _NESTED_KEYS:
        nested = data.get(key)
        if isinstance(nested, dict):
            result = _find_token_in_dict(nested)
            if result is not None:
                return result

    return None


def _resolve_token() -> str | None:
    for var in _TOKEN_ENV_VARS:
        val = os.environ.get(var, "").strip()
        if val:
            return val

    config_path = _config_file_path()
    if config_path.is_file():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return _find_token_in_dict(data)
        except (json.JSONDecodeError, OSError):
            pass

    return None


@dataclass
class SkillSearchResult:
    slug: str
    name: str
    description: str
    version: str


@dataclass
class SkillDetail:
    slug: str
    name: str
    description: str
    latest_version: str
    author: str | None = None
    sha256hash: str | None = None


class ClawHubClient:
    def __init__(
        self,
        base_url: str = "https://clawhub.ai",
        token: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token if token is not None else _resolve_token()

        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        logger.debug("token present: %s", "yes" if self._token else "no")

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=_TIMEOUT,
        )

    async def search(self, query: str) -> list[SkillSearchResult]:
        resp = await self._client.get("/api/v1/search", params={"q": query})
        if not resp.is_success:
            raise ClawHubError(
                message=resp.text,
                status_code=resp.status_code,
            )
        data = resp.json()
        items: list[Any] = (
            data if isinstance(data, list) else data.get("results", data.get("skills", []))
        )
        if not isinstance(items, list):
            items = []
        return [
            SkillSearchResult(
                slug=item.get("slug", ""),
                name=item.get("displayName", item.get("name", "")),
                description=item.get("summary", item.get("description", "")),
                version=item.get("version") or item.get("latestVersion") or "",
            )
            for item in items
            if isinstance(item, dict)
        ]

    async def get_detail(self, slug: str) -> SkillDetail:
        resp = await self._client.get(f"/api/v1/skills/{slug}")
        if not resp.is_success:
            raise ClawHubError(
                message=resp.text,
                status_code=resp.status_code,
            )
        data = resp.json()
        skill_data = data.get("skill", data)
        latest_ver = data.get("latestVersion", {})
        owner = data.get("owner", {})
        return SkillDetail(
            slug=skill_data.get("slug", slug),
            name=skill_data.get("displayName", skill_data.get("name", "")),
            description=skill_data.get("summary", skill_data.get("description", "")),
            latest_version=latest_ver.get("version", "")
            if isinstance(latest_ver, dict)
            else str(latest_ver or ""),
            author=owner.get("displayName", owner.get("handle"))
            if isinstance(owner, dict)
            else None,
            sha256hash=latest_ver.get("sha256hash") if isinstance(latest_ver, dict) else None,
        )

    async def download(self, slug: str, version: str) -> bytes:
        resp = await self._client.get(
            "/api/v1/download",
            params={"slug": slug, "version": version},
        )
        if not resp.is_success:
            raise ClawHubError(
                message=resp.text,
                status_code=resp.status_code,
            )
        return resp.content

    async def close(self) -> None:
        await self._client.aclose()


async def create_client(base_url: str = "https://clawhub.ai") -> ClawHubClient:
    return ClawHubClient(base_url=base_url)
