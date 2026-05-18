from abc import ABC, abstractmethod

from domain.models import Transaction


class Exporter(ABC):
    @abstractmethod
    async def export_transactions(self, transactions: list[Transaction]) -> None:
        raise NotImplementedError

