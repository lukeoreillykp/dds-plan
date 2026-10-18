
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd


UK = ZoneInfo("Europe/London")


def local_datetime(value):
    """Convert a value to a UK-localised datetime, or return None."""
    if value is None or value == "":
        return None

    try:
        result = pd.to_datetime(value, errors="coerce")

        if pd.isna(result):
            return None

        result = result.to_pydatetime()

        if result.tzinfo is None:
            return result.replace(tzinfo=UK)

        return result.astimezone(UK)

    except Exception:
        return None


def parse_date(value):
    """Parse dates using UK day-first conventions."""
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    try:
        if isinstance(value, (datetime, date, pd.Timestamp)):
            return pd.Timestamp(value).date()

        result = pd.to_datetime(
            value,
            dayfirst=True,
            errors="coerce",
        )

        return None if pd.isna(result) else result.date()

    except Exception:
        return None


def parse_time(value):
    """
    Supports Excel time fractions, whole hours, decimal-style times
    such as 13.3 = 13:30, and text times such as 08:30.
    """
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    if isinstance(value, time):
        return value.replace(tzinfo=None)

    if isinstance(value, (datetime, pd.Timestamp)):
        return value.time().replace(tzinfo=None)

    try:
        number = float(value)

        if 0 <= number < 1:
            seconds = round(number * 86400)
            return (
                datetime.min + timedelta(seconds=seconds)
            ).time()

        if 0 <= number < 24:
            if number.is_integer():
                return time(int(number), 0)

            text = str(value).strip()

            if "." in text:
                whole, decimal = text.split(".", 1)
                hour = int(whole)

                if len(decimal) == 1:
                    minute = int(decimal) * 10
                else:
                    minute = int(decimal[:2].ljust(2, "0"))

                if 0 <= hour < 24 and 0 <= minute < 60:
                    return time(hour, minute)

    except (ValueError, TypeError, OverflowError):
        pass

    try:
        text = str(value).strip()

        match = re.fullmatch(r"(\d{1,2})[:.](\d{2})", text)

        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))

            if 0 <= hour < 24 and 0 <= minute < 60:
                return time(hour, minute)

        result = pd.to_datetime(text, errors="coerce")

        if not pd.isna(result):
            return result.to_pydatetime().time().replace(tzinfo=None)

    except Exception:
        pass

    return None
