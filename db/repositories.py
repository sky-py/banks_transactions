from abc import ABC, abstractmethod

from domain.models import OutboxEntry, Transaction


class TransactionRepository(ABC):
    @abstractmethod
    async def get_existing_unique_ids(self, unique_ids: list[str]) -> set[str]:
        raise NotImplementedError

    @abstractmethod
    async def add(self, transaction: Transaction) -> None:
        raise NotImplementedError

    @abstractmethod
    async def add_many(self, transactions: list[Transaction]) -> None:
        raise NotImplementedError


class OutboxRepository(ABC):
    @abstractmethod
    async def add_many(self, transactions: list[Transaction]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_pending(self) -> list[OutboxEntry]:
        raise NotImplementedError

    @abstractmethod
    async def mark_processed(self, message_ids: list[int]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def mark_failed(self, message_id: int, error_message: str) -> None:
        raise NotImplementedError
