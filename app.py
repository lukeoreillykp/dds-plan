import hashlib
from datetime import date, datetime, time, timedelta

import pandas as pd
import streamlit as st

# Must be called before other code configures the Streamlit page.
st.set_page_config(
    page_title="DDS Transport Calendar",
    page_icon="🚚",
    layout="wide",
)

from streamlit_calendar import calendar

from config import (
    UK,
    BLUE,
    GREEN,
    RED,
    PURPLE,
    AMBER,
    DHL_COLOUR,
    SOURCE_COLOURS,
    SOURCE_LABELS,
    STATUS_OPTIONS,
    SENDER_REPO,
)

from parsers.campey import parse_campey_workbook
from parsers.bulk import parse_bulk_workbook
from parsers.dhl import parse_dhl_workbook

from services.events import (
    get_events,
    get_imported_hashes,
    save_event,
    update_event_status,
    update_campey_load_ref,
    supabase,
)

from services.imports import import_bookings
from services.sent_loads import get_sent_loads, sync_sent_loads
from services.calendar_events import decorate_events
from utils.dates import local_datetime


# ============================================================
# MAIN PAGE
# ============================================================

st.title("🚚 DDS Transport Calendar")
st.caption(
    "Shared calendar • Supabase bookings • Campey sent-load checking"
)

try:
    events = get_events()
except Exception as exc:
    st.error(f"Could not load bookings from Supabase: {exc}")
    st.stop()

now = datetime.now(UK)

github_error = None
sync_errors = []
match_save_errors = []

# ============================================================
# SYNC SAVED-LOAD CSVs INTO SUPABASE
# ============================================================

# Import any new CSVs from the sender repository into Supabase.
# Existing records should be left intact by sync_sent_loads().
try:
    sync_result = sync_sent_loads()

    if isinstance(sync_result, dict):
        sync_errors = sync_result.get("errors", [])

    # The sync may have inserted new records, so clear the cached
    # result before loading the saved-load list again.
    get_sent_loads.clear()

except Exception as exc:
    github_error = str(exc)


# ============================================================
# LOAD SENT-LOAD RECORDS
# ============================================================

try:
    sent_loads = get_sent_loads()
except Exception as exc:
    sent_loads = []
    if github_error:
        github_error = f"{github_error}; {exc}"
    else:
        github_error = str(exc)


# ============================================================
# MATCH CSVs TO CAMPEY BOOKINGS
# ============================================================

# decorate_events matches daily CSV loads to Campey bookings and
# creates virtual calendar events for CSVs without a matching booking.
display_events = decorate_events(events, sent_loads, now)

# Persist new matches in Supabase. Existing saved references are not
# overwritten, and virtual extra CSV entries are never saved as bookings.
for event in display_events:
    if event.get("source") != "Campey":
        continue

    if event.get("_is_extra", False):
        continue

    if not event.get("_sent", False):
        continue

    # Preserve any association already saved in Supabase.
    if event.get("campey_load_ref"):
        continue

    event_id = event.get("id")
    load_ref = str(event.get("_load_ref") or "").strip()

    if not event_id or not load_ref:
        continue

    try:
        update_campey_load_ref(event_id, load_ref)
        event["campey_load_ref"] = load_ref

    except Exception as exc:
        match_save_errors.append(
            f"{event.get('title', event_id)}: {exc}"
        )


# Reload bookings after saving references so the current page uses the
# persisted associations, rather than relying only on in-memory values.
try:
    events = get_events()
    display_events = decorate_events(events, sent_loads, now)

except Exception as exc:
    st.warning(
        f"Could not reload saved Campey matches: {exc}"
    )


# ============================================================
# SYNC AND MATCHING WARNINGS
# ============================================================

if github_error:
    st.warning(
        "Could not sync or read all saved-load CSV data. "
        "Campey matching and extra entries may be out of date. "
        f"Details: {github_error}"
    )

if sync_errors:
    with st.expander("⚠️ CSV sync issues"):
        for error in sync_errors:
            st.warning(error)

if match_save_errors:
    st.warning(
        "Some Campey matches could not be saved to Supabase. "
        "Those matches may be recalculated on refresh."
    )

    with st.expander("Match-save errors"):
        for error in match_save_errors:
            st.warning(error)


# ============================================================
# CAMPEY COLLECTION COUNTS
# ============================================================

# Exclude virtual extra CSV entries when counting scheduled bookings.
campey_bookings = [
    event
    for event in display_events
    if event.get("source") == "Campey"
    and not event.get("_is_extra", False)
]

# Matched CSV collections and unmatched extra CSVs are both actual
# collections. The extra entries are generated by decorate_events().
actual_collections = sum(
    1
    for event in display_events
    if event.get("source") == "Campey"
    and event.get("_sent", False)
)

extra_count = sum(
    1
    for event in display_events
    if event.get("source") == "Campey"
    and event.get("_is_extra", False)
)

cancelled_count = sum(
    1
    for event in campey_bookings
    if event.get("status", "scheduled") == "cancelled"
)

# A booking only counts as missed after its scheduled day has passed.
missed_count = sum(
    1
    for event in campey_bookings
    if event.get("status", "scheduled") != "cancelled"
    and not event.get("_sent", False)
    and (local_datetime(event.get("start")) or now).date() < now.date()
)

expected_count = sum(
    1
    for event in campey_bookings
    if event.get("status", "scheduled") != "cancelled"
)

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Expected Campey", expected_count)
m2.metric("Actual collections", actual_collections)
m3.metric("Missed", missed_count)
m4.metric("Cancelled", cancelled_count)
m5.metric("Extra CSVs", extra_count)


# ============================================================
# CALENDAR COLOUR KEY
# ============================================================

with st.expander("Calendar colour key"):
    st.markdown(
        f'<span style="color:{BLUE}">■</span> Upcoming Campey &nbsp; '
        f'<span style="color:{GREEN}">■</span> Complete / CSV sent / extra CSV &nbsp; '
        f'<span style="color:{RED}">■</span> Cancelled / missed Campey &nbsp; '
        f'<span style="color:{AMBER}">■</span> Bulk Inbound &nbsp; '
        f'<span style="color:{PURPLE}">■</span> Manual &nbsp; '
        f'<span style="color:{DHL_COLOUR}">■</span> DHL Collection',
        unsafe_allow_html=True,
    )


# ============================================================
# CALENDAR SOURCE FILTER
# ============================================================

st.subheader("Filter calendar")

selected_sources = st.multiselect(
    "Show booking sources",
    options=["Bulk", "Campey", "DHL", "Manual"],
    default=["Bulk", "Campey", "DHL", "Manual"],
    format_func=lambda source: {
        "Bulk": "Bulk",
        "Campey": "Campey",
        "DHL": "DHL",
        "Manual": "Manual",
    }[source],
    help=(
        "Choose one or more sources to display. "
        "Clear the selection to show no bookings."
    ),
    key="calendar_source_filter",
)


# ============================================================
# PREPARE FILTERED CALENDAR EVENTS
# ============================================================

calendar_events = []

for event in display_events:
    if event.get("source", "Manual") not in selected_sources:
        continue

    start_dt = local_datetime(event.get("start"))

    if not start_dt:
        continue

    # Extra CSV events already have a one-hour end time.
    # Other events without an end also default to one hour.
    end_dt = (
        local_datetime(event.get("end"))
        or start_dt + timedelta(hours=1)
    )

    colour = event.get("_display_colour", PURPLE)

    calendar_events.append({
        "id": str(event.get("id", "")),
        "title": event.get(
            "_display_title",
            event.get("title", ""),
        ),
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "backgroundColor": colour,
        "borderColor": colour,
        "extendedProps": {
            "source": event.get("source", "Manual"),
            "sourceLabel": SOURCE_LABELS.get(
                event.get("source", "Manual"),
                event.get("source", "Manual"),
            ),
            "originalTitle": event.get("title", ""),
            "status": event.get("status", "scheduled"),
            "loadHeader": event.get("load_header") or "",
            "sent": event.get("_sent", False),
            "sentFilename": event.get("_sent_filename", ""),
            "isExtra": event.get("_is_extra", False),
            "loadRef": event.get("_load_ref", ""),
        },
    })


# ============================================================
# CALENDAR WITH EVENT-CLICK STATUS CONTROLS
# ============================================================

calendar_result = calendar(
    events=calendar_events,
    options={
        "initialView": "dayGridMonth",
        "headerToolbar": {
            "left": "prev,next today",
            "center": "title",
            "right": (
                "dayGridMonth,timeGridWeek,timeGridDay,listMonth"
            ),
        },
        "editable": False,
        "selectable": False,
        "nowIndicator": True,
        "height": 760,
        "eventDisplay": "block",
        "dayMaxEvents": True,
        "slotMinTime": "00:00:00",
        "slotMaxTime": "24:00:00",
        "firstDay": 1,
        "timeZone": "local",
    },
    callbacks=["eventClick"],
    key="dds_transport_calendar",
)

if isinstance(calendar_result, dict):
    clicked_event = (
        calendar_result.get("eventClick", {})
        .get("event", {})
    )

    clicked_event_id = clicked_event.get("id")

    if clicked_event_id:
        clicked_event_id = str(clicked_event_id)

        clicked_booking = next(
            (
                event
                for event in events
                if str(event.get("id", "")) == clicked_event_id
            ),
            None,
        )

        # Virtual extra CSV entries are not database bookings, so
        # they must not open the ordinary booking status editor.
        if clicked_booking and clicked_booking.get("source") in (
            "Bulk",
            "DHL",
        ):
            st.session_state["selected_status_event_id"] = (
                clicked_event_id
            )


# ============================================================
# STATUS PANEL FOR BULK AND DHL
# ============================================================

selected_status_id = st.session_state.get(
    "selected_status_event_id"
)

selected_status_event = next(
    (
        event
        for event in events
        if str(event.get("id", "")) == str(selected_status_id)
    ),
    None,
)

if selected_status_event and selected_status_event.get("source") in (
    "Bulk",
    "DHL",
):
    status_source = selected_status_event.get("source")
    status_title = selected_status_event.get("title", "(untitled)")
    current_status = selected_status_event.get("status", "scheduled")

    if current_status not in STATUS_OPTIONS:
        current_status = "scheduled"

    status_start = local_datetime(
        selected_status_event.get("start")
    )
    status_date = (
        status_start.strftime("%d/%m/%Y %H:%M")
        if status_start
        else "Date not available"
    )

    st.subheader("📋 Update booking status")
    st.write(f"**Booking:** {status_title}")
    st.write(
        f"**Source:** "
        f"{SOURCE_LABELS.get(status_source, status_source)}"
    )
    st.write(f"**Date/time:** {status_date}")

    with st.form("update_booking_status"):
        new_status = st.selectbox(
            "Status",
            STATUS_OPTIONS,
            index=STATUS_OPTIONS.index(current_status),
            format_func=lambda value: {
                "scheduled": "Scheduled",
                "complete": "Complete — green",
                "cancelled": "Cancelled — red",
            }[value],
        )

        load_header_value = ""

        if status_source == "DHL":
            load_header_value = st.text_input(
                "DHL load header",
                value=selected_status_event.get("load_header") or "",
                placeholder="Enter the load header/reference",
                help=(
                    "Required when marking a DHL collection as complete."
                ),
            )

        save_status_clicked = st.form_submit_button(
            "Save status",
            type="primary",
        )

        if save_status_clicked:
            if (
                status_source == "DHL"
                and new_status == "complete"
                and not load_header_value.strip()
            ):
                st.error(
                    "Please enter the DHL load header before marking "
                    "this collection as complete."
                )
            else:
                try:
                    update_event_status(
                        selected_status_event["id"],
                        new_status,
                        (
                            load_header_value
                            if status_source == "DHL"
                            else None
                        ),
                    )

                    st.session_state.pop(
                        "selected_status_event_id",
                        None,
                    )

                    st.success(f"Booking marked as {new_status}.")
                    st.rerun()

                except Exception as exc:
                    st.error(
                        f"Could not update booking status: {exc}"
                    )

    if st.button(
        "Close status panel",
        key="close_status_panel",
    ):
        st.session_state.pop("selected_status_event_id", None)
        st.rerun()

elif selected_status_id:
    st.session_state.pop("selected_status_event_id", None)


# ============================================================
# ADD A BOOKING
# ============================================================

with st.expander("➕ Add a booking"):
    with st.form("add_booking", clear_on_submit=True):
        title = st.text_input("Booking / delivery details")

        c1, c2 = st.columns(2)
        booking_date = c1.date_input("Date", value=date.today())
        booking_time = c2.time_input(
            "Planned time",
            value=time(8, 0),
        )

        c3, c4 = st.columns(2)
        source_label = c3.selectbox(
            "Source",
            ["Manual", "Campey", "Bulk Inbound", "DHL Collection"],
        )
        duration = c4.number_input(
            "Duration (hours)",
            min_value=0.25,
            max_value=12.0,
            value=1.0,
            step=0.25,
        )

        add_clicked = st.form_submit_button(
            "Add booking",
            type="primary",
        )

        if add_clicked:
            if not title.strip():
                st.error("Please enter booking details.")
            else:
                start_dt = datetime.combine(
                    booking_date,
                    booking_time,
                ).replace(tzinfo=UK)

                source = {
                    "Bulk Inbound": "Bulk",
                    "DHL Collection": "DHL",
                }.get(source_label, source_label)

                try:
                    save_event(
                        title,
                        start_dt,
                        start_dt + timedelta(hours=float(duration)),
                        source,
                    )
                    st.success("Booking added.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not add booking: {exc}")


# ============================================================
# SHARED IMPORT HELPER UI
# ============================================================

def show_imported_hash_message(file_hash):
    """Show whether this exact workbook was imported before."""
    try:
        if file_hash in get_imported_hashes():
            st.info(
                "This workbook was imported previously. Re-scanning "
                "can add missing bookings; existing matching bookings "
                "will be skipped."
            )
    except Exception as exc:
        st.warning(
            f"Could not check the import history: {exc}"
        )


# ============================================================
# IMPORT CAMPEY WORKBOOK
# ============================================================

with st.expander("📥 Import Campey transport workbook"):
    campey_file = st.file_uploader(
        "Select the Campey Excel workbook",
        type=["xlsx", "xls"],
        key="campey_excel",
    )

    if campey_file is not None:
        file_bytes = campey_file.getvalue()
        file_hash = hashlib.sha256(file_bytes).hexdigest()

        show_imported_hash_message(file_hash)

        try:
            parsed = parse_campey_workbook(file_bytes)
            st.write(f"Found **{len(parsed)}** Campey bookings.")

            if parsed:
                st.dataframe(
                    pd.DataFrame([
                        {
                            "Date": row["start"].strftime("%d/%m/%Y"),
                            "Planned time": row["start"].strftime("%H:%M"),
                            "Del Site": row["title"],
                        }
                        for row in parsed
                    ]),
                    use_container_width=True,
                    hide_index=True,
                )

                if st.button(
                    "Import / re-scan Campey bookings",
                    type="primary",
                    key="import_campey",
                ):
                    try:
                        count = import_bookings(
                            parsed,
                            file_hash,
                            campey_file.name,
                        )
                        st.success(
                            f"Added {count} new Campey bookings. "
                            "Matching existing bookings were skipped."
                        )
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Import failed: {exc}")
            else:
                st.warning(
                    "No bookings found. Check the Date, Planned Time "
                    "and Del Site columns."
                )

        except Exception as exc:
            st.error(f"Could not read the Campey workbook: {exc}")


# ============================================================
# IMPORT BULK INBOUND WORKBOOK
# ============================================================

with st.expander("📦 Import Bulk Inbound schedule"):
    st.write(
        "Each timed row becomes a booking. Bulk events can be marked "
        "complete or cancelled from the calendar."
    )

    bulk_file = st.file_uploader(
        "Select the Bulk Schedule Excel workbook",
        type=["xlsx", "xls"],
        key="bulk_excel",
    )

    if bulk_file is not None:
        bulk_bytes = bulk_file.getvalue()
        bulk_hash = hashlib.sha256(bulk_bytes).hexdigest()

        show_imported_hash_message(bulk_hash)

        try:
            parsed_bulk = parse_bulk_workbook(bulk_bytes)

            st.write(
                f"Found **{len(parsed_bulk)}** timed Bulk Inbound entries."
            )

            if parsed_bulk:
                st.dataframe(
                    pd.DataFrame([
                        {
                            "Date": row["start"].strftime("%d/%m/%Y"),
                            "Time": row["start"].strftime("%H:%M"),
                            "Bulk Inbound details": row["title"],
                        }
                        for row in parsed_bulk
                    ]),
                    use_container_width=True,
                    hide_index=True,
                )

                if st.button(
                    "Import / re-scan Bulk Inbound bookings",
                    type="primary",
                    key="import_bulk",
                ):
                    try:
                        count = import_bookings(
                            parsed_bulk,
                            bulk_hash,
                            bulk_file.name,
                        )
                        st.success(
                            f"Added {count} new Bulk Inbound entries. "
                            "Existing matching entries were skipped."
                        )
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Bulk import failed: {exc}")
            else:
                st.warning(
                    "No timed entries found. Check the weekly tabs, "
                    "dates, times and booking details."
                )

        except Exception as exc:
            st.error(
                f"Could not read the Bulk Schedule workbook: {exc}"
            )


# ============================================================
# IMPORT DHL PONTEFRACT COLLECTIONS
# ============================================================

with st.expander("🚚 Bulk import DHL collections — Pontefract"):
    st.write(
        "Every worksheet is treated as one week. The week date is "
        "read from the worksheet header. Only Pontefract collection "
        "slots are imported. Review the weekly counts before importing."
    )

    dhl_file = st.file_uploader(
        "Select the Factory Movements workbook",
        type=["xlsx", "xls"],
        key="dhl_excel",
    )

    if dhl_file is not None:
        dhl_bytes = dhl_file.getvalue()
        dhl_hash = hashlib.sha256(dhl_bytes).hexdigest()

        show_imported_hash_message(dhl_hash)

        try:
            parsed_dhl = parse_dhl_workbook(dhl_bytes)

            if parsed_dhl:
                weekly_counts = {}

                for item in parsed_dhl:
                    collection_date = item["start"].date()
                    week_start = collection_date - timedelta(
                        days=(collection_date.weekday() + 1) % 7
                    )

                    weekly_counts[week_start] = (
                        weekly_counts.get(week_start, 0) + 1
                    )

                preview_rows = [
                    {
                        "Week commencing": week_start.strftime(
                            "%d/%m/%Y"
                        ),
                        "Collections found": count,
                    }
                    for week_start, count in sorted(
                        weekly_counts.items()
                    )
                ]

                st.write(
                    f"**{len(parsed_dhl)} collection slots found "
                    f"across {len(weekly_counts)} weeks.**"
                )

                st.dataframe(
                    pd.DataFrame(preview_rows),
                    use_container_width=True,
                    hide_index=True,
                )

                if st.button(
                    "Bulk import / re-scan DHL collections",
                    type="primary",
                    key="import_dhl",
                ):
                    try:
                        added_count = import_bookings(
                            parsed_dhl,
                            dhl_hash,
                            dhl_file.name,
                        )

                        skipped_count = len(parsed_dhl) - added_count

                        st.success(
                            f"Import complete: {added_count} added · "
                            f"{skipped_count} already existed · "
                            f"{len(weekly_counts)} weeks processed."
                        )
                        st.rerun()

                    except Exception as exc:
                        st.error(f"DHL import failed: {exc}")

            else:
                st.warning(
                    "No DHL collection slots were found. Check that "
                    "each worksheet has its week date in the header "
                    "area, weekday headings, hourly columns, and "
                    "PONTEFRACT rows."
                )

        except Exception as exc:
            st.error(f"Could not parse the DHL workbook: {exc}")


# ============================================================
# EDIT OR DELETE BOOKINGS
# ============================================================

with st.expander("✏️ Edit or delete a booking"):
    if not events:
        st.info("There are no bookings to edit.")
    else:
        choices = {}

        for event in events:
            start_dt = local_datetime(event.get("start"))
            source_label = SOURCE_LABELS.get(
                event.get("source", "Manual"),
                event.get("source", "Manual"),
            )

            if start_dt:
                label = (
                    f"{start_dt:%d/%m/%Y %H:%M} — "
                    f"{event.get('title', '(untitled)')} "
                    f"[{source_label}]"
                )
            else:
                label = event.get("title", "(untitled)")

            choices[f"{label} · {event['id'][:8]}"] = event

        selected_label = st.selectbox(
            "Choose a booking",
            list(choices.keys()),
        )

        selected = choices[selected_label]
        selected_start = local_datetime(
            selected.get("start")
        ) or now
        selected_end = (
            local_datetime(selected.get("end"))
            or selected_start + timedelta(hours=1)
        )

        with st.form("edit_booking"):
            edit_title = st.text_input(
                "Booking / delivery details",
                value=selected.get("title", ""),
            )

            ec1, ec2 = st.columns(2)
            edit_date = ec1.date_input(
                "Date",
                value=selected_start.date(),
                key="edit_date",
            )
            edit_time = ec2.time_input(
                "Planned time",
                value=selected_start.time().replace(tzinfo=None),
                key="edit_time",
            )

            ec3, ec4 = st.columns(2)
            source_options = [
                "Manual",
                "Campey",
                "Bulk Inbound",
                "DHL Collection",
            ]

            current_source = selected.get("source", "Manual")
            current_label = SOURCE_LABELS.get(
                current_source,
                current_source,
            )

            edit_source_label = ec3.selectbox(
                "Source",
                source_options,
                index=(
                    source_options.index(current_label)
                    if current_label in source_options
                    else 0
                ),
                key="edit_source",
            )

            old_duration = (
                selected_end - selected_start
            ).total_seconds() / 3600

            edit_duration = ec4.number_input(
                "Duration (hours)",
                min_value=0.25,
                max_value=12.0,
                value=max(0.25, min(12.0, old_duration)),
                step=0.25,
                key="edit_duration",
            )

            save_clicked = st.form_submit_button(
                "Save changes",
                type="primary",
            )

            if save_clicked:
                if not edit_title.strip():
                    st.error("Please enter booking details.")
                else:
                    new_start = datetime.combine(
                        edit_date,
                        edit_time,
                    ).replace(tzinfo=UK)

                    new_source = {
                        "Bulk Inbound": "Bulk",
                        "DHL Collection": "DHL",
                    }.get(edit_source_label, edit_source_label)

                    try:
                        save_event(
                            edit_title,
                            new_start,
                            new_start + timedelta(
                                hours=float(edit_duration)
                            ),
                            new_source,
                            event_id=selected["id"],
                        )
                        st.success("Booking updated.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not update booking: {exc}")

        confirm_delete = st.checkbox(
            "I understand this permanently deletes the selected booking",
            key="confirm_delete_booking",
        )

        if st.button(
            "Delete selected booking",
            disabled=not confirm_delete,
        ):
            try:
                supabase.table("events").delete().eq(
                    "id",
                    selected["id"],
                ).execute()

                st.success("Booking deleted.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not delete booking: {exc}")


# ============================================================
# CAMPEY SENT-LOAD DETAILS
# ============================================================

with st.expander("📄 Campey sent-load CSV details"):
    st.caption(
        f"Read-only source: {SENDER_REPO}/saved_loads. "
        "CSV data is cached for 60 seconds."
    )

    st.write(f"CSV loads found: **{len(sent_loads)}**")

    if sent_loads:
        st.dataframe(
            pd.DataFrame([
                {
                    "Load Ref": load["load_ref"],
                    "Sent date/time": load["sent_at"].strftime(
                        "%d/%m/%Y %H:%M"
                    ),
                    "CSV file": load["filename"],
                }
                for load in sent_loads
            ]),
            use_container_width=True,
            hide_index=True,
        )

    st.caption(
        "Campey bookings are matched against sent-load CSVs. "
        "Unmatched CSVs appear as extra one-hour calendar entries. "
        "Bulk and DHL bookings can be marked complete or cancelled."
    )


st.caption(
    "Bookings are shared through Supabase. Refresh the page to load "
    "changes made by other users."
)