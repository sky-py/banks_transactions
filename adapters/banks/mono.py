from datetime import datetime
import httpx
from adapters.banks.base import BankAdapter
from domain.exceptions import BankAdapterError
from domain.models import Transaction


REQUEST_TIMEOUT = httpx.Timeout(12.0, connect=4.0)


class MonoAdapter(BankAdapter):
    CLIENT_INFO_URL = 'https://api.monobank.ua/personal/client-info'
    STATEMENT_URL = 'https://api.monobank.ua/personal/statement'

    def __init__(self, source_name: str, iban: str, token: str, account_id: str | None = None) -> None:
        self.source_name = source_name
        self.iban = iban
        self.token = token
        self.account_id = account_id
        self.client = httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT, headers={'X-Token': self.token, 'Content-Type': 'application/json'}
        )

    async def _get_and_validate_data(self, url: str) -> list | dict:
        r = await self.client.get(url=url)
        r.raise_for_status()
        data = r.json()
        return data

    async def fetch_transactions(self, start_date: datetime, end_date: datetime) -> list[Transaction]:
        account_id = await self._get_account_id()
        start_ts = int(start_date.timestamp())
        end_ts = int(end_date.timestamp())
        data = await self._get_and_validate_data(f'{self.STATEMENT_URL}/{account_id}/{start_ts}/{end_ts}')
        if not isinstance(data, list):
            raise BankAdapterError(f'Unexpected Mono statement response for source {self.source_name}')
        return [self._map_transaction(item) for item in data]

    async def fetch_balance(self, at_datetime: datetime | None = None) -> int | None:
        account = await self._get_account_info()
        return int(account['balance'])

    async def _get_account_id(self) -> str:
        if self.account_id is None:
            account = await self._get_account_info()
            self.account_id = str(account['id'])
        return self.account_id

    async def _get_account_info(self) -> dict:
        data = await self._get_and_validate_data(self.CLIENT_INFO_URL)
        for account_dict in data.get('accounts', []):
            if account_dict.get('iban') == self.iban:
                return account_dict
        raise BankAdapterError(f'Mono account with IBAN {self.iban} not found')

    async def aclose(self) -> None:
        await self.client.aclose()

    def _map_transaction(self, item: dict) -> Transaction:
        amount = int(item['amount'])
        return Transaction(
            unique_id=f'{item['id']}:{amount}',
            external_id=item['id'],
            source_name=self.source_name,
            iban=self.iban,
            occurred_at=datetime.fromtimestamp(item['time']),
            amount=amount,
            currency='UAH',
            counterparty=item.get('counterName') or item.get('description', ''),
            description=item.get('comment', ''),
            bank_balance=int(item['balance']),
            raw_data=item,
        )
