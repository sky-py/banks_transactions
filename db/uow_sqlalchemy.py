
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.sqlalchemy_repositories import (
    SqlAlchemyOutboxRepository,
    SqlAlchemyTransactionRepository,
)
from db.uow import UnitOfWork


class SqlAlchemyUnitOfWork(UnitOfWork):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory
        self.session: AsyncSession | None = None

    async def __aenter__(self) -> 'SqlAlchemyUnitOfWork':
        self.session = self.session_factory()
        self.transactions = SqlAlchemyTransactionRepository(self.session)
        self.outbox = SqlAlchemyOutboxRepository(self.session)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self.session is None:
            return

        if exc_type is not None:
            await self.rollback()
        else:
            await self.commit()
        await self.session.close()
        self.session = None

    async def commit(self) -> None:
        if self.session is None:
            return
        await self.session.commit()

    async def rollback(self) -> None:
        if self.session is None:
            return
        await self.session.rollback()
