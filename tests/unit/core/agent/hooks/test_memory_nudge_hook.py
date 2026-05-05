import pytest

from pyclaw.core.agent.hooks.memory_nudge_hook import MemoryNudgeHook, _NUDGE_TEXT
from pyclaw.core.hooks import PromptBuildContext, ResponseObservation


def _ctx(session_id: str = "s1") -> PromptBuildContext:
    return PromptBuildContext(
        session_id=session_id, workspace_id="ws", agent_id="default"
    )


def _obs(session_id: str = "s1", tool_calls: list | None = None) -> ResponseObservation:
    return ResponseObservation(
        session_id=session_id,
        assistant_text="",
        tool_calls=tool_calls or [],
    )


async def test_nudge_fires_on_nth_turn_with_custom_interval():
    hook = MemoryNudgeHook(interval=3)
    results = []
    for _ in range(9):
        r = await hook.before_prompt_build(_ctx())
        results.append(r is not None)
    assert results == [False, False, True, False, False, True, False, False, True]


async def test_non_nudge_turns_return_none():
    hook = MemoryNudgeHook(interval=5)
    for i in range(1, 5):
        r = await hook.before_prompt_build(_ctx())
        assert r is None, f"Turn {i} should return None"


async def test_nudge_result_contains_append_text():
    hook = MemoryNudgeHook(interval=2)
    await hook.before_prompt_build(_ctx())
    r = await hook.before_prompt_build(_ctx())
    assert r is not None
    assert r.append == _NUDGE_TEXT


async def test_nudge_text_contains_nudge_marker():
    assert "<nudge>" in _NUDGE_TEXT
    assert "</nudge>" in _NUDGE_TEXT


async def test_default_interval_is_10():
    hook = MemoryNudgeHook()
    assert hook._interval == 10


async def test_interval_zero_raises_value_error():
    with pytest.raises(ValueError, match="interval must be positive"):
        MemoryNudgeHook(interval=0)


async def test_interval_negative_raises_value_error():
    with pytest.raises(ValueError, match="interval must be positive"):
        MemoryNudgeHook(interval=-5)


async def test_memorize_call_resets_counter():
    hook = MemoryNudgeHook(interval=3)
    for _ in range(2):
        await hook.before_prompt_build(_ctx())
    # count=2; memorize resets to 0
    await hook.after_response(_obs(
        tool_calls=[{"function": {"name": "memorize", "arguments": "{}"}, "id": "x"}],
    ))
    # Next turn is turn 1, no nudge
    r = await hook.before_prompt_build(_ctx())
    assert r is None
    # Turn 2 - no nudge
    r = await hook.before_prompt_build(_ctx())
    assert r is None
    # Turn 3 - nudge fires again
    r = await hook.before_prompt_build(_ctx())
    assert r is not None


async def test_after_response_no_tool_calls_does_nothing():
    hook = MemoryNudgeHook(interval=3)
    for _ in range(2):
        await hook.before_prompt_build(_ctx())
    await hook.after_response(_obs(tool_calls=[]))
    # Counter should not be reset; turn 3 should nudge
    r = await hook.before_prompt_build(_ctx())
    assert r is not None


async def test_after_response_non_memorize_tool_does_not_reset():
    hook = MemoryNudgeHook(interval=3)
    for _ in range(2):
        await hook.before_prompt_build(_ctx())
    await hook.after_response(_obs(
        tool_calls=[{"function": {"name": "bash", "arguments": "{}"}, "id": "y"}],
    ))
    # Counter should not be reset; turn 3 nudges
    r = await hook.before_prompt_build(_ctx())
    assert r is not None


async def test_after_response_memorize_alongside_other_calls_resets():
    hook = MemoryNudgeHook(interval=3)
    for _ in range(2):
        await hook.before_prompt_build(_ctx())
    await hook.after_response(_obs(
        tool_calls=[
            {"function": {"name": "bash", "arguments": "{}"}, "id": "a"},
            {"function": {"name": "memorize", "arguments": "{}"}, "id": "b"},
            {"function": {"name": "read", "arguments": "{}"}, "id": "c"},
        ],
    ))
    # Counter reset; turn 1 no nudge
    r = await hook.before_prompt_build(_ctx())
    assert r is None


async def test_independent_counters_per_session():
    hook = MemoryNudgeHook(interval=2)
    # s1 turn 1
    r = await hook.before_prompt_build(_ctx("s1"))
    assert r is None
    # s2 turn 1
    r = await hook.before_prompt_build(_ctx("s2"))
    assert r is None
    # s1 turn 2 - nudge
    r = await hook.before_prompt_build(_ctx("s1"))
    assert r is not None
    # s2 turn 2 - nudge
    r = await hook.before_prompt_build(_ctx("s2"))
    assert r is not None
    # s1 turn 3 - no nudge
    r = await hook.before_prompt_build(_ctx("s1"))
    assert r is None
    # s2 turn 3 - no nudge
    r = await hook.before_prompt_build(_ctx("s2"))
    assert r is None
