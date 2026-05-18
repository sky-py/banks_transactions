from abc import ABC, abstractmethod

from db.repositories import OutboxRepository, TransactionRepository


class UnitOfWork(ABC):
    transactions: TransactionRepository
    outbox: OutboxRepository

    @abstractmethod
    async def __aenter__(self) -> 'UnitOfWork':
        raise NotImplementedError

    @abstractmethod
    async def __aexit__(self, exc_type, exc, tb) -> None:
        raise NotImplementedError

    @abstractmethod
    async def commit(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def rollback(self) -> None:
        raise NotImplementedError
