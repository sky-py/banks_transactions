import inspect
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from loguru import logger


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / 'log'


LOG_ROTATION = '1 month'
LOG_RETENTION = '1 year'
LOG_FORMAT = '{time:YYYY-MM-DD at HH:mm:ss.SSS} | {level} | {message}'
SHORT_LOG_FORMAT = '{time:YYYY-MM-DD at HH:mm:ss.SSS} | {level} | {extra[short_message]}\n'
RICH_LOG_FORMAT = (
    '<green>{time:YYYY-MM-DD at HH:mm:ss.SSS}</green> | '
    '<level>{level}</level> | '
    '<level>{extra[short_message]}</level>\n'
)


class RichLogger(Protocol):
    def print_log(self, text: str) -> None: ...
    def print_to_request_area(self, area: str, text: str) -> None: ...


def has_source_name(record) -> bool:
    return 'source_name' in record['extra']


def get_caller_log_name() -> str:
    frame = inspect.currentframe()
    caller = frame.f_back.f_back if frame and frame.f_back else None
    try:
        if caller is None:
            return 'app'
        return Path(caller.f_code.co_filename).stem
    finally:
        del frame


def short_log_formatter(cut_after: str | None, log_format: str = SHORT_LOG_FORMAT) -> Callable[..., str]:
    def formatter(record) -> str:
        message = record['message']
        if cut_after is not None:
            message = message.split(cut_after, maxsplit=1)[0]
        record['extra']['short_message'] = message
        return log_format

    return formatter


def logger_init(
    log_dir: Path = LOG_DIR,
    service_notifier: Callable[[str], None] | None = None,
    rich_log: RichLogger | None = None,
    log_name: str | None = None,
    rich_log_colorize: bool = True,
    log_cut_after: str | None = None,
) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_name = log_name or get_caller_log_name()

    if rich_log is not None:
        rich_log_format = RICH_LOG_FORMAT if rich_log_colorize else SHORT_LOG_FORMAT
        logger.remove()
        logger.add(
            sink=lambda msg: rich_log.print_log(str(msg).rstrip()),
            format=short_log_formatter(log_cut_after, rich_log_format),
            level='INFO',
            colorize=rich_log_colorize,
            filter=lambda record: not has_source_name(record),
        )
        logger.add(
            sink=lambda msg: rich_log.print_to_request_area(
                msg.record['extra']['source_name'],
                msg.record['message'],
            ),
            level='INFO',
            filter=has_source_name,
        )
    logger.add(
        sink=log_dir / f'{log_name}.log',
        format=short_log_formatter(log_cut_after),
        level='INFO',
        filter=lambda record: not has_source_name(record),
        rotation=LOG_ROTATION,
        retention=LOG_RETENTION,
        compression='zip',
        encoding='utf-8',
    )
    logger.add(
        sink=log_dir / f'{log_name}_debug.log',
        format=LOG_FORMAT,
        level='DEBUG',
        backtrace=True,
        diagnose=True,
        filter=lambda record: not has_source_name(record),
        rotation=LOG_ROTATION,
        retention=LOG_RETENTION,
        compression='zip',
        encoding='utf-8',
    )

    if service_notifier is not None:
        logger.add(
            sink=lambda msg: service_notifier(str(msg)),
            format='{time:YYYY-MM-DD at HH:mm:ss} | {level} | {message}',
            level='ERROR',
        )
