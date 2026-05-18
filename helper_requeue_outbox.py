import argparse
import asyncio
from datetime import date, datetime, time
from config.settings import load_settings
from db.models import OutboxMessageORM, TransactionORM
from db.session import create_engine_and_session_factory
from sqlalchemy import insert, select, update


def _normalize_start_datetime(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, time.min)


def _normalize_end_datetime(value: date | datetime | None) -> datetime:
    if value is None:
        return datetime.now()
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, time.max)


def _parse_cli_date(value: str) -> date:
    return datetime.strptime(value, '%d.%m.%Y').date()


async def requeue_transactions_for_export(
    source_name: str, start_date: date | datetime, end_date: date | datetime | None = None
) -> int:
    settings = load_settings()
    engine, session_factory = create_engine_and_session_factory(settings.database.url)

    start_datetime = _normalize_start_datetime(start_date)
    end_datetime = _normalize_end_datetime(end_date)

    try:
        async with session_factory() as session:
            transaction_ids_result = await session.execute(
                select(TransactionORM.id).where(
                    TransactionORM.source_name == source_name,
                    TransactionORM.occurred_at >= start_datetime,
                    TransactionORM.occurred_at <= end_datetime,
                )
            )
            transaction_ids = list(transaction_ids_result.scalars().all())
            if not transaction_ids:
                return 0

            existing_outbox_result = await session.execute(
                select(OutboxMessageORM.transaction_id).where(OutboxMessageORM.transaction_id.in_(transaction_ids))
            )
            existing_transaction_ids = set(existing_outbox_result.scalars().all())
            missing_transaction_ids = [
                transaction_id for transaction_id in transaction_ids if transaction_id not in existing_transaction_ids
            ]

            await session.execute(
                update(OutboxMessageORM)
                .where(OutboxMessageORM.transaction_id.in_(transaction_ids))
                .values(status='pending', attempts=0, error_message=None, processed_at=None)
            )

            if missing_transaction_ids:
                await session.execute(
                    insert(OutboxMessageORM),
                    [
                        {'transaction_id': transaction_id, 'status': 'pending', 'attempts': 0}
                        for transaction_id in missing_transaction_ids
                    ],
                )

            await session.commit()
            return len(transaction_ids)
    finally:
        await engine.dispose()


if __name__ == '__main__':
    # parser = argparse.ArgumentParser(description='Put existing transactions back to pending outbox export.')
    # parser.add_argument('source_name')
    # parser.add_argument('start_date', help='dd.mm.yyyy')
    # parser.add_argument('end_date', nargs='?', help='dd.mm.yyyy, defaults to now')
    # args = parser.parse_args()

    source_name = ''
    start_date = '16.04.2026'
    end_date = None

    # source_name = args.source_name or source_name
    # start_date = args.start_date or start_date
    # end_date = args.end_date or end_date

    count = asyncio.run(
        requeue_transactions_for_export(
            source_name=source_name,
            start_date=_parse_cli_date(start_date),
            end_date=_parse_cli_date(end_date) if end_date else None,
        )
    )
    print(f'Requeued {count} transactions')
