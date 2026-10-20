
from datetime import timedelta
import hashlib

from config import GREEN, PURPLE, RED, SOURCE_COLOURS
from utils.dates import local_datetime


def _event_day(event):
    """Return the event's local calendar date, or None if unavailable."""
    start_dt = local_datetime(event.get("start"))
    return start_dt.date() if start_dt else None


def _load_day(load):
    """Return the local calendar date for a sent CSV load."""
    sent_at = load.get("sent_at")
    if not sent_at:
        return None

    sent_at = local_datetime(sent_at)
    return sent_at.date() if sent_at else None


def _stable_extra_id(load):
    """Create a stable ID for a derived extra-CSV calendar entry."""
    identity = "|".join(
        (
            str(load.get("filename", "")),
            str(load.get("load_ref", "")),
            str(load.get("sent_at", "")),
        )
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"extra-csv-{digest}"


def _status(event):
    """Return a normalised event status."""
    return str(event.get("status") or "scheduled").strip().lower()


def decorate_events(events, sent_loads, now):
    """
    Decorate calendar events with collection matching and display colours.

    Campey matching rules:
    - Cancelled bookings are red and do not consume a CSV load.
    - Existing campey_load_ref associations are honoured when possible.
    - Remaining CSV loads are matched to remaining bookings chronologically.
    - Unmatched bookings from previous days are marked red.
    - Unmatched CSV loads become separate one-hour EXTRA events.

    Extra events are derived from CSV data and are not saved as database
    bookings by this function.
    """
    decorated = []

    # Group sent CSV loads by their local send date.
    loads_by_day = {}
    for load in sent_loads or []:
        sent_at = load.get("sent_at")
        load_ref = str(load.get("load_ref") or "").strip()

        if not sent_at or not load_ref:
            continue

        sent_at = local_datetime(sent_at)
        if not sent_at:
            continue

        normalised_load = dict(load)
        normalised_load["sent_at"] = sent_at
        normalised_load["load_ref"] = load_ref

        loads_by_day.setdefault(sent_at.date(), []).append(normalised_load)

    # Group Campey bookings by their local calendar date.
    campey_by_day = {}
    non_campey_events = []

    for event in events or []:
        if event.get("source", "Manual") == "Campey":
            day = _event_day(event)
            if day is not None:
                campey_by_day.setdefault(day, []).append(event)
            else:
                # Keep events with missing/invalid start dates visible.
                item = dict(event)
                item["_display_title"] = item.get("title", "")
                item["_display_colour"] = (
                    RED if _status(item) == "cancelled"
                    else item.get("colour")
                    or SOURCE_COLOURS.get("Campey", GREEN)
                )
                item["_sent"] = False
                item["_sent_filename"] = ""
                item["_is_extra"] = False
                decorated.append(item)
        else:
            non_campey_events.append(event)

    # Process each day with either Campey bookings or sent CSV loads.
    all_days = sorted(set(campey_by_day) | set(loads_by_day))

    for day in all_days:
        bookings = sorted(
            campey_by_day.get(day, []),
            key=lambda event: (
                local_datetime(event.get("start")),
                str(event.get("id", "")),
            ),
        )

        day_loads = sorted(
            loads_by_day.get(day, []),
            key=lambda load: (
                load["sent_at"],
                str(load.get("filename", "")),
                load["load_ref"],
            ),
        )

        # Track which CSV loads have already been matched.
        used_load_indexes = set()
        matched_booking_ids = set()

        # First honour an existing association, if one is present.
        # This supports persistence added in services/events.py.
        for booking in bookings:
            if _status(booking) == "cancelled":
                continue

            existing_ref = str(
                booking.get("campey_load_ref") or ""
            ).strip()

            if not existing_ref:
                continue

            for index, load in enumerate(day_loads):
                if index in used_load_indexes:
                    continue

                if load["load_ref"] == existing_ref:
                    used_load_indexes.add(index)
                    matched_booking_ids.add(str(booking.get("id", "")))

                    item = dict(booking)
                    item["_display_title"] = existing_ref
                    item["_display_colour"] = GREEN
                    item["_sent"] = True
                    item["_sent_filename"] = load.get("filename", "")
                    item["_load_ref"] = existing_ref
                    item["_is_extra"] = False
                    decorated.append(item)
                    break

        # Match remaining bookings and CSV loads chronologically.
        for booking in bookings:
            if _status(booking) == "cancelled":
                item = dict(booking)
                item["_display_title"] = item.get("title", "")
                item["_display_colour"] = RED
                item["_sent"] = False
                item["_sent_filename"] = ""
                item["_cancelled"] = True
                item["_is_extra"] = False
                decorated.append(item)
                continue

            booking_id = str(booking.get("id", ""))

            if booking_id in matched_booking_ids:
                continue

            available_index = next(
                (
                    index
                    for index in range(len(day_loads))
                    if index not in used_load_indexes
                ),
                None,
            )

            item = dict(booking)

            if available_index is not None:
                load = day_loads[available_index]
                used_load_indexes.add(available_index)

                item["_display_title"] = load["load_ref"]
                item["_display_colour"] = GREEN
                item["_sent"] = True
                item["_sent_filename"] = load.get("filename", "")
                item["_load_ref"] = load["load_ref"]
                item["_is_extra"] = False
            else:
                item["_display_title"] = item.get("title", "")
                item["_sent"] = False
                item["_sent_filename"] = ""
                item["_is_extra"] = False

                # A booking is missed only after its scheduled calendar
                # day has passed, not simply because its start time passed.
                if day < now.date():
                    item["_display_colour"] = RED
                else:
                    item["_display_colour"] = (
                        item.get("colour")
                        or SOURCE_COLOURS.get("Campey", GREEN)
                    )

            decorated.append(item)

        # Any CSV loads left over are extra collections.
        for index, load in enumerate(day_loads):
            if index in used_load_indexes:
                continue

            sent_at = load["sent_at"]
            load_ref = load["load_ref"]
            filename = str(load.get("filename", ""))

            decorated.append(
                {
                    "id": _stable_extra_id(load),
                    "title": load_ref,
                    "start": sent_at.isoformat(),
                    "end": (sent_at + timedelta(hours=1)).isoformat(),
                    "source": "Campey",
                    "status": "complete",
                    "load_header": load_ref,
                    "colour": GREEN,
                    "_display_title": f"{load_ref} · EXTRA",
                    "_display_colour": GREEN,
                    "_sent": True,
                    "_sent_filename": filename,
                    "_load_ref": load_ref,
                    "_is_extra": True,
                }
            )

    # Preserve existing handling for non-Campey events.
    for event in non_campey_events:
        item = dict(event)
        source = item.get("source", "Manual")
        status = _status(item)

        item["_sent"] = False
        item["_sent_filename"] = ""
        item["_is_extra"] = False

        display_title = item.get("title", "")

        if source == "DHL" and item.get("load_header"):
            display_title = f"{display_title} · {item['load_header']}"

        item["_display_title"] = display_title

        if source in ("Bulk", "DHL") and status == "complete":
            item["_display_colour"] = GREEN
        elif source in ("Bulk", "DHL") and status == "cancelled":
            item["_display_colour"] = RED
        else:
            item["_display_colour"] = (
                item.get("colour")
                or SOURCE_COLOURS.get(source, PURPLE)
            )

        decorated.append(item)

    return decorated
