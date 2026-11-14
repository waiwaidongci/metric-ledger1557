"""Independent ledger import and two-way reconciliation.

Import is split into two commits:

1. reserve  - persist the exact payload as LedgerImport(processing).  A client
              retry (same batch_key) or a process restart can resume this row.
2. apply    - insert events, project every affected bill from the full event
              log and update the bill cache in ONE transaction.  A crash
              therefore leaves either all events of the batch or none; the
              reservation is replayed on the next start.

Bills are projected by ``ledger_rules.replay`` instead of by incremental
mutation.  Out-of-order versions are stored but stay pending until their prefix
is complete; duplicates leave the amount untouched; a reversal closes the
timeline; and adjacent concurrent imports serialise on the bill rows so the
last projection always sees every committed event.
"""
import hashlib
import json
import time

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from ..config.database import SessionLocal
from ..constants.ledger import BillStatus, ImportStatus
from ..models import LedgerBill, LedgerEvent, LedgerImport, SourceRow
from ..utils.clock import now
from ..utils.ledger_rules import EventView, event_hash, replay, validate_event
from .audit_service import log

MAX_EVENTS = 2000
MAX_AMOUNT_CENTS = 100_000_000_00


def _normalise(raw):
    """Turn one request event into a validated EventView plus storage dict."""
    try:
        version = int(raw["version"])
        if "amount_cents" in raw and "amount" not in raw:
            amount = float(raw["amount_cents"]) / 100
        else:
            amount = float(raw.get("amount", 0))
    except (KeyError, TypeError, ValueError):
        raise ValueError("事件的 version 与 amount 必须是数值")
    if abs(amount) > MAX_AMOUNT_CENTS / 100:
        raise ValueError("金额超出允许范围")
    amount_cents = int(round(amount * 100))
    event = EventView(
        bill_no=str(raw.get("bill_no", "")).strip(),
        version=version,
        kind=str(raw.get("kind", "")),
        amount_cents=amount_cents,
    )
    validate_event(event)
    occurred_at = str(raw.get("occurred_at", "") or "")[:64]
    memo = str(raw.get("memo", "") or "")[:200]
    event.occurred_at = occurred_at
    event.memo = memo
    event.content_hash = event_hash(
        event.bill_no, event.version, event.kind, event.amount_cents,
        occurred_at, memo,
    )
    return event, {
        "bill_no": event.bill_no,
        "version": event.version,
        "kind": event.kind,
        "amount_cents": event.amount_cents,
        "occurred_at": occurred_at,
        "memo": memo,
    }


def _batch_digest(payload):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def normalise_events(raw_events):
    """Validate the whole payload and collapse identical in-batch repeats.

    A same-version / different-content pair anywhere rejects the entire batch
    before anything is written.
    """
    if not raw_events:
        raise ValueError("导入内容为空，至少包含一个账本事件")
    if len(raw_events) > MAX_EVENTS:
        raise ValueError(f"单批最多 {MAX_EVENTS} 个事件")
    normalised, seen, duplicates = [], {}, 0
    for raw in raw_events:
        if hasattr(raw, "model_dump"):
            raw = raw.model_dump()
        if not isinstance(raw, dict):
            raise ValueError("每个事件必须是 JSON 对象")
        event, payload = _normalise(raw)
        key = (event.bill_no, event.version)
        previous = seen.get(key)
        if previous is not None:
            if previous.content_hash != event.content_hash:
                raise ValueError(
                    f"账单 {event.bill_no} 第 {event.version} 版在本次导入中存在不同内容，拒绝整批导入"
                )
            duplicates += 1
            continue
        seen[key] = event
        normalised.append(payload)
    return normalised, duplicates


def reserve_import(db, batch_key, payload, actor, source):
    """Phase 1 commit.  Returns (import_row, was_already_reserved)."""
    digest = _batch_digest(payload)
    existing = db.query(LedgerImport).filter_by(batch_key=batch_key).first()
    if existing:
        if existing.content_hash != digest:
            raise HTTPException(409, "同一批次标识对应不同内容，拒绝整批导入")
        return existing, True
    row = LedgerImport(
        batch_key=batch_key,
        content_hash=digest,
        payload=payload,
        received_count=len(payload),
        actor=actor,
        source=source,
        status=ImportStatus.PROCESSING,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(LedgerImport).filter_by(batch_key=batch_key).first()
        if not existing or existing.content_hash != digest:
            raise HTTPException(409, "批次标识冲突，拒绝整批导入")
        return existing, True
    return row, False


def _bill_locks(db, bill_nos):
    """Serialise concurrent imports that touch the same bills.

    A no-op on SQLite (single writer); on PostgreSQL the transaction-scoped
    advisory lock is keyed by the bill number, so two adjacent batches cannot
    interleave their event insert and projection.  Keys are taken in sorted
    order to avoid deadlocks.
    """
    if db.get_bind().dialect.name != "postgresql":
        return
    for bill_no in sorted(bill_nos):
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:bill_no))"),
            {"bill_no": "ledger:" + bill_no},
        )


def _project(db, bill_nos):
    """Recompute every affected bill from the persisted event log."""
    events = (
        db.query(LedgerEvent)
        .filter(LedgerEvent.bill_no.in_(bill_nos))
        .order_by(LedgerEvent.bill_no, LedgerEvent.version, LedgerEvent.content_hash)
        .all()
    )
    grouped = {}
    for event in events:
        grouped.setdefault(event.bill_no, []).append(event)
    return {bill_no: replay(rows) for bill_no, rows in grouped.items()}


def _apply_once(import_id):
    """Phase 2 in one transaction.  ValueError rejects the whole batch.

    The payload and content hash come from the reserved row, so a resumed
    process applies exactly what was accepted, even if the original request
    is long gone.
    """
    with SessionLocal() as db:
        row = db.get(LedgerImport, import_id)
        if not row or row.status != ImportStatus.PROCESSING:
            return row
        payload = row.payload
        if row.content_hash != _batch_digest(payload):
            raise ValueError("预留批次内容与摘要不一致，拒绝整批导入")
        _bill_locks(db, [event["bill_no"] for event in payload])
        bill_nos = sorted({event["bill_no"] for event in payload})
        try:
            before = {
                bill.bill_no: bill.applied_version
                for bill in db.query(LedgerBill)
                .filter(LedgerBill.bill_no.in_(bill_nos))
                .all()
            }
            existing = {
                (e.bill_no, e.version): e
                for e in db.query(LedgerEvent).filter(LedgerEvent.bill_no.in_(bill_nos)).all()
            }
            duplicates = inserted = 0
            for event in payload:
                key = (event["bill_no"], event["version"])
                digest = event_hash(
                    event["bill_no"], event["version"], event["kind"],
                    event["amount_cents"], event["occurred_at"], event["memo"],
                )
                stored = existing.get(key)
                if stored is not None:
                    if stored.content_hash != digest:
                        raise ValueError(
                            f"账单 {event['bill_no']} 第 {event['version']} 版已存在不同内容，拒绝整批导入"
                        )
                    duplicates += 1
                    continue
                db.add(LedgerEvent(**event, content_hash=digest, batch_key=row.batch_key))
                inserted += 1
            db.flush()
            projection = _project(db, bill_nos)
            for bill_no, state in projection.items():
                bill = db.query(LedgerBill).filter_by(bill_no=bill_no).first()
                if bill is None:
                    bill = LedgerBill(bill_no=bill_no)
                    db.add(bill)
                bill.applied_version = state["applied_version"]
                bill.amount_cents = state["amount_cents"]
                bill.status = state["status"]
                bill.cancel_version = state["cancel_version"]
                bill.version_count = state["version_count"]
                bill.max_version = state["max_version"]
                bill.updated_at = now()
            db.flush()
            fresh = _project(db, bill_nos)
            advances = sum(
                1
                for bill_no, state in fresh.items()
                if state["applied_version"] > before.get(bill_no, 0)
            )
            row.duplicate_count = duplicates
            row.inserted_count = inserted
            row.pending_count = sum(
                1 for state in fresh.values() if state["status"] == BillStatus.PENDING
            )
            row.applied_advance = advances
            row.bills_affected = len(fresh)
            row.attempts = (row.attempts or 0) + 1
            row.status = ImportStatus.COMPLETED
            row.completed_at = now()
            row.summary = {
                "bills": [
                    {
                        "bill_no": bill_no,
                        "applied_version": state["applied_version"],
                        "amount": round(state["amount_cents"] / 100, 2),
                        "status": state["status"],
                        "pending_versions": state["pending_versions"],
                        "cancel_version": state["cancel_version"],
                    }
                    for bill_no, state in sorted(fresh.items())
                ],
                "pending_versions": sum(
                    state["pending_versions"] for state in fresh.values()
                ),
                "duplicate_versions_kept": duplicates,
            }
            db.commit()
            return row
        except IntegrityError as error:
            db.rollback()
            raise ConcurrentWrite(error) from error
        except ValueError:
            db.rollback()
            raise


def _mark_rejected(batch_key, reason):
    with SessionLocal() as db:
        row = db.query(LedgerImport).filter_by(batch_key=batch_key).first()
        if row and row.status == ImportStatus.PROCESSING:
            row.status = ImportStatus.REJECTED
            row.rejected_reason = reason[:400]
            row.attempts = (row.attempts or 0) + 1
            row.completed_at = now()
            db.commit()


class ConcurrentWrite(Exception):
    """Another transaction touched the same version first; retry the batch."""


def apply_import(import_id, retries=4):
    for attempt in range(retries):
        try:
            return _apply_once(import_id)
        except (OperationalError, ConcurrentWrite):
            if attempt == retries - 1:
                raise
            time.sleep(0.05 * (attempt + 1))


def import_events(db, data, user):
    try:
        payload, batch_duplicates = normalise_events(data.events)
    except ValueError as error:
        raise HTTPException(422, str(error))
    batch_key = (data.batch_key or "").strip() or _batch_digest(payload)
    reserve, replayed = reserve_import(db, batch_key, payload, user.email, data.source)
    log(db, user.email, "ledger.import.reserve", batch_key)
    db.commit()
    if reserve.status == ImportStatus.COMPLETED:
        return _import_view(reserve, replayed=replayed, batch_duplicates=batch_duplicates)
    if reserve.status == ImportStatus.REJECTED:
        raise HTTPException(422, reserve.rejected_reason or "该批次已被拒绝")
    try:
        apply_import(reserve.id)
    except ValueError as error:
        _mark_rejected(batch_key, str(error))
        raise HTTPException(422, str(error))
    except (OperationalError, ConcurrentWrite):
        raise HTTPException(503, "账本导入暂时被并发写入占用，请稍后重试")
    with SessionLocal() as fresh:
        done = fresh.query(LedgerImport).filter_by(batch_key=batch_key).first()
        log(fresh, user.email, "ledger.import.apply", batch_key)
        fresh.commit()
        return _import_view(done, replayed=replayed, batch_duplicates=batch_duplicates)


def resume_imports(db=None):
    """Replay reservations left processing by an interruption."""
    own = db is None
    if own:
        db = SessionLocal()
    try:
        rows = db.query(LedgerImport).filter_by(status=ImportStatus.PROCESSING).limit(50).all()
        for row in rows:
            try:
                apply_import(row.id)
            except ValueError as error:
                _mark_rejected(row.batch_key, str(error))
            except (OperationalError, ConcurrentWrite):
                continue
        if rows:
            log(db, "worker", "ledger.import.resume", f"{len(rows)} batch(es)")
            db.commit()
    finally:
        if own:
            db.close()


def _import_view(row, replayed=False, batch_duplicates=0):
    return {
        "batch_key": row.batch_key,
        "status": row.status,
        "content_hash": row.content_hash,
        "received_count": row.received_count,
        "batch_duplicates": batch_duplicates,
        "inserted_count": row.inserted_count,
        "duplicate_count": row.duplicate_count,
        "pending_count": row.pending_count,
        "applied_advance": row.applied_advance,
        "bills_affected": row.bills_affected,
        "attempts": row.attempts,
        "rejected_reason": row.rejected_reason,
        "replayed": replayed,
        "bills": (row.summary or {}).get("bills", []),
        "pending_versions": (row.summary or {}).get("pending_versions", 0),
        "created_at": row.created_at,
        "completed_at": row.completed_at,
    }


def list_imports(db):
    return [
        _import_view(row)
        for row in db.query(LedgerImport).order_by(LedgerImport.id.desc()).limit(30).all()
    ]


def _bill_item(bill_no, source, bill, extra_pending=0):
    source_cents = int(round(source.reference_net * 100)) if source else None
    ledger_cents = bill.amount_cents if bill else None
    difference = (
        ledger_cents - source_cents
        if source_cents is not None and ledger_cents is not None
        else None
    )
    pending_versions = (
        bill.version_count - bill.applied_version
        if bill and bill.status != BillStatus.CANCELLED
        else 0
    )
    if source is None:
        status = "source_missing"
    elif bill is None:
        status = "ledger_missing"
    elif bill.status == BillStatus.CANCELLED:
        status = "cancelled"
    elif bill.status == BillStatus.PENDING or pending_versions or extra_pending:
        status = "pending"
    elif difference:
        status = "amount_difference"
    else:
        status = "matched"
    return {
        "bill_no": bill_no,
        "status": status,
        "source_exists": source is not None,
        "ledger_exists": bill is not None,
        "source_reference": round(source_cents / 100, 2) if source_cents is not None else None,
        "ledger_amount": round(ledger_cents / 100, 2) if ledger_cents is not None else None,
        "difference": round(difference / 100, 2) if difference is not None else None,
        "applied_version": bill.applied_version if bill else 0,
        "version_count": bill.version_count if bill else 0,
        "pending_versions": extra_pending or pending_versions,
        "cancel_version": bill.cancel_version if bill else None,
        "channel": source.channel if source else None,
        "month": source.day[:7] if source else None,
        "region": source.region if source else None,
        "orders": source.orders if source else None,
    }


def ledger_reconciliation(db):
    """Two-way comparison between source settlement rows and ledger bills."""
    sources = {row.source_key: row for row in db.query(SourceRow).order_by(SourceRow.id).all()}
    bills = {bill.bill_no: bill for bill in db.query(LedgerBill).order_by(LedgerBill.id).all()}
    # Events whose bill cache has not caught up yet (e.g. read during a retry)
    # still count as pending versions rather than disappearing.
    event_counts = {}
    for event in db.query(LedgerEvent.bill_no).all():
        event_counts[event.bill_no] = event_counts.get(event.bill_no, 0) + 1
    items = []
    for bill_no in sorted(set(sources) | set(bills) | set(event_counts)):
        bill = bills.get(bill_no)
        extra = 0
        if bill is None:
            extra = event_counts.get(bill_no, 0)
        items.append(_bill_item(bill_no, sources.get(bill_no), bill, extra))

    def pick(status):
        rows = [item for item in items if item["status"] == status]
        if status in {"amount_difference", "ledger_missing"}:
            rows.sort(key=lambda item: abs(item["difference"] or 0), reverse=True)
        return rows

    ledger_total = sum(bill.amount_cents for bill in bills.values())
    source_total = sum(int(round(row.reference_net * 100)) for row in sources.values())
    pending = pick("pending")
    source_missing = pick("source_missing")
    ledger_missing = pick("ledger_missing")
    amount_differences = pick("amount_difference")
    cancelled = pick("cancelled")
    matched = sum(1 for item in items if item["status"] == "matched")
    return {
        "summary": {
            "bills": len(bills),
            "sources": len(sources),
            "matched": matched,
            "pending": len(pending),
            "source_missing": len(source_missing),
            "ledger_missing": len(ledger_missing),
            "amount_difference": len(amount_differences),
            "cancelled": len(cancelled),
            "ledger_total": round(ledger_total / 100, 2),
            "source_reference_total": round(source_total / 100, 2),
            "difference_total": round((ledger_total - source_total) / 100, 2),
            "pending_versions": sum(item["pending_versions"] for item in pick("pending")),
        },
        "pending": pending,
        "source_missing": source_missing,
        "ledger_missing": ledger_missing,
        "amount_differences": amount_differences,
        "cancelled": cancelled,
        "matched_sample": [item for item in items if item["status"] == "matched"][:5],
        "rule": "账单编号对应来源编号，双向核对。账本有而来源无 = 来源缺单；来源有而账本无 = 账本缺单（待补齐）。金额以参考账本净额逐分比较，撤销单单列。核对结果不修改任何历史报表与交付快照。",
    }


def rebuild_all_bills(db):
    """Re-project every bill from the complete event log.

    Used by the demo seed and available to verify that the cached progress is
    only a projection: recomputing must not change any amount.
    """
    events = db.query(LedgerEvent).order_by(LedgerEvent.bill_no, LedgerEvent.version).all()
    grouped = {}
    for event in events:
        grouped.setdefault(event.bill_no, []).append(event)
    known = {bill.bill_no: bill for bill in db.query(LedgerBill).all()}
    for bill_no, rows in grouped.items():
        state = replay(rows)
        bill = known.get(bill_no) or LedgerBill(bill_no=bill_no)
        if bill_no not in known:
            db.add(bill)
        bill.applied_version = state["applied_version"]
        bill.amount_cents = state["amount_cents"]
        bill.status = state["status"]
        bill.cancel_version = state["cancel_version"]
        bill.version_count = state["version_count"]
        bill.max_version = state["max_version"]
        bill.updated_at = now()
    return len(grouped)


def ledger_bills(db, status=None):
    query = db.query(LedgerBill).order_by(LedgerBill.id)
    if status:
        query = query.filter(LedgerBill.status == status)
    return [
        {
            "bill_no": bill.bill_no,
            "status": bill.status,
            "amount": round(bill.amount_cents / 100, 2),
            "applied_version": bill.applied_version,
            "version_count": bill.version_count,
            "pending_versions": (
                bill.version_count - bill.applied_version
                if bill.status != BillStatus.CANCELLED
                else 0
            ),
            "cancel_version": bill.cancel_version,
            "updated_at": bill.updated_at,
        }
        for bill in query.limit(500).all()
    ]
