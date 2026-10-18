import io
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from utils.dates import parse_time
from utils.text import clean_cell, make_booking_key


UK = ZoneInfo("Europe/London")
DHL_COLOUR = "#159eaa"


def parse_dhl_header_date(value):
    """
    Parse a week date from the worksheet header area.
    UK day/month/year formats are tried before US formats.
    """
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    if isinstance(value, (datetime, date, pd.Timestamp)):
        return pd.Timestamp(value).date()

    if isinstance(value, (int, float)):
        if 20000 <= value <= 80000:
            try:
                return (
                    pd.Timestamp("1899-12-30")
                    + pd.to_timedelta(value, unit="D")
                ).date()
            except (TypeError, ValueError, OverflowError):
                return None
        return None

    text = clean_cell(value)
    if not text:
        return None

    for fmt in (
        "%d/%m/%Y",
        "%d/%m/%y",
        "%d-%m-%Y",
        "%d-%m-%y",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%m-%d-%Y",
        "%m-%d-%y",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    return None


def get_week_start_from_sheet(sheet_name):
    """Extract a week commencing date from a worksheet name."""
    match = re.search(
        r"W\s*\.?\s*C\s*\.?\s*[-:]?\s*(\d{8}|\d{6})",
        sheet_name,
        re.IGNORECASE,
    )

    if not match:
        return None

    value = match.group(1)

    if len(value) == 8:
        formats = ("%d%m%Y", "%Y%m%d")
    else:
        formats = ("%d%m%y", "%y%m%d")

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue

    return None


def is_one(value):
    """Return True only for a numeric cell whose value is exactly 1."""
    if value is None or isinstance(value, (str, bool)):
        return False

    try:
        if pd.isna(value):
            return False
        return float(value) == 1
    except (TypeError, ValueError):
        return False


def parse_dhl_workbook(file_bytes):
    """
    Parse weekly worksheets in the Factory Movements workbook.

    Only PONTEFRACT rows are imported. Hourly cells containing
    exactly numeric 1 create DHL Collection events. TOTAL columns
    and text values such as "1" are ignored.
    """
    workbook = pd.ExcelFile(io.BytesIO(file_bytes))
    bookings = {}

    weekday_offsets = {
        "SUN": 0,
        "MON": 1,
        "TUE": 2,
        "WED": 3,
        "THU": 4,
        "FRI": 5,
        "SAT": 6,
    }

    for sheet_name in workbook.sheet_names:
        raw = pd.read_excel(
            workbook,
            sheet_name=sheet_name,
            header=None,
        )

        if raw.empty or raw.shape[1] < 2:
            continue

        week_start = None

        # Find the week commencing date near the top of the sheet.
        for row_index in range(min(10, raw.shape[0])):
            for col_index in range(min(5, raw.shape[1])):
                candidate = parse_dhl_header_date(
                    raw.iat[row_index, col_index]
                )

                if candidate is not None:
                    week_start = candidate
                    break

            if week_start is not None:
                break

        if week_start is None:
            continue

        for header_row in range(len(raw.index)):
            first_cell = clean_cell(
                raw.iat[header_row, 0]
            ).upper()

            day_key = first_cell[:3]

            if day_key not in weekday_offsets:
                continue

            collection_date = week_start + timedelta(
                days=weekday_offsets[day_key]
            )

            hourly_columns = []

            for col in range(1, raw.shape[1]):
                heading = raw.iat[header_row, col]
                heading_text = clean_cell(heading).upper()

                if not heading_text or "TOTAL" in heading_text:
                    continue

                if parse_dhl_header_date(heading) is not None:
                    continue

                parsed_time = parse_time(heading)

                if parsed_time is None:
                    continue

                hourly_columns.append(
                    (col, time(parsed_time.hour, 0))
                )

            if not hourly_columns:
                continue

            for row_index in range(
                header_row + 1,
                len(raw.index),
            ):
                row_label = clean_cell(
                    raw.iat[row_index, 0]
                ).upper()

                # Stop when the next weekday section begins.
                if row_label[:3] in weekday_offsets:
                    break

                # Import only Pontefract rows.
                if not row_label.startswith("PONTEFRACT"):
                    continue

                for col, hour in hourly_columns:
                    if not is_one(raw.iat[row_index, col]):
                        continue

                    start_dt = datetime.combine(
                        collection_date,
                        hour,
                    ).replace(tzinfo=UK)

                    title = "DHL Collection - Pontefract"

                    key = make_booking_key(
                        title,
                        start_dt,
                        "DHL",
                    )

                    bookings[key] = {
                        "title": title,
                        "start": start_dt,
                        "end": start_dt + timedelta(hours=1),
                        "source": "DHL",
                        "colour": DHL_COLOUR,
                    }

    return sorted(
        bookings.values(),
        key=lambda item: item["start"],
    )