from __future__ import annotations

import os

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: requires real Redis at PYCLAW_TEST_REDIS_* env vars",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if not os.environ.get("PYCLAW_TEST_REDIS_HOST"):
        skip = pytest.mark.skip(reason="PYCLAW_TEST_REDIS_HOST not set")
        for item in items:
            if item.get_closest_marker("integration"):
                item.add_marker(skip)
