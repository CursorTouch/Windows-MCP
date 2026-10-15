"""Missed-release recovery without physical input or installed hooks."""

import ctypes
from unittest.mock import Mock

import pytest

from windows_mcp.desktop import control
from windows_mcp.desktop.control_recovery import StaleInputRecovery


@pytest.fixture
def case(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(control.time, "monotonic", lambda: clock[0])
    owner = control.ControlCoordinator()
    owner._state = "user"
    owner._last_user = owner._last_physical_event = 90.0
    owner._pressed.add(0xA4)
    return owner, StaleInputRecovery(), clock


def samples(case, query, count=5):
    owner, recovery, clock = case
    for _ in range(count):
        recovery.sample(owner, clock[0], query)
        clock[0] += 0.101


@pytest.mark.parametrize("result", [0, 1])
def test_fifth_up_clears_records_and_allows_call(case, result):
    owner, _, _ = case
    owner._mouse_down.update(range(1, 6))
    owner._delivered_keys.update({0xA4, 0x42})
    owner._quarantine.update({0xA4, 0x42})
    owner._delivered_mouse.update({1, 2, 3, 4, 5, 9})
    query = Mock(return_value=result)
    samples(case, query, 4)
    assert owner.status()["state"] == "user"
    assert owner._pressed and owner._mouse_down
    samples(case, query, 1)
    assert owner.status()["state"] == "ready"
    assert not owner._pressed and not owner._mouse_down
    assert owner._delivered_keys == owner._quarantine == {0x42}
    assert owner._delivered_mouse == {9}
    assert {call.args[0] for call in query.call_args_list} == {0xA4, 1, 2, 4, 5, 6}
    token = owner.begin_call("Probe")
    owner.checkpoint(token)
    owner.end_call(token)


def test_fast_polls_and_status_cannot_accumulate_samples(case):
    owner, recovery, clock = case
    query = Mock(return_value=0)
    recovery.sample(owner, clock[0], query)
    for _ in range(30):
        clock[0] += 0.001
        owner.status()
        recovery.sample(owner, clock[0], query)
    assert query.call_count == 1
    assert owner._pressed


@pytest.mark.parametrize("during_query", [False, True])
def test_coordinator_contention_never_blocks_pump_and_restarts_samples(case, during_query):
    owner, recovery, clock = case
    samples(case, lambda vk: 0, 4)

    class ContendedLock:
        def acquire(self, blocking=True):
            assert not blocking, "The hook pump must not wait for the coordinator"
            return False

        def __enter__(self):
            self.acquire()

        def __exit__(self, *args):
            pass

    original = owner._lock
    if not during_query:
        owner._lock = ContendedLock()

    def query(vk):
        owner._lock = ContendedLock()
        return 0

    try:
        recovery.sample(owner, clock[0], query)
        assert owner._pressed
    finally:
        owner._lock = original
    samples(case, lambda vk: 0, 4)
    assert owner._pressed
    samples(case, lambda vk: 0, 1)
    assert not owner._pressed


def test_any_down_resets_complete_sequence(case):
    owner, _, _ = case
    owner._mouse_down.add(3)
    samples(case, lambda vk: 0, 4)
    queried = []

    def mixed(vk):
        queried.append(vk)
        return -32768 if vk == 4 else 0

    samples(case, mixed, 1)
    assert set(queried) == {0xA4, 4}
    assert owner._pressed and owner._mouse_down
    samples(case, lambda vk: 0, 4)
    assert owner._pressed and owner._mouse_down
    samples(case, lambda vk: 0, 1)
    assert not owner._pressed and not owner._mouse_down


def test_real_hold_never_times_out(case):
    owner, _, clock = case
    for _ in range(70):
        samples(case, lambda vk: 0x8000, 1)
        clock[0] += 1
    assert owner._pressed == {0xA4}


@pytest.mark.parametrize("state", ["user", "ready", "takeover_pending"])
def test_new_input_restarts_idle_and_confirmation(case, state):
    owner, _, clock = case
    owner._state = state
    samples(case, lambda vk: 0, 4)
    owner._last_physical_event = clock[0]
    query = Mock(return_value=0)
    samples(case, query, 1)
    query.assert_not_called()
    clock[0] += 10
    samples(case, query, 4)
    assert owner._pressed
    samples(case, query, 1)
    assert not owner._pressed


@pytest.mark.parametrize(
    "change",
    [
        "input",
        "user_stamp",
        "state",
        "generation",
        "keys",
        "buttons",
        "emergency",
        "stop",
        "disabled",
    ],
)
def test_change_during_query_cancels_cleanup(case, change):
    owner, _, clock = case
    samples(case, lambda vk: 0, 4)

    def query(vk):
        if change == "input":
            owner._last_physical_event = clock[0]
        elif change == "user_stamp":
            owner._last_user = clock[0]
        elif change == "state":
            owner._state = "unavailable"
        elif change == "generation":
            owner._generation += 1
        elif change == "keys":
            owner._pressed.add(0x42)
        elif change == "buttons":
            owner._mouse_down.add(1)
        elif change == "emergency":
            owner._emergency = True
        elif change == "stop":
            owner._stop.set()
        else:
            owner.enabled = False
        return 0

    samples(case, query, 1)
    assert 0xA4 in owner._pressed


@pytest.mark.parametrize("state", ["ai", "unavailable"])
def test_active_or_unavailable_states_are_not_sampled(case, state):
    owner, _, _ = case
    owner._state = state
    query = Mock(return_value=0)
    samples(case, query)
    query.assert_not_called()


def test_new_monitor_does_not_inherit_confirmations(case):
    owner, _, clock = case
    samples(case, lambda vk: 0, 4)
    restarted = owner, StaleInputRecovery(), clock
    samples(restarted, lambda vk: 0, 4)
    assert owner._pressed
    samples(restarted, lambda vk: 0, 1)
    assert not owner._pressed


def test_slow_query_cannot_compress_next_confirmation(case):
    owner, recovery, clock = case
    observed = []

    def slow_query(vk):
        clock[0] += 0.095
        observed.append(clock[0])
        return 0

    recovery.sample(owner, clock[0], slow_query)
    clock[0] = 100.101
    recovery.sample(owner, clock[0], lambda vk: observed.append(clock[0]) or 0)
    assert len(observed) == 1
    assert recovery._count == 1
    assert owner._pressed


def test_lock_delay_cannot_compress_actual_query_spacing(case):
    owner, recovery, clock = case
    original_lock = owner._lock

    class DelayedLock:
        def acquire(self, blocking=True):
            assert original_lock.acquire(blocking=blocking)
            clock[0] += 0.095
            return True

        def release(self):
            original_lock.release()

    owner._lock = DelayedLock()
    recovery.sample(owner, 100.0, lambda vk: 0)
    owner._lock = original_lock
    query = Mock(return_value=0)
    recovery.sample(owner, clock[0], query)
    query.assert_not_called()
    assert recovery._count == 1


def test_missed_alt_tab_release_after_ai_lease_recovers(case):
    owner, recovery, clock = case
    owner._pressed.clear()
    owner._state = "ready"
    token = owner.begin_call("FirstProbe")
    # Physical downs during suppression are recorded; both ups are lost.
    owner._pressed.update({0xA4, 0x09})
    owner._last_physical_event = clock[0]
    owner._handle(("key",))
    owner.end_call(token)
    clock[0] += 20
    owner._handle(("tick",))
    assert owner.status()["state"] == "ready"
    with pytest.raises(control.ControlBlocked, match="PHYSICAL_INPUT_HELD"):
        owner.begin_call("BlockedProbe")
    # A real held key must keep blocking even in ready; no timeout clears it.
    samples(case, lambda vk: 0x8000, 10)
    assert owner._pressed == {0xA4, 0x09}
    samples(case, lambda vk: 0, 4)
    assert owner._pressed == {0xA4, 0x09}
    samples(case, lambda vk: 0, 1)
    assert not owner._pressed
    owner.end_call(owner.begin_call("RecoveredProbe"))


def test_missed_mouse_release_after_confirmed_takeover_recovers(case):
    owner, _, clock = case
    owner._pressed.clear()
    owner._state = "ready"
    token = owner.begin_call("FirstProbe")
    owner._handle(("raw", 1, 500, 0))
    assert owner._state == "user"
    owner._mouse_down.add(1)  # A physical mouse down whose up never arrives.
    owner._last_physical_event = clock[0]
    owner.end_call(token)
    clock[0] += 20
    owner._handle(("tick",))
    assert owner._state == "user"
    samples(case, lambda vk: 0x8000, 5)
    assert owner._mouse_down == {1}
    samples(case, lambda vk: 0, 4)
    assert owner._mouse_down == {1}
    samples(case, lambda vk: 0, 1)
    assert not owner._mouse_down
    owner._handle(("tick",))
    assert owner._state == "ready"
    owner.end_call(owner.begin_call("RecoveredProbe"))


@pytest.mark.parametrize("active,lease", [(1, 0.0), (0, 200.0)])
def test_pending_active_call_or_live_lease_is_not_sampled(case, active, lease):
    owner, _, _ = case
    owner._state = "takeover_pending"
    owner._active_calls = active
    owner._lease_until = lease
    query = Mock(return_value=0)
    samples(case, query)
    query.assert_not_called()


@pytest.mark.parametrize("change", ["active_call", "lease"])
def test_pending_reactivation_during_query_cancels_cleanup(case, change):
    owner, _, clock = case
    owner._state = "takeover_pending"
    samples(case, lambda vk: 0, 4)

    def query(vk):
        if change == "active_call":
            owner._active_calls = 1
        else:
            owner._lease_until = clock[0] + 10
        return 0

    samples(case, query, 1)
    assert owner._pressed == {0xA4}


@pytest.mark.parametrize(
    "keys",
    [
        (0xA4, 0x09),
        (0xA5, 0x09),
        (0xA4, 0xA0, 0x09),
        (0x5B, 0x09),
        (0xA2, 0x43),
        (0xA3, 0xA1, 0x1B),
    ],
    ids=["alt-tab", "right-alt-tab", "alt-shift-tab", "win-tab", "ctrl-c", "ctrl-shift-esc"],
)
@pytest.mark.parametrize("lost", ["none", "modifier", "key", "all"])
@pytest.mark.parametrize("reverse", [False, True], ids=["forward-up", "reverse-up"])
def test_shortcut_hook_sequences_with_missing_releases(case, monkeypatch, keys, lost, reverse):
    """Exercise real callbacks and queue handling, without installing hooks.

    Missing callbacks represent an earlier hook swallowing selected up events;
    these tests do not claim that Windows or a third-party switcher was exercised.
    """
    owner, _, clock = case
    owner._pressed.clear()
    monkeypatch.setattr(control._user32, "CallNextHookEx", lambda *args: 0)

    def key(vk, down):
        event = control._KeyHookData()
        event.vkCode = vk
        owner._physical_key(0, 0x104 if down else 0x105, ctypes.addressof(event))
        while not owner._events.empty():
            owner._handle(owner._events.get_nowait())
        clock[0] += 0.01

    for vk in keys:
        key(vk, True)
    key(keys[-1], True)  # Typematic down is a set entry, not another held key.
    missing = set(
        keys
        if lost == "all"
        else keys[:1]
        if lost == "modifier"
        else keys[-1:]
        if lost == "key"
        else ()
    )
    for vk in reversed(keys) if reverse else keys:
        if vk not in missing:
            key(vk, False)
    assert owner._pressed == missing
    clock[0] += owner.idle_timeout_seconds
    query = Mock(return_value=0)
    samples(case, query, 4)
    assert owner._pressed == missing
    samples(case, query, 1)
    assert not owner._pressed and not owner._delivered_keys
    assert owner.status()["state"] == "ready"
    assert query.call_count == 5 * len(missing)
    token = owner.begin_call("ShortcutRecovered")
    owner.checkpoint(token)
    owner.end_call(token)


@pytest.mark.parametrize("expire_before_dispatch", [False, True])
def test_takeover_chord_queued_at_lease_expiry_does_not_latch(
    case, monkeypatch, expire_before_dispatch
):
    owner, _, clock = case
    owner._pressed.clear()
    owner._state = "ready"
    token = owner.begin_call("FirstProbe")
    owner.end_call(token)
    clock[0] = owner._lease_until - 0.01
    owner._deadline = clock[0] + 1  # Simulate a healthy watchdog heartbeat.
    monkeypatch.setattr(control._user32, "CallNextHookEx", lambda *args: 0)
    keys = (0xA2, 0xA4, 0xA0, 0x08)

    def key(vk, down):
        event = control._KeyHookData()
        event.vkCode = vk
        owner._physical_key(0, 0x100 if down else 0x101, ctypes.addressof(event))

    for vk in keys:
        key(vk, True)
    assert owner._fast_takeover
    if expire_before_dispatch:
        clock[0] += 0.02
    while not owner._events.empty():
        owner._handle(owner._events.get_nowait())
    for vk in reversed(keys):
        key(vk, False)
    while not owner._events.empty():
        owner._handle(owner._events.get_nowait())
    assert not owner._pressed and not owner._quarantine
    assert not owner._fast_takeover
    assert owner.status()["state"] == "user"
    clock[0] += owner.idle_timeout_seconds
    assert owner.status()["state"] == "ready"
    token = owner.begin_call("RecoveredProbe")
    owner.checkpoint(token)
    owner.end_call(token)
