import threading

import pytest
from starlette.testclient import TestClient

from src.config.database import SessionLocal
from src.constants.ledger import BillStatus, ImportStatus
from src.models import LedgerBill, LedgerEvent, LedgerImport
from src.services.ledger_service import (
    apply_import,
    normalise_events,
    rebuild_all_bills,
    reserve_import,
    resume_imports,
)
from tests.conftest import token


def ev(bill, version, kind, amount, memo=""):
    return {
        "bill_no": bill, "version": version, "kind": kind,
        "amount": amount, "occurred_at": "2026-10-01", "memo": memo,
    }


def post(client, auth, events, batch_key=""):
    return client.post("/ledger/imports", headers=auth,
                       json={"batch_key": batch_key, "source": "测试账本", "events": events})


def reconciliation(client, auth):
    response = client.get("/ledger/reconciliation", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


def bill(client, auth, bill_no):
    response = client.get("/ledger/bills", headers=auth)
    assert response.status_code == 200, response.text
    for item in response.json():
        if item["bill_no"] == bill_no:
            return item
    return None


def test_import_matches_source_and_is_idempotent(client, auth):
    events = [ev("HC-000001", 1, "original", 99.0)]
    first = post(client, auth, events, "batch-1")
    assert first.status_code == 202, first.text
    assert first.json()["status"] == "completed"
    assert first.json()["inserted_count"] == 1

    second = post(client, auth, events, "batch-1")
    assert second.status_code == 202
    assert second.json()["replayed"] is True
    assert second.json()["inserted_count"] == 1  # persisted result, not re-applied

    third = post(client, auth, events, "batch-2")  # new batch, same content
    assert third.status_code == 202
    assert third.json()["duplicate_count"] == 1

    with SessionLocal() as db:
        assert db.query(LedgerEvent).count() == 1
        assert db.query(LedgerBill).one().amount_cents == 9900
        assert db.query(LedgerBill).one().status == BillStatus.OPEN

    report = reconciliation(client, auth)
    item = next(i for i in report["matched_sample"] if i["bill_no"] == "HC-000001")
    assert item["status"] == "matched"
    assert item["ledger_amount"] == 99.0


def test_out_of_order_then_gap_filled(client, auth):
    later = post(client, auth, [ev("HC-000002", 3, "adjustment", -1.0)], "later")
    assert later.status_code == 202
    assert later.json()["pending_count"] == 1

    item = bill(client, auth, "HC-000002")
    assert item["status"] == "pending"
    assert item["applied_version"] == 0
    assert item["pending_versions"] == 1

    early = post(client, auth, [ev("HC-000002", 1, "original", 100.0),
                                ev("HC-000002", 2, "adjustment", 2.5)], "early")
    assert early.status_code == 202
    assert early.json()["pending_count"] == 0
    assert early.json()["applied_advance"] == 1

    item = next(i for i in reconciliation(client, auth)["amount_differences"] if i["bill_no"] == "HC-000002")
    assert item["ledger_amount"] == 101.5
    assert item["applied_version"] == 3

    # Replaying both batches changes nothing: same amounts, same progress.
    replay_later = post(client, auth, [ev("HC-000002", 3, "adjustment", -1.0)], "later").json()
    replay_early = post(client, auth, [ev("HC-000002", 1, "original", 100.0),
                                       ev("HC-000002", 2, "adjustment", 2.5)], "early").json()
    assert replay_later["replayed"] is True and replay_early["replayed"] is True
    # The stored receipts are returned verbatim; nothing was re-applied.
    assert replay_later["inserted_count"] == 1
    assert replay_early["inserted_count"] == 2
    assert replay_later["replayed"] and replay_early["replayed"]
    with SessionLocal() as db:
        assert db.query(LedgerEvent).filter_by(bill_no="HC-000002").count() == 3
        stored = db.query(LedgerBill).filter_by(bill_no="HC-000002").one()
        assert (stored.amount_cents, stored.applied_version) == (10150, 3)
    item = next(i for i in reconciliation(client, auth)["amount_differences"] if i["bill_no"] == "HC-000002")
    assert item["ledger_amount"] == 101.5


def test_same_version_different_content_rejects_entire_batch(client, auth):
    post(client, auth, [ev("HC-000003", 1, "original", 101.0)], "base")
    rejected = post(client, auth, [
        ev("HC-000004", 1, "original", 500.0),
        ev("HC-000003", 2, "adjustment", 1.0),
        ev("HC-000003", 2, "adjustment", 2.0),
    ], "conflict-in-batch")
    assert rejected.status_code == 422
    assert "不同内容" in rejected.json()["detail"]
    with SessionLocal() as db:
        assert db.query(LedgerBill).filter_by(bill_no="HC-000004").count() == 0

    cross = post(client, auth, [ev("HC-000003", 2, "adjustment", 3.0)], "conflict-vs-stored")
    assert cross.status_code == 202  # rejected batch wrote nothing, so this is fresh
    with SessionLocal() as db:
        assert db.query(LedgerBill).filter_by(bill_no="HC-000003").one().amount_cents == 10400
    # Same batch key, different payload -> the reservation blocks the replay.
    assert post(client, auth, [ev("HC-000003", 2, "adjustment", 4.0)], "conflict-vs-stored").status_code == 409
    # A new batch carrying a conflicting version is rejected against stored events.
    conflict = post(client, auth, [ev("HC-000003", 2, "adjustment", 5.0)], "conflict-new-key")
    assert conflict.status_code == 422
    with SessionLocal() as db:
        assert db.query(LedgerBill).filter_by(bill_no="HC-000003").one().amount_cents == 10400


def test_reversal_then_stale_event_does_not_revive(client, auth):
    assert post(client, auth, [ev("HC-000005", 1, "original", 103.0)], "a").status_code == 202
    assert post(client, auth, [ev("HC-000005", 2, "reversal", 0)], "b").status_code == 202
    stale = post(client, auth, [ev("HC-000005", 2, "reversal", 0), ev("HC-000005", 3, "adjustment", 9.0)], "c")
    assert stale.status_code == 202
    with SessionLocal() as db:
        stored = db.query(LedgerBill).filter_by(bill_no="HC-000005").one()
        assert stored.status == BillStatus.CANCELLED
        assert stored.amount_cents == 0
        assert stored.applied_version == 2
    cancelled = reconciliation(client, auth)["cancelled"]
    assert [i["bill_no"] for i in cancelled] == ["HC-000005"]


def test_batch_key_with_different_payload_rejected(client, auth):
    assert post(client, auth, [ev("HC-000001", 1, "original", 99.0)], "same-key").status_code == 202
    response = post(client, auth, [ev("HC-000001", 1, "original", 123.0)], "same-key")
    assert response.status_code == 409


def test_reconciliation_lists_pending_missing_and_differences(client, auth):
    post(client, auth, [
        ev("HC-000001", 1, "original", 99.0),       # matched
        ev("HC-000002", 1, "original", 100.0),      # matched so far
        ev("HC-000002", 2, "adjustment", 2.5),      # -> amount difference
        ev("HC-099999", 1, "original", 10.0),       # source missing
    ], "mixed")
    post(client, auth, [ev("HC-000003", 2, "adjustment", 1.0)], "gap")  # pending
    report = reconciliation(client, auth)
    summary = report["summary"]
    assert summary["amount_difference"] == 1
    assert summary["pending"] == 1
    assert summary["source_missing"] == 1
    assert summary["ledger_missing"] == 3  # HC-000004..6 have no ledger events
    assert [i["bill_no"] for i in report["amount_differences"]] == ["HC-000002"]
    assert [i["bill_no"] for i in report["pending"]] == ["HC-000003"]
    assert [i["bill_no"] for i in report["source_missing"]] == ["HC-099999"]
    assert "HC-000004" in [i["bill_no"] for i in report["ledger_missing"]]


def test_historical_report_snapshot_not_rewritten(client, auth):
    from src.models import ReportRun
    with SessionLocal() as db:
        run = ReportRun(name="历史快照", dimensions=["channel", "month"], status="completed",
                        result={"total": {"canonical_net": 1.0}, "checksum": "frozen"},
                        owner="宋予")
        db.add(run)
        db.commit()
        run_id = run.id
    post(client, auth, [ev("HC-000001", 1, "original", 1.0)], "history")
    with SessionLocal() as db:
        from src.models import ReportRun
        run = db.get(ReportRun, run_id)
        assert run.result == {"total": {"canonical_net": 1.0}, "checksum": "frozen"}


def test_interrupted_import_is_resumed(client, auth):
    payload, _ = normalise_events([
        ev("HC-000004", 1, "original", 102.0),
        ev("HC-000004", 2, "adjustment", -2.0),
    ])
    with SessionLocal() as db:
        row, _ = reserve_import(db, "interrupted", payload, "analyst@hengchi.demo", "中断测试")
        db.commit()
        import_id = row.id
    # Simulate a crash between reservation and apply.
    with SessionLocal() as db:
        assert db.get(LedgerImport, import_id).status == ImportStatus.PROCESSING
        assert db.query(LedgerBill).filter_by(bill_no="HC-000004").count() == 0
    resume_imports()
    with SessionLocal() as db:
        row = db.get(LedgerImport, import_id)
        assert row.status == ImportStatus.COMPLETED
        stored = db.query(LedgerBill).filter_by(bill_no="HC-000004").one()
        assert stored.amount_cents == 10000
        assert stored.applied_version == 2


def test_concurrent_adjacent_batches_converge(client, auth):
    """Adjacent versions imported by two threads: both land, amount is exact."""
    from src.main import app

    post(client, auth, [ev("HC-000006", 1, "original", 104.0)], "concurrent-base")
    payload = [
        (2, 1.0, "concurrent-v2"),
        (3, -0.25, "concurrent-v3"),
    ]
    results = {}

    def send(version, amount, batch_key):
        with TestClient(app) as c:
            headers = {"Authorization": "Bearer " + token(c)}
            results[version] = post(c, headers, [ev("HC-000006", version, "adjustment", amount)], batch_key)

    threads = [threading.Thread(target=send, args=item) for item in payload]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert all(r.status_code == 202 for r in results.values()), {v: r.text for v, r in results.items()}
    report = reconciliation(client, auth)
    item = next(i for i in report["amount_differences"] if i["bill_no"] == "HC-000006")
    assert item["ledger_amount"] == 104.75
    assert item["applied_version"] == 3
    with SessionLocal() as db:
        assert rebuild_all_bills(db) >= 1
        db.commit()
        stored = db.query(LedgerBill).filter_by(bill_no="HC-000006").one()
        assert stored.amount_cents == 10475
        assert stored.applied_version == 3


def test_repeated_interruption_keeps_amount_and_progress_consistent(client, auth):
    """Reserve, crash, replay twice: identical amount and progress each time."""
    events = [ev("HC-000001", 1, "original", 99.0), ev("HC-000001", 2, "adjustment", 1.0),
              ev("HC-000001", 3, "adjustment", -0.5)]
    from src.services.ledger_service import resume_imports as resume
    payload, _ = normalise_events(events)
    with SessionLocal() as db:
        row, _ = reserve_import(db, "crash-twice", payload, "analyst@hengchi.demo", "中断")
        db.commit()
        import_id = row.id
    snapshots = []
    for _ in range(2):
        with SessionLocal() as db:
            row = db.get(LedgerImport, import_id)
            row.status = ImportStatus.PROCESSING  # pretend the previous run died
            db.commit()
        resume()
        with SessionLocal() as db:
            stored = db.query(LedgerBill).filter_by(bill_no="HC-000001").one()
            receipt = db.get(LedgerImport, import_id)
            snapshots.append((stored.amount_cents, stored.applied_version, receipt.status))
            assert db.query(LedgerEvent).filter_by(bill_no="HC-000001").count() == 3
    assert snapshots == [(9950, 3, ImportStatus.COMPLETED)] * 2


def test_viewer_cannot_import(client, auth):
    viewer = {"Authorization": "Bearer " + token(client, "viewer@hengchi.demo")}
    response = post(client, viewer, [ev("HC-000001", 1, "original", 99.0)], "viewer")
    assert response.status_code == 403
    assert client.get("/ledger/reconciliation", headers=viewer).status_code == 200
