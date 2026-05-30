import asyncio
import platform
from datetime import datetime, timedelta
from pathlib import Path
from adapters.banks.base import BankAdapter
from adapters.banks.mono import MonoAdapter
from adapters.banks.novapay import NovapayAdapter
from adapters.banks.privat import PrivatAdapter
from adapters.banks.universal import UniversalAdapter
from adapters.exporters.google_sheets import GoogleSheetsExporter
from config.logger import logger_init
from config.rich_log import RichLogMulti
from config.settings import BankSourceSettings, Settings, load_settings
from db.session import create_engine_and_session_factory, create_tables
from db.uow_sqlalchemy import SqlAlchemyUnitOfWork
from loguru import logger
from orchestrators.export_orchestrator import ExportOrchestrator
from orchestrators.import_orchestrator import ImportOrchestrator
from telegram.sender_sync import build_service_tg_sender


reload_file = Path(__file__).with_suffix('.reload')


def build_bank_adapter(source_name: str, source_settings: BankSourceSettings) -> BankAdapter:
    match source_settings.bank_type:
        case 'privat':
            return PrivatAdapter(
                source_name=source_name,
                iban=source_settings.iban,
                account_id=source_settings.id,
                token=source_settings.token,
            )
        case 'universal':
            return UniversalAdapter(
                source_name=source_name,
                iban=source_settings.iban,
                fop_folder=source_settings.client_path,
                executable=source_settings.executable_path,
            )
        case 'monobank':
            return MonoAdapter(
                source_name=source_name,
                iban=source_settings.iban,
                token=source_settings.token,
                account_id=source_settings.id,
            )
        case 'novapay':
            return NovapayAdapter(
                source_name=source_name,
                iban=source_settings.iban,
                login=source_settings.id,
                token_file=source_settings.token_file_path,
            )
        case _:
            raise ValueError(f'Unsupported bank type: {source_settings.bank_type}')


async def worker(
    import_orchestrator: ImportOrchestrator,
    export_orchestrator: ExportOrchestrator,
    import_lookback_days: int,
    sleep_seconds: int,
) -> None:
    errors_num = 0
    while True:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=import_lookback_days)

        try:
            imported_count, import_errors = await import_orchestrator.import_transactions(start_date=start_date, end_date=end_date)
            exported_count, export_errors = await export_orchestrator.export_pending()
            errors = f'ERRORS: {import_errors}/{export_errors}' if import_errors or export_errors else ''
            logger.bind(area='global').info(f'Imported/Exported: {imported_count}/{exported_count} {errors}')
            errors_num = 0
        except Exception as exc:
            errors_num += 1
            logger.warning(f'Worker iteration {errors_num} failed {exc}')
            if errors_num > 5:
                raise

        if reload_file.exists():
            logger.info(f'Found reload file {__file__}, STOPPING WORKER')
            return
        await asyncio.to_thread(rich_log.sleep, sleep_seconds)


async def main(settings: Settings) -> None:
    engine, session_factory = create_engine_and_session_factory(settings.database.url)
    await create_tables(engine)

    exporters = [
        GoogleSheetsExporter(
            credentials_path=settings.google_sheets.credentials_path,
            spreadsheet_id=settings.google_sheets.spreadsheet_id,
            source_name_to_sheet={
                source: source_settings.sheet_name for source, source_settings in settings.bank_sources.items()
            },
            columns=settings.google_sheets.columns,
            header_row_number=settings.google_sheets.header_row,
        )
    ]
    export_uow = SqlAlchemyUnitOfWork(session_factory=session_factory)
    export_orchestrator = ExportOrchestrator(uow=export_uow, exporters=exporters)
    bank_adapters = [
        build_bank_adapter(source_name, source_settings)
        for source_name, source_settings in settings.bank_sources.items()
    ]
    import_orchestrator = ImportOrchestrator(
        bank_adapters=bank_adapters, uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory=session_factory)
    )

    try:
        await worker(
            import_orchestrator=import_orchestrator,
            export_orchestrator=export_orchestrator,
            import_lookback_days=settings.worker.import_lookback_days,
            sleep_seconds=settings.worker.sleep_seconds,
        )
    finally:
        for bank_adapter in bank_adapters:
            await bank_adapter.aclose()
        await engine.dispose()


if __name__ == '__main__':
    settings = load_settings()
    rich_log = RichLogMulti(
        header=f'Транзакции банков - {__file__}',
        shop_names=[source_name for source_name in settings.bank_sources.keys()],
        header_style='bold white on green',
    )
    logger_init(rich_log=rich_log, log_cut_after='=>', service_notifier=build_service_tg_sender(settings.telegram))
    logger.info(f'STARTING {__file__}')

    if platform.system() == 'Windows':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main(settings))
    except Exception as e:
        logger.exception(f'Error in {__file__}: {e}')
        raise
    finally:
        reload_file.unlink(missing_ok=True)
        rich_log.stop()
        logger.info(f'SHUTTING DOWN {__file__}')
