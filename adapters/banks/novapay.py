import asyncio
import json
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import httpx
from adapters.banks.base import BankAdapter
from domain.exceptions import BankAdapterError
from domain.models import Transaction
from domain.money import money_to_minor_units
from loguru import logger


REQUEST_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
SOAP_NS = 'http://schemas.xmlsoap.org/soap/envelope/'
TEMPURI_NS = 'http://tempuri.org/'
SOAP_ACTION_BASE = 'http://tempuri.org/IClientAPIService/'
CONDUCTED_DATE_TYPE = 0
CONDUCTED_STATUS_DOCUMENT_ID = '8'


class NovapayAdapter(BankAdapter):
    API_URL = 'https://business.novapay.ua/Services/ClientAPIService.svc'

    def __init__(self, iban: str, login: str, token_file: Path, source_name: str) -> None:
        self.iban = iban
        self.login = login
        self.token_file = token_file
        self.source_name = source_name
        self.jwt: str | None = None
        self.jwt_expires_at: datetime | None = None
        self.auth_lock = asyncio.Lock()
        self.novapay_account_id: int | None = None
        self.client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)

    async def fetch_transactions(self, start_date: datetime, end_date: datetime) -> list[Transaction]:
        jwt = await self._get_jwt()
        novapay_account_id = await self._get_novapay_account_id(jwt)
        payments_xml = await self._fetch_payments_xml(jwt, novapay_account_id, start_date, end_date)
        bank_balance = await self._fetch_balance_with_jwt(jwt, novapay_account_id)
        return [
            self._map_transaction(doc, bank_balance)
            for doc in self._parse_payment_docs(payments_xml)
            if self._is_conducted_payment(doc)
        ]

    async def fetch_balance(self, at_datetime: datetime | None = None) -> int | None:
        jwt = await self._get_jwt()
        novapay_account_id = await self._get_novapay_account_id(jwt)
        return await self._fetch_balance_with_jwt(jwt, novapay_account_id)

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _get_jwt(self) -> str:
        if self.jwt is not None and not self._is_jwt_expired():
            return self.jwt

        async with self.auth_lock:
            if self.jwt is None or self._is_jwt_expired():
                self.jwt = await self._authenticate()
        return self.jwt

    def _is_jwt_expired(self) -> bool:
        if self.jwt_expires_at is None:
            return False
        return datetime.now() >= self.jwt_expires_at - timedelta(minutes=5)   # JWT_EXPIRATION_MARGIN

    async def _authenticate(self) -> str:
        token_data = self._load_token_data()
        result = await self._call_soap(
            'UserAuthenticationJWT',
            {
                'request_ref': self._request_ref(),
                'refresh_token': token_data['token'],
                'login': self.login,
                'public_certificate': token_data['certificate'],
            },
        )

        jwt = self._required_text(result, 'jwt')
        new_token = self._required_text(result, 'refresh_token')
        new_certificate = self._required_text(result, 'public_certificate')
        self.jwt_expires_at = self._parse_optional_expiration(self._optional_text(result, 'expiration'))
        self._save_token_data(token=new_token, certificate=new_certificate)
        return jwt

    async def _get_novapay_account_id(self, jwt: str) -> int:
        if self.novapay_account_id is not None:
            return self.novapay_account_id

        clients_result = await self._call_soap('GetClientsList', {'request_ref': self._request_ref(), 'jwt': jwt})

        for client_element in self._iter_by_local_name(clients_result, 'Clients'):
            client_id = self._optional_text(client_element, 'id')
            if not client_id:
                continue

            accounts_result = await self._call_soap(
                'GetAccountsList', {'request_ref': self._request_ref(), 'jwt': jwt, 'client_id': client_id}
            )
            account_id = self._find_account_id(accounts_result)
            if account_id is not None:
                self.novapay_account_id = account_id
                return account_id

        raise BankAdapterError(f'Novapay account with IBAN {self.iban} not found')

    async def _fetch_balance_with_jwt(self, jwt: str, novapay_account_id: int) -> int:
        result = await self._call_soap(
            'GetAccountRest', {'request_ref': self._request_ref(), 'jwt': jwt, 'account_id': novapay_account_id}
        )
        balance = (
            self._optional_text(result, 'confirmed_balance')
            or self._optional_text(result, 'available_balance')
            or self._optional_text(result, 'projected_balance')
        )
        if balance is None:
            raise BankAdapterError(f'Novapay balance is missing for account {novapay_account_id}')
        return money_to_minor_units(balance)

    async def _fetch_payments_xml(
        self, jwt: str, novapay_account_id: int, start_date: datetime, end_date: datetime
    ) -> str:
        result = await self._call_soap(
            'GetPaymentsList',
            {
                'request_ref': self._request_ref(),
                'jwt': jwt,
                'account_id': novapay_account_id,
                'date_from': start_date.strftime('%d.%m.%Y'),
                'date_to': end_date.strftime('%d.%m.%Y'),
                'date_type': CONDUCTED_DATE_TYPE,
            },
        )
        return self._optional_text(result, 'payments') or ''

    async def _call_soap(self, method: str, payload: dict[str, object]) -> ET.Element:
        request_body = self._build_soap_envelope(method, payload)
        response = await self.client.post(
            self.API_URL,
            content=request_body,
            headers={'Content-Type': 'text/xml; charset=utf-8', 'SOAPAction': f'"{SOAP_ACTION_BASE}{method}"'},
        )
        if response.is_error:
            safe_request_body = self._safe_xml_for_log(request_body)
            logger.error(
                f'Novapay {method} returned HTTP {response.status_code}. '
                f'Response body: {response.text}. Request body: {safe_request_body}'
            )
            raise BankAdapterError(f'Novapay {method} returned HTTP {response.status_code}')

        root = ET.fromstring(response.text)
        result = self._find_first_by_suffix(root, f'{method}Result')
        if result is None:
            raise BankAdapterError(f'Novapay response has no {method}Result')

        result_status = self._optional_text(result, 'result')
        if result_status and result_status.lower() != 'ok':
            message = self._optional_text(result, 'message') or self._optional_text(result, 'error') or result_status
            raise BankAdapterError(f'Novapay {method} failed: {message}')

        return result

    def _build_soap_envelope(self, method: str, payload: dict[str, object]) -> bytes:
        envelope = ET.Element(f'{{{SOAP_NS}}}Envelope')
        body = ET.SubElement(envelope, f'{{{SOAP_NS}}}Body')
        method_element = ET.SubElement(body, f'{{{TEMPURI_NS}}}{method}')
        request_element = ET.SubElement(method_element, f'{{{TEMPURI_NS}}}request')

        for key, value in payload.items():
            element = ET.SubElement(request_element, f'{{{TEMPURI_NS}}}{key}')
            element.text = str(value)

        return ET.tostring(envelope, encoding='utf-8', xml_declaration=True)

    def _safe_xml_for_log(self, request_body: bytes) -> str:
        try:
            root = ET.fromstring(request_body)
        except ET.ParseError:
            return request_body.decode('utf-8', errors='replace')

        for field_name in ('refresh_token', 'public_certificate', 'jwt'):
            for element in self._iter_by_local_name(root, field_name):
                element.text = '***'

        return ET.tostring(root, encoding='unicode')

    def _find_account_id(self, accounts_result: ET.Element) -> int | None:
        for account_element in self._iter_by_local_name(accounts_result, 'Accounts'):
            iban = self._optional_text(account_element, 'IBAN')
            account_id = self._optional_text(account_element, 'id')
            if iban == self.iban and account_id:
                return int(account_id)
        return None

    def _parse_payment_docs(self, payments_xml: str) -> list[ET.Element]:
        if not payments_xml.strip():
            return []

        try:
            payments_root = ET.fromstring(payments_xml)
        except ET.ParseError as exc:
            raise BankAdapterError('Novapay payments XML cannot be parsed') from exc

        return list(self._iter_by_local_name(payments_root, 'Docs'))

    def _map_transaction(self, doc: ET.Element, bank_balance: int) -> Transaction:
        raw_data = self._element_to_dict(doc)
        amount = money_to_minor_units(doc.attrib.get('Amount', '0'))
        payment_type = self._optional_text(doc, 'PaymentType')

        if payment_type == 'Debit' or self._optional_text(doc, 'DebitCodeIBAN') == self.iban:
            amount = -amount
            counterparty = self._optional_text(doc, 'CreditName') or ''
        else:
            counterparty = self._optional_text(doc, 'DebitName') or ''

        code = self._optional_text(doc, 'Code') or str(uuid.uuid4())
        occurred_at = self._parse_transaction_datetime(doc)

        return Transaction(
            unique_id=f'{code}:{occurred_at.strftime("%d.%m.%Y_%H:%M:%S")}:{amount}',
            external_id=code,
            source_name=self.source_name,
            iban=self.iban,
            occurred_at=occurred_at,
            amount=amount,
            currency=doc.attrib.get('CurrencyTag', 'UAH'),
            counterparty=counterparty,
            description=self._optional_text(doc, 'Purpose') or '',
            bank_balance=bank_balance,
            raw_data=raw_data,
        )

    def _is_conducted_payment(self, doc: ET.Element) -> bool:
        return self._optional_text(doc, 'StatusDocumentId') == CONDUCTED_STATUS_DOCUMENT_ID

    def _parse_transaction_datetime(self, doc: ET.Element) -> datetime:
        for field in ('DayDate', 'OrgDate', 'Created', 'Changed'):
            value = self._optional_text(doc, field)
            if value:
                return self._parse_datetime(value)
        raise BankAdapterError('Novapay transaction date is missing')

    def _parse_datetime(self, value: str) -> datetime:
        normalized = value.strip()
        for date_format in ('%d.%m.%Y %H:%M:%S', '%d.%m.%Y'):
            try:
                return datetime.strptime(normalized, date_format)
            except ValueError:
                continue
        raise BankAdapterError(f'Unsupported Novapay date format: {value}')

    def _parse_optional_expiration(self, value: str | None) -> datetime | None:
        if value is None:
            return None

        normalized = value.strip().replace('Z', '+00:00')
        try:
            expiration = datetime.fromisoformat(normalized)
        except ValueError:
            for date_format in ('%d.%m.%Y %H:%M:%S', '%Y-%m-%d %H:%M:%S'):
                try:
                    return datetime.strptime(normalized, date_format)
                except ValueError:
                    continue
            return None

        if expiration.tzinfo is not None:
            return expiration.astimezone().replace(tzinfo=None)
        return expiration

    def _load_token_data(self) -> dict[str, str]:
        try:
            data = json.loads(self.token_file.read_text(encoding='utf-8'))
        except OSError as exc:
            raise BankAdapterError(f'Cannot read Novapay token file: {self.token_file}') from exc
        except json.JSONDecodeError as exc:
            raise BankAdapterError(f'Novapay token file is not valid JSON: {self.token_file}') from exc

        token = data.get('token')
        certificate = data.get('certificate')
        if not token or not certificate:
            raise BankAdapterError('Novapay token file must contain token and certificate')
        return {'token': token, 'certificate': certificate}

    def _save_token_data(self, token: str, certificate: str) -> None:
        data = {'token': token, 'certificate': certificate}
        temporary_file = self.token_file.with_name(f'{self.token_file.name}.tmp')
        try:
            temporary_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary_file.replace(self.token_file)
        except OSError as exc:
            raise BankAdapterError(f'Cannot write Novapay token file: {self.token_file}') from exc

    def _element_to_dict(self, element: ET.Element) -> dict[str, Any]:
        data: dict[str, Any] = dict(element.attrib)
        for child in element:
            key = self._local_name(child.tag)
            if list(child):
                data[key] = self._element_to_dict(child)
            else:
                data[key] = child.text or ''
        return data

    def _optional_text(self, root: ET.Element, local_name: str) -> str | None:
        element = self._find_first_by_local_name(root, local_name)
        if element is None or element.text is None:
            return None
        return element.text.strip()

    def _required_text(self, root: ET.Element, local_name: str) -> str:
        value = self._optional_text(root, local_name)
        if not value:
            raise BankAdapterError(f'Novapay response field {local_name} is missing')
        return value

    def _find_first_by_local_name(self, root: ET.Element, local_name: str) -> ET.Element | None:
        return next(self._iter_by_local_name(root, local_name), None)

    def _find_first_by_suffix(self, root: ET.Element, local_name_suffix: str) -> ET.Element | None:
        for element in root.iter():
            if self._local_name(element.tag).endswith(local_name_suffix):
                return element
        return None

    def _iter_by_local_name(self, root: ET.Element, local_name: str):
        for element in root.iter():
            if self._local_name(element.tag) == local_name:
                yield element

    def _local_name(self, tag: str) -> str:
        return tag.rsplit('}', maxsplit=1)[-1]

    def _request_ref(self) -> str:
        return str(uuid.uuid4())
