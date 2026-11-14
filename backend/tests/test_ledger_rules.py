import pytest

from src.constants.ledger import BillStatus
from src.utils.ledger_rules import EventView, event_hash, replay


def event(bill, version, kind, cents, memo=""):
    return EventView(
        bill, version, kind, cents,
        event_hash(bill, version, kind, cents, "", memo),
    )


def test_out_of_order_waits_then_applies_in_order():
    v3 = event("A", 3, "adjustment", -100)
    v1 = event("A", 1, "original", 10_000)
    v2 = event("A", 2, "adjustment", 250)
    state = replay([v3])
    assert state["status"] == BillStatus.PENDING
    state = replay([v3, v1])
    assert state["amount_cents"] == 10_000
    assert state["applied_version"] == 1
    assert state["pending_versions"] == 1
    state = replay([v3, v1, v2])
    assert state["amount_cents"] == 10_150
    assert state["applied_version"] == 3


def test_reversal_closes_timeline_and_old_events_do_not_revive():
    v1 = event("A", 1, "original", 5_000)
    v2 = event("A", 2, "reversal", 0)
    state = replay([v1, v2])
    assert state["amount_cents"] == 0
    assert state["status"] == BillStatus.CANCELLED
    # A delayed later version is stored but never applied.
    late = event("A", 3, "adjustment", 700)
    state = replay([v1, v2, late])
    assert state["amount_cents"] == 0
    assert state["applied_version"] == 2
    # A delayed duplicate of an already-applied version is a duplicate.
    state = replay([v1, v2, v1])
    assert state["amount_cents"] == 0
    assert state["duplicates"] == 1


def test_same_version_same_content_is_idempotent():
    v1 = event("A", 1, "original", 1_000)
    v2 = event("A", 2, "adjustment", 100)
    once = replay([v1, v2])
    twice = replay([v1, v2, event("A", 2, "adjustment", 100)])
    assert once["amount_cents"] == twice["amount_cents"] == 1_100
    assert twice["duplicates"] == 1


def test_same_version_different_content_rejects():
    with pytest.raises(ValueError):
        replay([
            event("A", 1, "original", 1_000),
            event("A", 2, "adjustment", 100),
            event("A", 2, "adjustment", 200),
        ])


def test_original_must_be_v1_and_reversal_has_no_amount():
    with pytest.raises(ValueError):
        replay([event("A", 1, "adjustment", 100)])
    with pytest.raises(ValueError):
        replay([event("A", 1, "original", 100), event("A", 2, "original", 100)])
    with pytest.raises(ValueError):
        replay([event("A", 1, "original", 100), event("A", 2, "reversal", 50)])


def test_pending_versions_do_not_count_when_a_cancel_is_already_final():
    v1 = event("A", 1, "original", 1_000)
    v2 = event("A", 2, "reversal", 0)
    v4 = event("A", 4, "adjustment", 10)
    state = replay([v1, v2, v4])
    assert state["status"] == BillStatus.CANCELLED
    assert state["applied_version"] == 2
    assert state["pending_versions"] == 0


def test_projection_ignores_insertion_order():
    events = [
        event("A", 1, "original", 2_000),
        event("A", 2, "adjustment", 250),
        event("A", 3, "adjustment", -50),
    ]
    for ordering in (events, events[::-1], [events[1], events[2], events[0]]):
        assert replay(ordering)["amount_cents"] == 2_200
