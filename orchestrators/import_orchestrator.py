import asyncio
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from adapters.banks.base import BankAdapter
from db.uow import UnitOfWork
from loguru import logger


MAX_PARALLEL_IMPORTS_PER_ADAPTER_TYPE = 1
BANK_ERROR_AFTER_ERRORS = 20


@dataclass(slots=True)
class ImportResult:
    source_name: str
    imported_count: int = 0
    error: Exception | None = None


class ImportOrchestrator:
    def __init__(self, bank_adapters: list[BankAdapter], uow_factory: Callable[[], UnitOfWork]) -> None:
        self.bank_adapters = bank_adapters
        self.uow_factory = uow_factory
        self.source_errors: dict[str, int] = defaultdict(int)

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
        results = await asyncio.gather(*tasks)

        imported_count = 0
        for result in results:
            if result.error is not None:
                self._handle_import_error(result)
                continue
            imported_count += result.imported_count
            self.source_errors[result.source_name] = 0
        return imported_count

    async def _import_from_adapter_with_limit(
        self, bank_adapter: BankAdapter, semaphore: asyncio.Semaphore, start_date: datetime, end_date: datetime
    ) -> ImportResult:
        async with semaphore:
            return await self._import_from_adapter(bank_adapter=bank_adapter, start_date=start_date, end_date=end_date)

    async def _import_from_adapter(
        self, bank_adapter: BankAdapter, start_date: datetime, end_date: datetime
    ) -> ImportResult:
        source_name = self._adapter_source_name(bank_adapter)
        try:
            fetched_transactions = await bank_adapter.fetch_transactions(start_date, end_date)
        except Exception as exc:
            return ImportResult(source_name=source_name, error=exc)

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
                return ImportResult(source_name=source_name)

            await uow.transactions.add_many(new_transactions)
            await uow.outbox.add_many(new_transactions)
            return ImportResult(source_name=source_name, imported_count=len(new_transactions))

    def _adapter_type_name(self, bank_adapter: BankAdapter) -> str:
        return bank_adapter.__class__.__name__

    def _adapter_source_name(self, bank_adapter: BankAdapter) -> str:
        return getattr(bank_adapter, 'source_name', bank_adapter.__class__.__name__)

    def _handle_import_error(self, result: ImportResult) -> None:
        self.source_errors[result.source_name] += 1
        if self.source_errors[result.source_name] >= BANK_ERROR_AFTER_ERRORS:
            logger.error(
                f'Import task FAILED for {result.source_name} '
                f'{self.source_errors[result.source_name]} times in a row: {result.error}'
            )
            self.source_errors[result.source_name] = 0
            return
        logger.bind(source_name=result.source_name).info(
            f'FAILED {self.source_errors[result.source_name]}/{BANK_ERROR_AFTER_ERRORS}: {result.error}'
        )
