import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
import yaml
from dotenv import load_dotenv


@dataclass(slots=True)
class DatabaseSettings:
    url: str


@dataclass(slots=True)
class WorkerSettings:
    import_lookback_days: int = 5
    sleep_seconds: int = 120


@dataclass(slots=True)
class TelegramSettings:
    enabled: bool
    token: str
    admin_chat_id: int
    max_message_length: int = 4096


@dataclass(slots=True)
class GoogleSheetsColumnSettings:
    field: str
    column_number: int
    header: str


@dataclass(slots=True)
class GoogleSheetsSettings:
    credentials_path: Path
    spreadsheet_id: str
    columns: list[GoogleSheetsColumnSettings]
    header_row: int = 2


@dataclass(slots=True)
class BankSourceSettings:
    bank_type: str
    sheet_name: str
    iban: str
    token: str | None = None
    id: str | None = None
    client_path: Path | None = None
    executable_path: Path | None = None
    token_file_path: Path | None = None


@dataclass(slots=True)
class Settings:
    database: DatabaseSettings
    google_sheets: GoogleSheetsSettings
    worker: WorkerSettings
    telegram: TelegramSettings
    bank_sources: dict[str, BankSourceSettings]


def get_env(var: str, default: str | None = None) -> str:
    value = os.getenv(var)
    if value is not None:
        return value
    if default is not None:
        return default
    raise ValueError(f'Environment variable {var} is not set')


def get_env_bool(var: str) -> bool:
    value = get_env(var).lower()
    if value in {'1', 'true', 'yes', 'on'}:
        return True
    if value in {'0', 'false', 'no', 'off'}:
        return False
    raise ValueError(f'Environment variable {var} must be boolean, got {value!r}')


def load_yaml(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f'Configuration file not found: {path}')
    return yaml.safe_load(path.read_text(encoding='utf-8'))


def load_google_sheets_columns(path: Path) -> list[GoogleSheetsColumnSettings]:
    raw_columns = load_yaml(path)
    return [
        GoogleSheetsColumnSettings(field=item['field'], column_number=int(item['column']), header=item['header'])
        for item in raw_columns
    ]


def load_settings() -> Settings:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent

    load_dotenv('/etc/env/tg.env')
    load_dotenv('/etc/env/dev.env')
    load_dotenv('/etc/env/db.env')

    db = get_env('TRANSACTIONS_DB')
    user = get_env('POSTGRES_USER')
    password = get_env('POSTGRES_PASSWORD')
    host = get_env('POSTGRES_HOST', 'localhost')
    url = f'postgresql+asyncpg://{quote_plus(user)}:{quote_plus(password)}@{host}/{db}'

    google_credentials_path = Path('/etc/env/credentials.json')
    columns_path = PROJECT_ROOT / 'config' / 'google_sheets_columns.yaml'
    spreadsheet_id = get_env('TRANSACTIONS_SPREADSHEET_ID')
    header_row = 2

    telegram_enabled = get_env_bool('DO_SEND_TO_BOT')
    telegram_token = get_env('tg_token_tools')
    telegram_admin_chat_id = int(get_env('admin_tg'))
    bank_sources_path = Path('/etc/env/bank_sources.yaml')

    bank_sources: dict[str, BankSourceSettings] = {}
    for source_name, payload in load_yaml(bank_sources_path).items():
        bank_sources[source_name] = BankSourceSettings(
            bank_type=payload['bank_type'],
            sheet_name=payload['sheet_name'],
            iban=payload['iban'],
            token=payload.get('token'),
            id=payload.get('id'),
            client_path=Path(payload['client_path']) if payload.get('client_path') else None,
            executable_path=Path(payload['executable_path']) if payload.get('executable_path') else None,
            token_file_path=Path(payload['token_file_path']) if payload.get('token_file_path') else None,
        )

    return Settings(
        database=DatabaseSettings(url=url),
        google_sheets=GoogleSheetsSettings(
            credentials_path=google_credentials_path,
            spreadsheet_id=spreadsheet_id,
            columns=load_google_sheets_columns(columns_path),
            header_row=header_row,
        ),
        worker=WorkerSettings(),
        telegram=TelegramSettings(enabled=telegram_enabled, token=telegram_token, admin_chat_id=telegram_admin_chat_id),
        bank_sources=bank_sources,
    )
