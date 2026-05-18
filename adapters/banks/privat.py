from datetime import datetime
from typing import Literal
import httpx
from adapters.banks.base import BankAdapter
from domain.exceptions import BankAdapterError
from domain.money import money_to_minor_units
from domain.models import Transaction


LIMIT_TRANSACTIONS_PER_PAGE = 500
REQUEST_TIMEOUT = httpx.Timeout(12.0, connect=4.0)


class PrivatAdapter(BankAdapter):
    API_URL = 'https://acp.privatbank.ua/api'
    TRANSACTIONS_URL = f'{API_URL}/statements/transactions'
    BALANCE_URL = f'{API_URL}/statements/balance'

    def __init__(self, source_name: str, iban: str, account_id: str, token: str) -> None:
        self.source_name = source_name
        self.iban = iban
        self.account_id = account_id
        self.token = token
        self.client = httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT,
            headers={
                'User-Agent': 'curl/8.6.0',
                'Content-Type': 'application/json;charset=utf8',
                'id': self.account_id,
                'token': self.token,
            },
        )

    async def fetch_transactions(self, start_date: datetime, end_date: datetime) -> list[Transaction]:
        data = await self._get_paginated_data(
            start_date=start_date, end_date=end_date, data_type='transactions', url=self.TRANSACTIONS_URL
        )
        bank_balance = await self.fetch_balance()
        return [self._map_transaction(item, bank_balance) for item in data]

    async def fetch_balances(self, start_date: datetime, end_date: datetime | None = None) -> list[dict]:
        end_date = end_date or datetime.now()
        data = await self._get_paginated_data(
            start_date=start_date, end_date=end_date, data_type='balances', url=self.BALANCE_URL
        )
        return data

    async def fetch_balance(self, at_datetime: datetime | None = None) -> int:
        at_datetime = at_datetime or datetime.now()
        data = await self.fetch_balances(start_date=at_datetime, end_date=at_datetime)
        return money_to_minor_units(data[0]['balanceOut'])

    async def _get_paginated_data(
        self, start_date: datetime, end_date: datetime, data_type: Literal['transactions', 'balances'], url: str
    ) -> list[dict]:
        date_format = '%d-%m-%Y'
        params = {
            'acc': self.iban,
            'startDate': start_date.strftime(date_format),
            'endDate': end_date.strftime(date_format),
            'limit': LIMIT_TRANSACTIONS_PER_PAGE,
        }

        data_list = []
        next_page_id = None

        while True:
            if next_page_id is not None:
                params['followId'] = next_page_id

            r = await self.client.get(url=url, params=params)
            r.raise_for_status()
            data = r.json()

            if data.get('status') != 'SUCCESS':
                raise BankAdapterError(f'Privat returned unexpected status for url {url}')

            data_list.extend(data.get(data_type, []))

            if data.get('exist_next_page'):
                next_page_id = data.get('next_page_id')
                if not next_page_id:
                    raise BankAdapterError(f'Privat pagination has no next_page_id for url {url}')
            else:
                break

        return data_list

    async def aclose(self) -> None:
        await self.client.aclose()

    def _map_transaction(self, item: dict, bank_balance: int) -> Transaction:
        ref = item['REF']
        item_id = item['ID']
        amount = money_to_minor_units(item['SUM'])
        match item['TRANTYPE']:
            case 'D':
                amount = -amount
            case 'C':
                pass
            case _:
                raise BankAdapterError(f"Unknown Privat transaction type '{item['TRANTYPE']}' for {ref}")

        return Transaction(
            unique_id=f'{ref}:{item_id}:{amount}',
            external_id=ref,
            source_name=self.source_name,
            iban=self.iban,
            occurred_at=datetime.strptime(item['DATE_TIME_DAT_OD_TIM_P'], '%d.%m.%Y %H:%M:%S'),
            amount=amount,
            currency='UAH',
            counterparty=item.get('AUT_CNTR_NAM', ''),
            description=item.get('OSND', ''),
            bank_balance=bank_balance,
            raw_data=item,
        )
