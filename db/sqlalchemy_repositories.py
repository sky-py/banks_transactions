from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import OutboxMessageORM, TransactionORM
from db.repositories import OutboxRepository, TransactionRepository
from domain.models import OutboxEntry, Transaction


def _transaction_from_orm(transaction: TransactionORM) -> Transaction:
    return Transaction(
        unique_id=transaction.unique_id,
        external_id=transaction.external_id,
        source_name=transaction.source_name,
        iban=transaction.iban,
        occurred_at=transaction.occurred_at,
        amount=transaction.amount,
        currency=transaction.currency,
        counterparty=transaction.counterparty,
        description=transaction.description,
        bank_balance=transaction.bank_balance,
        raw_data=transaction.raw_data,
    )


class SqlAlchemyTransactionRepository(TransactionRepository):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_existing_unique_ids(self, unique_ids: list[str]) -> set[str]:
        if not unique_ids:
            return set()

        statement = select(TransactionORM.unique_id).where(
            TransactionORM.unique_id.in_(unique_ids)
        )
        result = await self.session.execute(statement)
        return set(result.scalars().all())

    async def add(self, transaction: Transaction) -> None:
        self.session.add(
            TransactionORM(
                unique_id=transaction.unique_id,
                external_id=transaction.external_id,
                source_name=transaction.source_name,
                iban=transaction.iban,
                occurred_at=transaction.occurred_at,
                amount=transaction.amount,
                currency=transaction.currency,
                counterparty=transaction.counterparty,
                description=transaction.description,
                bank_balance=transaction.bank_balance,
                raw_data=transaction.raw_data,
            )
        )

    async def add_many(self, transactions: list[Transaction]) -> None:
        for transaction in transactions:
            await self.add(transaction)


class SqlAlchemyOutboxRepository(OutboxRepository):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_many(self, transactions: list[Transaction]) -> None:
        if not transactions:
            return

        await self.session.flush()
        unique_ids = [transaction.unique_id for transaction in transactions]
        statement = select(TransactionORM.id, TransactionORM.unique_id).where(
            TransactionORM.unique_id.in_(unique_ids)
        )
        result = await self.session.execute(statement)
        transaction_ids = {unique_id: transaction_id for transaction_id, unique_id in result.all()}

        for transaction in transactions:
            transaction_id = transaction_ids.get(transaction.unique_id)
            if transaction_id is None:
                raise ValueError(f'Transaction {transaction.unique_id} was not added before outbox message')

            self.session.add(
                OutboxMessageORM(
                    transaction_id=transaction_id,
                    status='pending',
                    attempts=0,
                )
            )

    async def get_pending(self) -> list[OutboxEntry]:
        statement = (
            select(OutboxMessageORM, TransactionORM)
            .join(TransactionORM, OutboxMessageORM.transaction_id == TransactionORM.id)
            .where(OutboxMessageORM.status == 'pending')
            .order_by(OutboxMessageORM.created_at.asc(), OutboxMessageORM.id.asc())
        )
        result = await self.session.execute(statement)
        return [
            OutboxEntry(
                id=message.id,
                transaction=_transaction_from_orm(transaction),
                created_at=message.created_at,
                attempts=message.attempts,
            )
            for message, transaction in result.all()
        ]

    async def mark_processed(self, message_ids: list[int]) -> None:
        if not message_ids:
            return
        statement = (
            update(OutboxMessageORM)
            .where(OutboxMessageORM.id.in_(message_ids))
            .values(status='processed', processed_at=datetime.now(), error_message=None)
        )
        await self.session.execute(statement)

    async def mark_failed(self, message_id: int, error_message: str) -> None:
        statement = (
            update(OutboxMessageORM)
            .where(OutboxMessageORM.id == message_id)
            .values(
                status='failed',
                attempts=OutboxMessageORM.attempts + 1,
                error_message=error_message,
            )
        )
        await self.session.execute(statement)
