from datetime import date, datetime

GROUP_SEPARATOR = "\u00a0"
NO_VALUE = "\u2014"


def group_number(value, decimals=None):
    if value is None:
        return NO_VALUE
    if isinstance(value, str):
        stripped = value.strip()
        if decimals is not None:
            try:
                value = float(stripped)
            except ValueError:
                return value
        else:
            value = stripped
    text = f"{value:.{decimals}f}" if decimals is not None else str(value)
    sign = ""
    if text[:1] in ("-", "+"):
        sign, text = text[0], text[1:]
    whole, dot, fraction = text.partition(".")
    if not whole.isdigit():
        return str(value)
    chunks = []
    while len(whole) > 3:
        chunks.insert(0, whole[-3:])
        whole = whole[:-3]
    chunks.insert(0, whole)
    tail = dot + fraction if dot else ""
    return sign + GROUP_SEPARATOR.join(chunks) + tail


def human_date(value):
    if value is None:
        return NO_VALUE
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    return str(value)
