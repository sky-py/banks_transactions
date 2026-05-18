import asyncio
from collections import defaultdict
from pathlib import Path
from typing import Any
import gspread
from adapters.exporters.base import Exporter
from config.settings import GoogleSheetsColumnSettings
from domain.models import Transaction
from domain.money import minor_units_to_sheet_value
from gspread.spreadsheet import Spreadsheet
from gspread.utils import rowcol_to_a1
from gspread.worksheet import Worksheet
from loguru import logger


CREDIT_ROW_COLOR = {'red': 0.82, 'green': 1.0, 'blue': 0.82}
DEBIT_ROW_COLOR = {'red': 1.0, 'green': 0.96, 'blue': 0.8}


class GoogleSheetsExporter(Exporter):
    def __init__(
        self,
        credentials_path: Path,
        spreadsheet_id: str,
        source_name_to_sheet: dict[str, str],
        columns: list[GoogleSheetsColumnSettings],
        header_row_number: int = 2,
    ) -> None:
        self.credentials_path = credentials_path
        self.spreadsheet_id = spreadsheet_id
        self.source_name_to_sheet = source_name_to_sheet
        self.columns = sorted(columns, key=lambda item: item.column_number)
        self.header_row_number = header_row_number
        self.insert_row_number = header_row_number + 1
        self.max_column = self.columns[-1].column_number
        self.amount_column_number = self._column_number_for_field('amount')

    async def export_transactions(self, transactions: list[Transaction]) -> None:
        if not transactions:
            return
        await asyncio.to_thread(self._export_sync, transactions)

    def _export_sync(self, transactions: list[Transaction]) -> None:
        spreadsheet = gspread.auth.service_account(filename=self.credentials_path).open_by_key(self.spreadsheet_id)
        grouped = self._group_by_sheet(transactions)
        for sheet_name, sheet_transactions in grouped.items():
            worksheet = self._get_or_create_worksheet(spreadsheet, sheet_name)
            rows = [self._build_row(transaction=transaction) for transaction in sheet_transactions]
            worksheet.insert_rows(rows, row=self.insert_row_number, value_input_option='USER_ENTERED')
            try:
                self._format_rows(worksheet, sheet_transactions)
            except Exception as e:
                logger.warning(f'Google Sheets row formatting failed: {e}')

    def _group_by_sheet(self, transactions: list[Transaction]) -> dict[str, list[Transaction]]:
        grouped: dict[str, list[Transaction]] = defaultdict(list)
        sorted_transactions = sorted(transactions, key=lambda transaction: transaction.occurred_at, reverse=True)
        for transaction in sorted_transactions:
            grouped[self.source_name_to_sheet[transaction.source_name]].append(transaction)
        return grouped

    def _get_or_create_worksheet(self, spreadsheet: Spreadsheet, sheet_name: str) -> Worksheet:
        for worksheet in spreadsheet.worksheets():
            if worksheet.title == sheet_name:
                return worksheet

        worksheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=self.max_column + 2)
        worksheet.update(
            range_name=f'{self.header_row_number}:{self.header_row_number}',
            values=[self._build_headers()],
            value_input_option='RAW',
        )
        return worksheet

    def _build_headers(self) -> list[str]:
        headers = [''] * self.max_column
        for column in self.columns:
            headers[column.column_number - 1] = column.header
        return headers

    def _build_row(self, transaction: Transaction) -> list[Any]:
        row = [''] * self.max_column
        for column in self.columns:
            row[column.column_number - 1] = self._value_for_field(transaction=transaction, field=column.field)
        return row

    def _value_for_field(self, transaction: Transaction, field: str) -> Any:
        match field:
            case 'occurred_at':
                return transaction.occurred_at.strftime('%d.%m.%Y %H:%M:%S')
            case 'external_id':
                return transaction.external_id
            case 'source_name':
                return transaction.source_name
            case 'iban':
                return transaction.iban
            case 'amount':
                return minor_units_to_sheet_value(transaction.amount)
            case 'currency':
                return transaction.currency
            case 'counterparty':
                return transaction.counterparty
            case 'description':
                return transaction.description
            case 'bank_balance':
                return minor_units_to_sheet_value(transaction.bank_balance)
            case 'calculated_balance':
                return self._build_calculated_balance_formula()
            case _:
                return ''

    def _build_calculated_balance_formula(self) -> str:
        return f'=N(INDIRECT("R[1]C"; FALSE))+N(INDIRECT("RC{self.amount_column_number}"; FALSE))'

    def _format_rows(self, worksheet: Worksheet, transactions: list[Transaction]) -> None:
        formats = []
        for index, transaction in enumerate(transactions):
            row_number = self.insert_row_number + index
            color = CREDIT_ROW_COLOR if transaction.amount >= 0 else DEBIT_ROW_COLOR
            formats.append({'range': self._row_range(row_number), 'format': {'backgroundColor': color}})
        worksheet.batch_format(formats)

    def _row_range(self, row_number: int) -> str:
        return f'{rowcol_to_a1(row_number, 1)}:{rowcol_to_a1(row_number, self.max_column)}'

    def _column_number_for_field(self, field: str) -> int:
        for column in self.columns:
            if column.field == field:
                return column.column_number
        raise ValueError(f'Google Sheets column for field {field} is not configured')
