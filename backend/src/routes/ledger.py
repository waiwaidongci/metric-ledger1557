from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config.database import get_db
from ..controllers.dependencies import current_user, roles
from ..constants.roles import Role
from ..schemas.ledger import LedgerImportInput
from ..services.ledger_service import (
    import_events,
    ledger_bills,
    ledger_reconciliation,
    list_imports,
)

router = APIRouter(prefix="/ledger", tags=["ledger"])


@router.get("/reconciliation")
def reconciliation(db: Session = Depends(get_db), user=Depends(current_user)):
    return ledger_reconciliation(db)


@router.get("/bills")
def bills(status: str = "", db: Session = Depends(get_db), user=Depends(current_user)):
    return ledger_bills(db, status or None)


@router.get("/imports")
def imports(db: Session = Depends(get_db), user=Depends(current_user)):
    return list_imports(db)


@router.post("/imports", status_code=202)
def create_import(
    data: LedgerImportInput,
    db: Session = Depends(get_db),
    user=Depends(roles(Role.ADMIN, Role.ANALYST)),
):
    return import_events(db, data, user)
