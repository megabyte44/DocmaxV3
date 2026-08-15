"""Cancellation: cooperative, thread-safe, and owned by nobody's framework.

The tests worth having here are the concurrent ones. A token is written to from
one thread and read from several others — a batch runner cancelling its workers —
so "fires its callbacks once" has to hold under a race, not merely in sequence.

Deadlines are asserted with generous margins. These run on shared CI machines
where a sleep can overrun badly, and a timing test that fails once a fortnight
teaches people to re-run the suite instead of reading it.
"""

from __future__ import annotations

import threading
import time

import pytest

from docmax.core.cancellation import NEVER_CANCELLED, CancellationToken
from docmax.core.errors import CancelledError

#: Long enough that a loaded runner cannot lapse it mid-assertion.
GENEROUS = 30.0

#: Short enough to expire during a test, long enough not to expire before one.
BRIEF = 0.05


def wait_past(seconds: float) -> None:
    time.sleep(seconds * 4)


# ---------------------------------------------------------------------------
# Observing
# ---------------------------------------------------------------------------


def test_a_fresh_token_is_active() -> None:
    token = CancellationToken()

    assert not token.is_cancelled
    token.raise_if_cancelled()  # must not raise


def test_cancel_is_observable() -> None:
    token = CancellationToken()
    token.cancel()

    assert token.is_cancelled


def test_raise_if_cancelled_names_the_operation() -> None:
    """The message reaches a user, so it says what stopped."""
    token = CancellationToken()
    token.cancel()

    with pytest.raises(CancelledError) as caught:
        token.raise_if_cancelled(operation="merge")

    assert "merge" in str(caught.value)
    assert caught.value.context["operation"] == "merge"


def test_cancellation_is_not_the_users_fault() -> None:
    """A completed instruction, not a failure — so no "report this" prompt."""
    token = CancellationToken()
    token.cancel()

    with pytest.raises(CancelledError) as caught:
        token.raise_if_cancelled()

    assert caught.value.user_fixable is False


# ---------------------------------------------------------------------------
# Callbacks — reaching things that cannot poll
# ---------------------------------------------------------------------------


def test_callbacks_fire_once_in_registration_order() -> None:
    fired: list[str] = []
    token = CancellationToken()
    token.on_cancel(lambda: fired.append("first"))
    token.on_cancel(lambda: fired.append("second"))

    assert fired == []

    token.cancel()
    token.cancel()

    assert fired == ["first", "second"]


def test_a_released_callback_does_not_fire() -> None:
    """An operation that finishes normally must not retain a dead process handle."""
    fired: list[str] = []
    token = CancellationToken()

    release = token.on_cancel(lambda: fired.append("kill"))
    release()
    token.cancel()

    assert fired == []


def test_registering_on_a_cancelled_token_runs_immediately() -> None:
    """Otherwise the guard silently does nothing at the moment it was needed."""
    fired: list[str] = []
    token = CancellationToken()
    token.cancel()

    token.on_cancel(lambda: fired.append("kill"))

    assert fired == ["kill"]


def test_a_broken_callback_does_not_prevent_cancellation() -> None:
    """Tearing down something already broken must not stop the teardown."""
    fired: list[str] = []
    token = CancellationToken()

    def explode() -> None:
        raise RuntimeError("this process is already gone")

    token.on_cancel(explode)
    token.on_cancel(lambda: fired.append("later"))

    token.cancel()

    assert token.is_cancelled
    assert fired == ["later"], "a raising callback must not skip the ones after it"


# ---------------------------------------------------------------------------
# Deadlines
# ---------------------------------------------------------------------------


def test_an_unbounded_token_reports_no_remaining_time() -> None:
    assert CancellationToken().remaining_seconds() is None


def test_a_deadline_reports_time_left_for_a_subprocess() -> None:
    """``remaining_seconds`` is what reaches ``subprocess.run(timeout=...)``."""
    remaining = CancellationToken(timeout=GENEROUS).remaining_seconds()

    assert remaining is not None
    assert 0 < remaining <= GENEROUS


def test_a_lapsed_deadline_cancels() -> None:
    token = CancellationToken(timeout=BRIEF)
    assert not token.is_cancelled

    wait_past(BRIEF)

    assert token.is_cancelled
    assert token.remaining_seconds() == 0.0, "a timeout argument cannot take a negative"


def test_observing_a_lapsed_deadline_fires_callbacks() -> None:
    """There is no timer thread, so the callback runs when someone looks."""
    fired: list[str] = []
    token = CancellationToken(timeout=BRIEF)
    token.on_cancel(lambda: fired.append("kill"))

    wait_past(BRIEF)
    assert token.is_cancelled

    assert fired == ["kill"]


# ---------------------------------------------------------------------------
# Composition — one document inside a batch
# ---------------------------------------------------------------------------


def test_cancelling_a_parent_cancels_its_child() -> None:
    parent = CancellationToken()
    child = parent.child()

    parent.cancel()

    assert child.is_cancelled


def test_cancelling_a_child_spares_the_parent() -> None:
    """One document failing does not end the batch."""
    parent = CancellationToken()
    child = parent.child()

    child.cancel()

    assert not parent.is_cancelled


def test_a_parent_cancel_reaches_the_childs_callbacks() -> None:
    fired: list[str] = []
    parent = CancellationToken()
    child = parent.child()
    child.on_cancel(lambda: fired.append("kill"))

    parent.cancel()
    assert child.is_cancelled

    assert fired == ["kill"]


def test_a_child_cannot_outlive_its_parents_deadline() -> None:
    """Deadlines accumulate down the chain rather than replacing each other."""
    parent = CancellationToken(timeout=BRIEF)
    child = parent.child(timeout=GENEROUS)

    remaining = child.remaining_seconds()
    assert remaining is not None
    assert remaining <= BRIEF

    wait_past(BRIEF)
    assert child.is_cancelled


def test_a_tighter_child_deadline_does_not_shorten_the_parent() -> None:
    parent = CancellationToken(timeout=GENEROUS)
    child = parent.child(timeout=BRIEF)

    wait_past(BRIEF)

    assert child.is_cancelled
    assert not parent.is_cancelled


# ---------------------------------------------------------------------------
# NEVER_CANCELLED
# ---------------------------------------------------------------------------


def test_the_shared_token_cannot_be_cancelled_by_one_caller() -> None:
    NEVER_CANCELLED.cancel()

    assert not NEVER_CANCELLED.is_cancelled
    NEVER_CANCELLED.raise_if_cancelled()


def test_the_shared_token_does_not_accumulate_callbacks() -> None:
    """It outlives every caller, so a stored callback leaks for the process."""
    for _ in range(1000):
        NEVER_CANCELLED.on_cancel(lambda: None)

    assert NEVER_CANCELLED._callbacks == []  # noqa: SLF001 — the leak is the point


def test_a_child_of_the_shared_token_is_a_normal_token() -> None:
    child = NEVER_CANCELLED.child(timeout=BRIEF)
    assert not child.is_cancelled

    wait_past(BRIEF)

    assert child.is_cancelled, "an uncancellable parent must not disable its child's deadline"


# ---------------------------------------------------------------------------
# Threads — the case this design exists for
# ---------------------------------------------------------------------------


def test_workers_observe_a_cancellation_from_another_thread() -> None:
    token = CancellationToken()
    observed: list[bool] = []
    started = threading.Barrier(5, timeout=10)

    def worker() -> None:
        started.wait()
        for _ in range(4000):
            if token.is_cancelled:
                observed.append(True)
                return
            time.sleep(0.001)
        observed.append(False)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()

    started.wait()
    token.cancel()

    for thread in threads:
        thread.join(timeout=10)

    assert observed == [True] * 4
    assert not any(thread.is_alive() for thread in threads)


def test_concurrent_cancels_fire_the_callbacks_exactly_once() -> None:
    """Sixteen threads racing on cancel() must not produce sixteen kill signals."""
    token = CancellationToken()
    fired: list[int] = []
    token.on_cancel(lambda: fired.append(1))

    racers = [threading.Thread(target=token.cancel) for _ in range(16)]
    for thread in racers:
        thread.start()
    for thread in racers:
        thread.join(timeout=10)

    assert fired == [1]
