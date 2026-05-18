def money_to_minor_units(value: str | int | float) -> int:
    text = str(value).strip().replace(' ', '').replace('\xa0', '').replace(',', '.')
    if not text:
        raise ValueError('Money value is empty')

    sign = -1 if text.startswith('-') else 1
    text = text.lstrip('+-')

    if '.' in text:
        whole_part, fractional_part = text.split('.', maxsplit=1)
    else:
        whole_part, fractional_part = text, ''

    whole = int(whole_part or '0')
    fractional = int((fractional_part + '00')[:2])
    return sign * (whole * 100 + fractional)


def minor_units_to_sheet_value(value: int | None) -> float | str:
    if value is None:
        return ''
    return value / 100
