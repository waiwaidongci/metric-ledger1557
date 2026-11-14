from sqlalchemy import JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..config.database import Base
from ..utils.clock import now


class LedgerEvent(Base):
    """An immutable event from the independent ledger.

    Bill numbers always correspond to source_key.  Versions start at 1 and are
    gapless once the prefix has been applied.  The unique constraint makes
    (bill_no, version) the idempotency key; a second payload for the same
    version is rejected as a whole-batch conflict.
    """

    __tablename__ = "ledger_events"
    __table_args__ = (
        UniqueConstraint("bill_no", "version", name="ledger_event_version_uq"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    bill_no: Mapped[str] = mapped_column(index=True)
    version: Mapped[int]
    kind: Mapped[str]
    amount_cents: Mapped[int] = mapped_column(default=0)
    occurred_at: Mapped[str] = mapped_column(default="")
    memo: Mapped[str] = mapped_column(default="")
    content_hash: Mapped[str]
    batch_key: Mapped[str] = mapped_column(index=True)
    created_at: Mapped[str] = mapped_column(default=now)


class LedgerBill(Base):
    """Derived, transactionally updated progress for one ledger bill.

    Every column is a pure projection of LedgerEvent rows, so the bill can
    always be recomputed from the event log after a crash or a concurrent
    import.
    """

    __tablename__ = "ledger_bills"

    id: Mapped[int] = mapped_column(primary_key=True)
    bill_no: Mapped[str] = mapped_column(unique=True, index=True)
    applied_version: Mapped[int] = mapped_column(default=0)
    amount_cents: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(default="pending")
    cancel_version: Mapped[int | None] = mapped_column(nullable=True)
    version_count: Mapped[int] = mapped_column(default=0)
    max_version: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[str] = mapped_column(default=now)


class LedgerImport(Base):
    """Import batch reservation, payload and result.

    The reservation is committed before applying events so a process
    interruption can resume the exact payload.  Event insertion plus bill
    projection happen in one transaction; a crash leaves either the full batch
    or nothing, and the reserved payload is replayed on restart.
    """

    __tablename__ = "ledger_imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_key: Mapped[str] = mapped_column(unique=True, index=True)
    status: Mapped[str] = mapped_column(default="processing")
    content_hash: Mapped[str]
    payload: Mapped[list] = mapped_column(JSON, default=list)
    received_count: Mapped[int] = mapped_column(default=0)
    duplicate_count: Mapped[int] = mapped_column(default=0)
    inserted_count: Mapped[int] = mapped_column(default=0)
    pending_count: Mapped[int] = mapped_column(default=0)
    applied_advance: Mapped[int] = mapped_column(default=0)
    bills_affected: Mapped[int] = mapped_column(default=0)
    attempts: Mapped[int] = mapped_column(default=0)
    rejected_reason: Mapped[str] = mapped_column(default="")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    actor: Mapped[str]
    source: Mapped[str] = mapped_column(default="")
    created_at: Mapped[str] = mapped_column(default=now)
    completed_at: Mapped[str] = mapped_column(default="")
