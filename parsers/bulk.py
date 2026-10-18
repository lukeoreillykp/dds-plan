
import io
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from utils.dates import parse_date, parse_time
from utils.text import clean_cell, make_booking_key


UK = ZoneInfo("Europe/London")
AMBER = "#e0a126"


def parse_bulk_workbook(file_bytes):
    """
    Each weekday uses a four-column group:
    time | supplier/details | product/code | quantity/extra details.

    The date is stored in row 0 of the second column in each group.
    """
    workbook = pd.ExcelFile(io.BytesIO(file_bytes))
    bookings = {}

    for sheet_name in workbook.sheet_names:
        if sheet_name.strip().lower() in {"maris", "rejected"}:
            continue

        raw = pd.read_excel(
            workbook,
            sheet_name=sheet_name,
            header=None,
        )

        if raw.empty or len(raw.index) < 4:
            continue

        for base_col in (0, 4, 8, 12, 16):
            date_col = base_col + 1

            if date_col >= raw.shape[1]:
                continue

            booking_date = parse_date(raw.iat[0, date_col])

            if booking_date is None:
                continue

            for row_index in range(3, len(raw.index)):
                booking_time = parse_time(raw.iat[row_index, base_col])

                if booking_time is None:
                    continue

                details = []

                for col_index in range(
                    base_col + 1,
                    min(base_col + 4, raw.shape[1]),
                ):
                    value = clean_cell(raw.iat[row_index, col_index])

                    if value:
                        details.append(value)

                if not details:
                    continue

                title = " · ".join(details)
                title_lower = title.casefold()

                if any(
                    phrase in title_lower
                    for phrase in (
                        "no deliveries",
                        "bank holiday",
                        "engagement day",
                    )
                ):
                    continue

                start_dt = datetime.combine(
                    booking_date, booking_time
                ).replace(tzinfo=UK)

                key = make_booking_key(title, start_dt, "Bulk")

                bookings[key] = {
                    "title": title,
                    "start": start_dt,
                    "end": start_dt + timedelta(hours=1),
                    "source": "Bulk",
                    "colour": AMBER,
                }

    return list(bookings.values())
