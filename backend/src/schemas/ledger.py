from pydantic import BaseModel, Field


class LedgerEventInput(BaseModel):
    bill_no: str = Field(min_length=1, max_length=40)
    version: int = Field(ge=1, le=100000)
    kind: str = Field(min_length=1, max_length=20)
    amount: float = 0
    occurred_at: str = Field(default="", max_length=64)
    memo: str = Field(default="", max_length=200)


class LedgerImportInput(BaseModel):
    batch_key: str = Field(default="", max_length=120)
    source: str = Field(default="", max_length=200)
    events: list[LedgerEventInput] = Field(min_length=1, max_length=2000)
