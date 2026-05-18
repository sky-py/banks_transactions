from abc import ABC, abstractmethod
from datetime import datetime
from domain.models import Transaction


class BankAdapter(ABC):
    @abstractmethod
    async def fetch_transactions(self, start_date: datetime, end_date: datetime) -> list[Transaction]:
        raise NotImplementedError

    @abstractmethod
    async def fetch_balance(self, at_datetime: datetime | None = None) -> int | None:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None
