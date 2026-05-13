from __future__ import annotations

from pyclaw.core.commands.builtin import register_builtin_commands
from pyclaw.core.commands.registry import CommandRegistry
from pyclaw.core.commands.spec import ALL_CHANNELS
from pyclaw.core.commands.steering import cmd_btw, cmd_steer


def test_steer_registered_with_steering_category():
    registry = CommandRegistry()
    register_builtin_commands(registry)

    spec = registry.get("/steer")
    assert spec is not None
    assert spec.category == "steering"
    assert spec.requires_idle is False
    assert spec.channels == ALL_CHANNELS
    assert spec.handler is cmd_steer
    assert spec.help_text
    assert spec.args_hint == "<message>"


def test_btw_registered_with_steering_category():
    registry = CommandRegistry()
    register_builtin_commands(registry)

    spec = registry.get("/btw")
    assert spec is not None
    assert spec.category == "steering"
    assert spec.requires_idle is False
    assert spec.channels == ALL_CHANNELS
    assert spec.handler is cmd_btw
    assert spec.help_text
    assert spec.args_hint == "<question>"


def test_total_registered_count_grows_by_2_with_new_commands():
    """Pre-existing count 21 (includes alias specs) + 2 new (/steer /btw) = 23."""
    registry = CommandRegistry()
    register_builtin_commands(registry)

    assert len(registry._specs) == 23  # noqa: SLF001
    assert "/steer" in registry._specs  # noqa: SLF001
    assert "/btw" in registry._specs  # noqa: SLF001
