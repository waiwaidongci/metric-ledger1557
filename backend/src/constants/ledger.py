from enum import StrEnum


class EventKind(StrEnum):
    ORIGINAL = "original"      # 原单
    ADJUSTMENT = "adjustment"  # 金额差额更正
    REVERSAL = "reversal"      # 撤销


class BillStatus(StrEnum):
    PENDING = "pending"        # 待补齐：后序先到，前序未到
    OPEN = "open"              # 已按连续版本记账
    CANCELLED = "cancelled"    # 已撤销，后续版本不得复活


class ImportStatus(StrEnum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    REJECTED = "rejected"


class CheckStatus(StrEnum):
    MATCHED = "matched"
    AMOUNT_DIFFERENCE = "amount_difference"
    SOURCE_MISSING = "source_missing"      # 账本有、结算来源缺
    LEDGER_MISSING = "ledger_missing"      # 结算来源有、账本缺
