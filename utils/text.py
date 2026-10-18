
import pandas as pd


def clean_cell(value):
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass

    if isinstance(value, float) and value.is_integer():
        return str(int(value))

    return str(value).strip()


def make_booking_key(title, start_dt, source):
    return (
        str(title).strip().casefold(),
        start_dt.isoformat(),
        source,
    )
