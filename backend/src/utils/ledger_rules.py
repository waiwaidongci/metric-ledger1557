"""Pure ordering / amount rules for the independent ledger.

Amounts are integers in cents so difference corrections, reversals and
idempotent replays never drift.  One bill's timeline is

    v1 ORIGINAL (sets the amount)
    v2..n ADJUSTMENT (adds the signed difference)
    optional REVERSAL (nets the balance to zero and cancels the bill)

The cancel version is final: any event whose version is not below the
cancellation is stored for audit but never applied again, so a delayed old
event cannot revive a cancelled bill.

``replay`` is the single source of truth.  The LedgerBill row is only a cache
of that projection, and every import re-projects from the full event log, so a
replayed batch, a concurrent adjacent batch and a fresh process all converge on
the same amount and progress.
"""
import hashlib
import json

from ..constants.ledger import BillStatus, EventKind


def event_hash(bill_no, version, kind, amount_cents, occurred_at, memo):
    payload = json.dumps(
        [str(bill_no), int(version), str(kind), int(amount_cents), occurred_at, memo],
        ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class EventView:
    """Minimal attribute view shared by ORM rows and normalised payloads."""

    def __init__(self, bill_no, version, kind, amount_cents, content_hash=""):
        self.bill_no = bill_no
        self.version = version
        self.kind = kind
        self.amount_cents = amount_cents
        self.content_hash = content_hash


def validate_event(event):
    """Structural rules that do not depend on what has already been applied."""
    if not str(event.bill_no).strip():
        raise ValueError("账单编号不能为空")
    if event.version < 1:
        raise ValueError("版本号必须从 1 开始连续递增")
    if event.kind == EventKind.ORIGINAL:
        if event.version != 1:
            raise ValueError("原单必须是第 1 版")
        if event.amount_cents < 0:
            raise ValueError("原单金额不能为负")
    elif event.kind == EventKind.ADJUSTMENT:
        if event.version == 1:
            raise ValueError("第 1 版必须是原单")
        if event.amount_cents == 0:
            raise ValueError("金额差额更正不能为 0")
    elif event.kind == EventKind.REVERSAL:
        if event.version == 1:
            raise ValueError("第 1 版必须是原单")
        if event.amount_cents != 0:
            raise ValueError("撤销事件不携带金额")
    else:
        raise ValueError("未知事件类型")


def replay(events):
    """Project one bill's full event log into its authoritative state.

    Events may arrive out of order and may repeat.  Same version + identical
    payload is a duplicate; same version + different payload raises ValueError
    so the caller can reject the entire incoming batch.
    """
    by_version = {}
    duplicates = 0
    for event in sorted(events, key=lambda e: (e.version, e.content_hash)):
        validate_event(event)
        previous = by_version.get(event.version)
        if previous is not None:
            if previous.content_hash == event.content_hash:
                duplicates += 1
                continue
            raise ValueError(
                f"账单 {event.bill_no} 第 {event.version} 版存在不同内容，拒绝整批导入"
            )
        by_version[event.version] = event

    applied_version = 0
    while applied_version + 1 in by_version:
        applied_version += 1

    amount_cents = 0
    cancel_version = None
    for version in range(1, applied_version + 1):
        event = by_version[version]
        if version == 1:
            if event.kind != EventKind.ORIGINAL:
                raise ValueError(f"账单 {event.bill_no} 第 1 版必须是原单")
            amount_cents = event.amount_cents
        elif event.kind == EventKind.ORIGINAL:
            raise ValueError(f"账单 {event.bill_no} 的原单只能出现在第 1 版")
        elif event.kind == EventKind.ADJUSTMENT:
            amount_cents += event.amount_cents
        elif event.kind == EventKind.REVERSAL:
            amount_cents = 0
            cancel_version = version
            # The reversal closes the timeline: nothing after it is applied.
            break

    # Reversal is final.  Versions recorded after it are audit-only; delayed
    # old versions (already <= applied) are never re-applied either.
    if cancel_version is not None and applied_version > cancel_version:
        applied_version = cancel_version

    status = (
        BillStatus.CANCELLED
        if cancel_version is not None
        else BillStatus.OPEN
        if applied_version > 0
        else BillStatus.PENDING
    )
    return {
        "applied_version": applied_version,
        "amount_cents": amount_cents,
        "cancel_version": cancel_version,
        "status": status,
        "duplicates": duplicates,
        "pending_versions": 0
        if cancel_version is not None
        else len(by_version) - applied_version,
        "max_version": max(by_version, default=0),
        "version_count": len(by_version),
    }
