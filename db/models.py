from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TransactionORM(Base):
    __tablename__ = 'transactions'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    unique_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    external_id: Mapped[str] = mapped_column(String(255), index=True)
    source_name: Mapped[str] = mapped_column(String(255), index=True)
    iban: Mapped[str] = mapped_column(String(34), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), index=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(10))
    counterparty: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    bank_balance: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    raw_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
    )


class OutboxMessageORM(Base):
    __tablename__ = 'outbox'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey('transactions.id'),
        unique=True,
        index=True,
    )
    transaction: Mapped[TransactionORM] = relationship()
    status: Mapped[str] = mapped_column(String(50), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
