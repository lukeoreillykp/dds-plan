
import uuid

from config import PURPLE, SOURCE_COLOURS
from services.events import get_events, remember_import, supabase
from utils.dates import local_datetime
from utils.text import make_booking_key


def import_bookings(parsed, file_hash, filename):
    """
    Insert new parsed bookings, skip existing duplicates, and record
    the imported file hash to prevent repeated file imports.
    """
    existing = get_events()
    existing_keys = set()

    for event in existing:
        start_dt = local_datetime(event.get("start"))

        if start_dt:
            existing_keys.add(
                make_booking_key(
                    event.get("title", ""),
                    start_dt,
                    event.get("source", "Manual"),
                )
            )

    rows_to_insert = []

    for row in parsed:
        source = row["source"]

        key = make_booking_key(
            row["title"],
            row["start"],
            source,
        )

        if key in existing_keys:
            continue

        rows_to_insert.append({
            "id": str(uuid.uuid4()),
            "title": row["title"],
            "start": row["start"].isoformat(),
            "end": (
                row["end"].isoformat()
                if row.get("end") else None
            ),
            "source": source,
            "colour": SOURCE_COLOURS.get(source, PURPLE),
            "status": "scheduled",
        })

        # Also prevents duplicates within the same uploaded file.
        existing_keys.add(key)

    for offset in range(0, len(rows_to_insert), 100):
        supabase.table("events").insert(
            rows_to_insert[offset:offset + 100]
        ).execute()

    remember_import(file_hash, filename)
    return len(rows_to_insert)
