
from config import BLUE, GREEN, PURPLE, RED, SOURCE_COLOURS
from utils.dates import local_datetime


def decorate_events(events, sent_loads, now):
    """
    Campey keeps its original sent/overdue colouring.
    Bulk and DHL use saved status colours.
    """
    loads_by_day = {}

    for load in sent_loads:
        loads_by_day.setdefault(
            load["sent_at"].date(), []
        ).append(load)

    campey_by_day = {}

    for event in events:
        if event.get("source", "Manual") != "Campey":
            continue

        start_dt = local_datetime(event.get("start"))

        if start_dt:
            campey_by_day.setdefault(
                start_dt.date(), []
            ).append((start_dt, event))

    decorated = []

    # Campey behaviour intentionally unchanged.
    for day, bookings in campey_by_day.items():
        bookings.sort(
            key=lambda pair: (
                pair[0],
                str(pair[1].get("id", "")),
            )
        )

        day_loads = loads_by_day.get(day, [])

        for index, (start_dt, event) in enumerate(bookings):
            item = dict(event)

            if index < len(day_loads):
                load = day_loads[index]
                item["_display_title"] = load["load_ref"]
                item["_display_colour"] = GREEN
                item["_sent"] = True
                item["_sent_filename"] = load["filename"]
            else:
                item["_display_title"] = event.get("title", "")
                item["_sent"] = False
                item["_sent_filename"] = ""
                item["_display_colour"] = (
                    RED if start_dt < now else BLUE
                )

            decorated.append(item)

    # Non-Campey sources.
    for event in events:
        if event.get("source", "Manual") == "Campey":
            continue

        item = dict(event)
        source = item.get("source", "Manual")
        status = item.get("status", "scheduled")

        item["_sent"] = False
        item["_sent_filename"] = ""

        display_title = item.get("title", "")

        # Show the saved DHL load header on the calendar.
        if source == "DHL" and item.get("load_header"):
            display_title = (
                f"{display_title} · {item['load_header']}"
            )

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
