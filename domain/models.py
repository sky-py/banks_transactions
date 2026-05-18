from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Transaction:
    unique_id: str
    external_id: str
    source_name: str
    iban: str
    occurred_at: datetime
    amount: int
    currency: str
    counterparty: str
    description: str
    bank_balance: int | None = None
    raw_data: dict[str, Any] | None = None


@dataclass(slots=True)
class OutboxEntry:
    id: int
    transaction: Transaction
    created_at: datetime
    attempts: int
