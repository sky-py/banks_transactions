import asyncio
import csv
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from adapters.banks.base import BankAdapter
from domain.exceptions import BankAdapterError
from domain.models import Transaction
from domain.money import money_to_minor_units


UNIVERSAL_EXPORT_TIMEOUT_SECONDS = 120
SET_ENVIRONMENT_FILE_NAME = 'setEnvironment.bat'


def find_java_executable() -> Path:
    candidates: list[Path] = []

    java_home = os.getenv('JAVA_HOME')
    if java_home:
        candidates.append(Path(java_home) / 'bin' / 'java.exe')

    for root in (os.getenv('ProgramFiles'), os.getenv('ProgramFiles(x86)')):
        if not root:
            continue
        java_root = Path(root) / 'Java'
        candidates.extend(java_root.glob('*/bin/java.exe'))

    path_java = shutil.which('java')
    if path_java:
        candidates.append(Path(path_java))

    existing_candidates = [candidate for candidate in candidates if candidate.exists()]
    if not existing_candidates:
        raise BankAdapterError('Java executable not found')

    return max(existing_candidates, key=java_version_key)


def java_version_key(java_path: Path) -> tuple[int, ...]:
    numbers = re.findall(r'\d+', java_path.parent.parent.name)
    return tuple(int(number) for number in numbers)


def update_environment_file(environment_file: Path, ibank_home: Path) -> None:
    if not environment_file.exists():
        raise BankAdapterError(f'Universal environment file not found: {environment_file}')

    java_path = find_java_executable()

    lines = environment_file.read_text(encoding='cp1251').splitlines()
    updated_lines = []
    java_updated = False
    ibank_home_updated = False

    for line in lines:
        stripped_line = line.strip().lower()
        if stripped_line.startswith('set java='):
            updated_lines.append(f'set JAVA="{java_path}"')
            java_updated = True
        elif stripped_line.startswith('set ibank_home='):
            updated_lines.append(f'set IBANK_HOME={ibank_home}')
            ibank_home_updated = True
        else:
            updated_lines.append(line)

    if not java_updated:
        updated_lines.append(f'set JAVA="{java_path}"')
    if not ibank_home_updated:
        updated_lines.append(f'set IBANK_HOME={ibank_home}')

    environment_file.write_text('\n'.join(updated_lines) + '\n', encoding='cp1251')


class UniversalAdapter(BankAdapter):
    def __init__(self, source_name: str, iban: str, fop_folder: Path, executable: Path) -> None:
        self.source_name = source_name
        self.iban = iban
        self.fop_folder = fop_folder
        self.executable = executable
        self.environment_file = self.executable.parent / SET_ENVIRONMENT_FILE_NAME
        self.transactions_file = self.fop_folder / 'OUT' / f'{self.iban}UAH_opers.txt'
        self.balance_file = self.fop_folder / 'OUT' / f'{self.iban}UAH_saldo.txt'
        update_environment_file(environment_file=self.environment_file, ibank_home=self.executable.parent.parent)

    async def fetch_transactions(self, start_date: datetime, end_date: datetime) -> list[Transaction]:
        await asyncio.to_thread(self._run_export_sync, start_date, end_date)
        rows = await asyncio.to_thread(self._read_transactions_file)
        balance = await self.fetch_balance()
        return [self._map_transaction(row, balance) for row in rows]

    async def fetch_balance(self, at_datetime: datetime | None = None) -> int | None:
        return await asyncio.to_thread(self._read_balance, at_datetime)

    def _read_balance(self, at_datetime: datetime | None) -> int | None:
        if not self.balance_file.exists():
            return None

        with self.balance_file.open(mode='r', newline='', encoding='cp1251') as csv_file:
            reader = csv.DictReader(csv_file, delimiter=';')
            rows = list(reader)
        if not rows:
            raise ValueError(f'Файл {self.balance_file} пустой или не содержит данных')
        return self._parse_money(rows[-1]['Вихідний залишок'])

    def _run_export_sync(self, start_date: datetime, end_date: datetime) -> None:
        command = [str(self.executable), start_date.strftime('%d.%m.%Y'), end_date.strftime('%d.%m.%Y')]
        try:
            subprocess.run(
                command,
                cwd=self.executable.parent,
                check=True,
                timeout=UNIVERSAL_EXPORT_TIMEOUT_SECONDS,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as e:
            raise BankAdapterError(
                f'Universal export timeout for {self.source_name} after {UNIVERSAL_EXPORT_TIMEOUT_SECONDS} seconds'
            ) from e
        except subprocess.CalledProcessError as e:
            raise BankAdapterError(f'Universal export failed for {self.source_name}: exit code {e.returncode}') from e

    def _read_transactions_file(self) -> list[dict]:
        if not self.transactions_file.exists():
            raise FileNotFoundError(f'Universal transactions file not found: {self.transactions_file}')

        with self.transactions_file.open(mode='r', newline='', encoding='cp1251') as csv_file:
            reader = csv.DictReader(csv_file, delimiter=';')
            return list(reader)

    def _map_transaction(self, row: dict, balance: int | None) -> Transaction:
        debit_value = self._parse_money(row.get('Дебет'))
        credit_value = self._parse_money(row.get('Кредит'))
        amount = credit_value if credit_value is not None else -1 * debit_value
        occurred_at = datetime.strptime(row['Дата операції'], '%d.%m.%Y %H:%M:%S')
        document_id = row['Номер документа']

        return Transaction(
            unique_id=f'{document_id}:{occurred_at.strftime("%d.%m.%Y_%H:%M:%S")}:{amount}',
            external_id=document_id,
            source_name=self.source_name,
            iban=self.iban,
            occurred_at=occurred_at,
            amount=amount,
            currency='UAH',
            counterparty=row.get('Кореспондент', ''),
            description=row.get('Призначення платежу', ''),
            bank_balance=balance,
            raw_data=row,
        )

    @staticmethod
    def _parse_money(value: str | None) -> int | None:
        if not value:
            return None
        return money_to_minor_units(value)
