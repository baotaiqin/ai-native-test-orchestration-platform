import pytest

from runner.errors import RunnerError
from runner.slots import SlotState


def test_slot_state_idle_busy_idle_and_idempotent_release() -> None:
    changes = 0

    def changed() -> None:
        nonlocal changes
        changes += 1

    state = SlotState(
        {"API": 1, "WEB": 2, "PERFORMANCE": 0},
        on_change=changed,
    )
    assert state.available() == {"API": 1, "WEB": 2, "PERFORMANCE": 0}

    lease = state.try_acquire("api")
    assert lease is not None
    assert state.available() == {"API": 0, "WEB": 2, "PERFORMANCE": 0}
    assert state.try_acquire("API") is None

    lease.release()
    lease.release()
    assert state.available() == {"API": 1, "WEB": 2, "PERFORMANCE": 0}
    assert changes == 2


def test_slot_state_rejects_invalid_totals_and_types() -> None:
    with pytest.raises(RunnerError):
        SlotState({"API": -1})
    with pytest.raises(RunnerError):
        SlotState({"API": 1},).try_acquire("SQL")
