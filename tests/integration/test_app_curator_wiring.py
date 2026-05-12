"""Test that app.py lifespan wires lock_manager+task_manager into curator loop (Phase D1)."""

from __future__ import annotations

import inspect

from pyclaw.core.curator import create_curator_loop


def test_create_curator_loop_signature_exposes_required_params() -> None:
    sig = inspect.signature(create_curator_loop)
    params = sig.parameters

    assert "lock_manager" in params
    assert "task_manager" in params
    assert params["lock_manager"].default is inspect.Parameter.empty
    assert params["task_manager"].default is inspect.Parameter.empty
    assert params["lock_manager"].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["task_manager"].kind == inspect.Parameter.KEYWORD_ONLY


def test_app_lifespan_forwards_lock_manager_to_curator() -> None:
    import pyclaw.app as app_module

    source = inspect.getsource(app_module)
    assert "create_curator_loop(" in source
    assert "lock_manager=lock_manager" in source
    assert "task_manager=task_manager" in source


def test_app_lifespan_forwards_lock_manager_to_runner_deps() -> None:
    import pyclaw.app as app_module

    source = inspect.getsource(app_module)
    call_sites = source.split("create_agent_runner_deps(")
    assert len(call_sites) >= 2
    call_and_after = call_sites[1]
    closing_paren = call_and_after.find(")\n")
    call_args = call_and_after[:closing_paren]
    assert "lock_manager=lock_manager" in call_args


def test_curator_spawn_name_is_curator_scan() -> None:
    import pyclaw.app as app_module

    source = inspect.getsource(app_module)
    assert '"curator-scan"' in source
