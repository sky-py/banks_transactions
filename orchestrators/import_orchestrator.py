import asyncio
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from adapters.banks.base import BankAdapter
from db.uow import UnitOfWork
from loguru import logger


MAX_PARALLEL_IMPORTS_PER_ADAPTER_TYPE = 2


class ImportOrchestrator:
    def __init__(self, bank_adapters: list[BankAdapter], uow_factory: Callable[[], UnitOfWork]) -> None:
        self.bank_adapters = bank_adapters
        self.uow_factory = uow_factory

    async def import_transactions(self, start_date: datetime, end_date: datetime) -> int:
        semaphores: dict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(MAX_PARALLEL_IMPORTS_PER_ADAPTER_TYPE)
        )
        tasks = [
            self._import_from_adapter_with_limit(
                bank_adapter=bank_adapter,
                semaphore=semaphores[self._adapter_type_name(bank_adapter)],
                start_date=start_date,
                end_date=end_date,
            )
            for bank_adapter in self.bank_adapters
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        imported_count = 0
        for result in results:
            if isinstance(result, Exception):
                logger.error(f'Import task failed: {result}')
                continue
            imported_count += result
        return imported_count

    async def _import_from_adapter_with_limit(
        self, bank_adapter: BankAdapter, semaphore: asyncio.Semaphore, start_date: datetime, end_date: datetime
    ) -> int:
        async with semaphore:
            return await self._import_from_adapter(bank_adapter=bank_adapter, start_date=start_date, end_date=end_date)

    async def _import_from_adapter(self, bank_adapter: BankAdapter, start_date: datetime, end_date: datetime) -> int:
        source_name = self._adapter_source_name(bank_adapter)
        try:
            fetched_transactions = await bank_adapter.fetch_transactions(start_date, end_date)
        except Exception as exc:
            logger.bind(source_name=source_name).error(f'FAILED: {exc}')
            return 0

        logger.bind(source_name=source_name).info(f'Fetched {len(fetched_transactions)} transactions')
        unique_ids = [transaction.unique_id for transaction in fetched_transactions]

        async with self.uow_factory() as uow:
            existing_ids = await uow.transactions.get_existing_unique_ids(unique_ids)
            new_transactions = []
            for transaction in fetched_transactions:
                if transaction.unique_id not in existing_ids:
                    new_transactions.append(transaction)
                    logger.info(f'{source_name} got transaction {transaction.unique_id} => {transaction}')

            if not new_transactions:
                return 0

            await uow.transactions.add_many(new_transactions)
            await uow.outbox.add_many(new_transactions)
            return len(new_transactions)

    def _adapter_type_name(self, bank_adapter: BankAdapter) -> str:
        return bank_adapter.__class__.__name__

    def _adapter_source_name(self, bank_adapter: BankAdapter) -> str:
        return getattr(bank_adapter, 'source_name', bank_adapter.__class__.__name__)
