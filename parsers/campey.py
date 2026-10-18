
import io
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from utils.dates import parse_date, parse_time
from utils.text import make_booking_key


UK = ZoneInfo("Europe/London")
BLUE = "#2878c8"



def parse_campey_workbook(file_bytes):
    workbook = pd.ExcelFile(io.BytesIO(file_bytes))
    bookings = {}

    for sheet_name in workbook.sheet_names:
        raw = pd.read_excel(
            workbook,
            sheet_name=sheet_name,
            header=None,
        )

        header_row = None

        for index, row in raw.iterrows():
            values = [
                str(value).strip().lower()
                for value in row.tolist()
                if not pd.isna(value)
            ]

            has_date = any(
                value == "date" or value.startswith("date ")
                for value in values
            )
            has_time = any("planned time" in value for value in values)

            if has_date and has_time:
                header_row = index
                break

        if header_row is None:
            continue

        headers = [
            "" if pd.isna(value) else str(value).strip()
            for value in raw.iloc[header_row].tolist()
        ]

        data = raw.iloc[header_row + 1:].copy()
        data.columns = headers

        lookup = {
            str(column).strip().lower(): column
            for column in data.columns
        }

        date_col = next(
            (
                column for name, column in lookup.items()
                if name == "date" or name.startswith("date ")
            ),
            None,
        )

        time_col = next(
            (
                column for name, column in lookup.items()
                if "planned time" in name
            ),
            None,
        )

        site_col = next(
            (
                column for name, column in lookup.items()
                if name in ("del site", "delivery site", "site")
            ),
            None,
        )

        if date_col is None or time_col is None or site_col is None:
            continue

        for _, row in data.iterrows():
            booking_date = parse_date(row.get(date_col))
            booking_time = parse_time(row.get(time_col))
            site = row.get(site_col)

            if booking_date is None or booking_time is None:
                continue

            if site is None or pd.isna(site):
                continue

            site = str(site).strip()

            if not site or site.lower() in {"nan", "none", "del site"}:
                continue

            start_dt = datetime.combine(
                booking_date, booking_time
            ).replace(tzinfo=UK)

            key = make_booking_key(site, start_dt, "Campey")

            bookings[key] = {
                "title": site,
                "start": start_dt,
                "end": start_dt + timedelta(hours=1),
                "source": "Campey",
                "colour": BLUE,
            }

    return list(bookings.values())
