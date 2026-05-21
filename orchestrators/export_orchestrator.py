from collections import defaultdict
from adapters.exporters.base import Exporter
from db.uow import UnitOfWork
from domain.models import Transaction
from loguru import logger


class ExportOrchestrator:
    def __init__(self, uow: UnitOfWork, exporters: list[Exporter]) -> None:
        self.uow = uow
        self.exporters = exporters

    async def export_pending(self) -> int:
        async with self.uow as uow:
            entries = await uow.outbox.get_pending()

        if not entries:
            return 0

        exported_entries_num = 0
        transactions_by_source = defaultdict(list)
        for entry in entries:
            transactions_by_source[entry.transaction.source_name].append(entry)

        for source_name, source_entries in transactions_by_source.items():
            try:
                await self.export_transactions([entry.transaction for entry in source_entries])
            except Exception as exc:
                logger.error(f'Failed to export transactions for {source_name}: {exc}')
            else:
                async with self.uow as uow:
                    await uow.outbox.mark_processed([entry.id for entry in source_entries])
                exported_entries_num += len(source_entries)
                logger.bind(source_name=source_name).info(
                    f'Exported to {source_name} transactions: {[entry.transaction.external_id for entry in source_entries]}'
                )

        return exported_entries_num

    async def export_transactions(self, transactions: list[Transaction]) -> None:
        for exporter in self.exporters:
            try:
                await exporter.export_transactions(transactions)
                # logger.info(f'Exported transactions through fake {exporter.__class__.__name__}')
            except Exception as exc:
                logger.error(f'Failed to export transactions through {exporter.__class__.__name__}: {exc}')
                raise
