from __future__ import annotations

from pyclaw.core.agent.run_control import RunControl, SteerMessage


def test_steer_message_has_kind_and_text():
    m = SteerMessage(kind="steer", text="hi")
    assert m.kind == "steer"
    assert m.text == "hi"


def test_sidebar_kind_valid():
    m = SteerMessage(kind="sidebar", text="side question")
    assert m.kind == "sidebar"


def test_pending_steers_default_is_empty_list():
    rc = RunControl()
    assert rc.pending_steers == []


def test_pending_steers_fresh_instances_have_independent_lists():
    rc_a = RunControl()
    rc_b = RunControl()
    rc_a.pending_steers.append(SteerMessage(kind="steer", text="a"))
    assert rc_b.pending_steers == [], (
        "Mutable default-argument bug check: appending to rc_a must not affect rc_b"
    )
    assert id(rc_a.pending_steers) != id(rc_b.pending_steers)


def test_stop_clears_pending_steers_and_sets_abort_event():
    rc = RunControl()
    rc.pending_steers.append(SteerMessage(kind="steer", text="hi"))
    rc.pending_steers.append(SteerMessage(kind="sidebar", text="btw"))
    assert len(rc.pending_steers) == 2

    rc.stop()

    assert rc.pending_steers == [], "stop() must clear pending_steers"
    assert rc.abort_event.is_set(), "stop() must set abort_event"


def test_dataclass_permits_caller_to_append_beyond_any_cap():
    """RunControl itself enforces no cap — cap enforcement is the handler's job."""
    rc = RunControl()
    for i in range(10):
        rc.pending_steers.append(SteerMessage(kind="steer", text=f"msg{i}"))
    assert len(rc.pending_steers) == 10


def test_is_active_semantics_unchanged():
    rc = RunControl()
    assert not rc.is_active()

    rc.active = True
    assert rc.is_active()

    rc.stop()
    assert not rc.is_active(), "After stop(), is_active should be False due to abort_event"
