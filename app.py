
import hashlib
import re
import uuid
from datetime import date, datetime, time, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from streamlit_calendar import calendar
from supabase import create_client

st.set_page_config(
    page_title="Campey Transport Calendar",
    page_icon="📅",
    layout="wide",
)

UK_TZ = ZoneInfo("Europe/London")
SOURCE_OPTIONS = ["Campey", "Bulk", "Manual"]
SOURCE_COLOURS = {
    "Campey": "#2878B5",
    "Bulk": "#D97706",
    "Manual": "#64748B",
}


@st.cache_resource
def get_supabase():
    url = st.secrets.get("SUPABASE_URL", "").strip()
    key = st.secrets.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        return None
    return create_client(url, key)


def local_now():
    return datetime.now(UK_TZ)


def db_timestamp(value):
    if isinstance(value, str):
        value = pd.to_datetime(value, errors="raise").to_pydatetime()
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, time.min)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UK_TZ)
    return value.astimezone(UK_TZ).isoformat()


def as_local_datetime(value):
    if not value:
        return None
    result = pd.to_datetime(value, errors="coerce")
    if pd.isna(result):
        return None
    result = result.to_pydatetime()
    if result.tzinfo is None:
        result = result.replace(tzinfo=UK_TZ)
    return result.astimezone(UK_TZ)


def fetch_events(db):
    return (
        db.table("events")
        .select("*")
        .order("start")
        .execute()
        .data
        or []
    )


def insert_event(db, title, start_dt, end_dt=None, source="Manual"):
    source = source if source in SOURCE_OPTIONS else "Manual"
    db.table("events").insert({
        "id": str(uuid.uuid4()),
        "title": title.strip(),
        "start": db_timestamp(start_dt),
        "end": db_timestamp(end_dt) if end_dt else None,
        "colour": SOURCE_COLOURS[source],
        "source": source,
    }).execute()


def update_event(db, event_id, title, start_dt, end_dt=None, source="Manual"):
    source = source if source in SOURCE_OPTIONS else "Manual"
    db.table("events").update({
        "title": title.strip(),
        "start": db_timestamp(start_dt),
        "end": db_timestamp(end_dt) if end_dt else None,
        "colour": SOURCE_COLOURS[source],
        "source": source,
    }).eq("id", event_id).execute()


def delete_event(db, event_id):
    db.table("events").delete().eq("id", event_id).execute()


# Excel importer for the existing Campey Transport Requests workbook

def clean(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def normalise(value):
    return re.sub(r"\s+", " ", clean(value).lower()).strip()


def parse_excel_date(value):
    if value is None or clean(value) == "":
        return None
    try:
        parsed = pd.to_datetime(value, errors="coerce")
        return None if pd.isna(parsed) else parsed.date()
    except (TypeError, ValueError, OverflowError):
        return None


def parse_excel_time(value):
    if value is None or clean(value) == "":
        return None

    if isinstance(value, datetime):
        return value.time().replace(second=0, microsecond=0)
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    if isinstance(value, timedelta):
        minutes = int(value.total_seconds() // 60) % 1440
        return time(minutes // 60, minutes % 60)
    if isinstance(value, (int, float)) and 0 <= float(value) < 1:
        minutes = round(float(value) * 1440) % 1440
        return time(minutes // 60, minutes % 60)

    try:
        parsed = pd.to_datetime(str(value).strip(), errors="coerce")
        return None if pd.isna(parsed) else parsed.time().replace(
            second=0, microsecond=0
        )
    except (TypeError, ValueError, OverflowError):
        return None


def parse_workbook(content, filename):
    events = []
    warnings = []

    try:
        workbook = pd.ExcelFile(BytesIO(content))
    except Exception as exc:
        return [], [f"Could not open {filename}: {exc}"]

    for sheet_name in workbook.sheet_names:
        try:
            raw = pd.read_excel(
                workbook, sheet_name=sheet_name, header=None
            )

            # Find the actual header row instead of assuming row 1.
            header_row = None
            for row_index in range(min(len(raw), 25)):
                headings = {
                    normalise(value)
                    for value in raw.iloc[row_index].tolist()
                }
                if "date" in headings and "planned time" in headings:
                    header_row = row_index
                    break

            if header_row is None:
                warnings.append(
                    f"{sheet_name}: couldn't find Date and Planned Time "
                    "headings; skipped this sheet."
                )
                continue

            frame = pd.read_excel(
                workbook, sheet_name=sheet_name, header=header_row
            )

            columns = {}
            for column in frame.columns:
                key = normalise(column)
                if key and key not in columns:
                    columns[key] = column

            date_col = columns.get("date")
            time_col = columns.get("planned time")
            site_col = columns.get("del site")

            if date_col is None or time_col is None:
                warnings.append(
                    f"{sheet_name}: required columns are missing."
                )
                continue

            sheet_count = 0

            for _, row in frame.iterrows():
                booking_date = parse_excel_date(row.get(date_col))
                booking_time = parse_excel_time(row.get(time_col))

                # Ignore blank rows and summary sections without a date/time.
                if booking_date is None or booking_time is None:
                    continue

                site = clean(row.get(site_col)) if site_col else ""
                if not site:
                    site = "Transport booking"

                start_dt = datetime.combine(
                    booking_date, booking_time
                ).replace(tzinfo=UK_TZ)

                events.append({
                    "id": str(uuid.uuid4()),
                    "title": site,
                    "start": start_dt.isoformat(),
                    "end": None,
                    "colour": SOURCE_COLOURS["Campey"],
                    "source": "Campey",
                })
                sheet_count += 1

            if sheet_count == 0:
                warnings.append(
                    f"{sheet_name}: no valid bookings found."
                )

        except Exception as exc:
            warnings.append(
                f"{sheet_name}: couldn't read worksheet ({exc})."
            )

    return events, warnings


def import_workbook(db, uploaded_file):
    content = uploaded_file.getvalue()
    file_hash = hashlib.sha256(content).hexdigest()

    existing = (
        db.table("imported_files")
        .select("file_hash")
        .eq("file_hash", file_hash)
        .limit(1)
        .execute()
    )

    if existing.data:
        return 0, [
            "This exact file has already been imported. No duplicates added."
        ]

    events, warnings = parse_workbook(content, uploaded_file.name)

    if not events:
        return 0, warnings or ["No valid bookings were found."]

    # Save bookings first, then record the import hash.
    db.table("events").insert(events).execute()
    db.table("imported_files").insert({
        "file_hash": file_hash,
        "filename": uploaded_file.name,
        "imported_at": local_now().isoformat(),
    }).execute()

    return len(events), warnings


def to_calendar_event(event):
    colour = event.get("colour") or SOURCE_COLOURS.get(
        event.get("source", "Manual"), SOURCE_COLOURS["Manual"]
    )
    result = {
        "id": str(event.get("id", "")),
        "title": event.get("title") or "Untitled",
        "start": event.get("start"),
        "backgroundColor": colour,
        "borderColor": colour,
        "textColor": "#FFFFFF",
    }
    if event.get("end"):
        result["end"] = event["end"]
    return result


# Main application

st.title("Campey Transport Calendar")
st.caption("Shared transport bookings • UK time")

db = get_supabase()
if db is None:
    st.error(
        "Supabase secrets are missing. Open Streamlit app Settings → "
        "Secrets and configure SUPABASE_URL and "
        "SUPABASE_SERVICE_ROLE_KEY."
    )
    st.stop()

with st.sidebar:
    st.header("Calendar")
    selected_date = st.date_input(
        "Focus date", value=local_now().date()
    )
    view_mode = st.selectbox(
        "Calendar view",
        ["dayGridMonth", "timeGridWeek", "timeGridDay", "listWeek"],
        format_func=lambda value: {
            "dayGridMonth": "Month",
            "timeGridWeek": "Week",
            "timeGridDay": "Day",
            "listWeek": "List",
        }[value],
    )
    st.divider()
    st.markdown("**Event colours**")
    for source, colour in SOURCE_COLOURS.items():
        st.markdown(
            f'<span style="display:inline-block;width:10px;height:10px;'
            f'background:{colour};border-radius:50%;margin-right:7px;"></span>'
            f'{source}',
            unsafe_allow_html=True,
        )

try:
    events = fetch_events(db)
except Exception as exc:
    st.error(f"Couldn't load bookings from Supabase: {exc}")
    st.stop()

calendar_tab, add_tab, manage_tab, import_tab = st.tabs([
    "Calendar", "Add booking", "Manage bookings", "Import Excel"
])

with calendar_tab:
    calendar(
        events=[to_calendar_event(event) for event in events],
        options={
            "initialView": view_mode,
            "initialDate": selected_date.isoformat(),
            "headerToolbar": {
                "left": "prev,next today",
                "center": "title",
                "right": "dayGridMonth,timeGridWeek,timeGridDay,listWeek",
            },
            "editable": False,
            "selectable": False,
            "height": 720,
            "nowIndicator": True,
            "dayMaxEvents": True,
            "timeZone": "local",
            "eventTimeFormat": {
                "hour": "2-digit",
                "minute": "2-digit",
                "hour12": False,
            },
        },
        key="transport_calendar",
    )
    st.caption(
        f"{len(events)} bookings total. Imported titles show the delivery "
        "site only; pallet counts are ignored."
    )

with add_tab:
    st.subheader("Add a booking")
    with st.form("add_booking_form", clear_on_submit=True):
        title = st.text_input(
            "Delivery site / booking title",
            placeholder="Campey or DHL",
        )
        col1, col2 = st.columns(2)
        booking_date = col1.date_input("Date", value=selected_date)
        booking_time = col2.time_input(
            "Planned time", value=time(9, 0)
        )
        col3, col4 = st.columns(2)
        source = col3.selectbox("Source", SOURCE_OPTIONS, index=2)
        has_end = col4.checkbox("Add an end time")
        end_time = st.time_input(
            "End time", value=time(10, 0), disabled=not has_end
        )
        submitted = st.form_submit_button(
            "Save booking", type="primary"
        )

    if submitted:
        if not title.strip():
            st.error("Enter a delivery site or booking title.")
        elif has_end and end_time <= booking_time:
            st.error("End time must be later than start time.")
        else:
            start_dt = datetime.combine(
                booking_date, booking_time
            ).replace(tzinfo=UK_TZ)
            end_dt = (
                datetime.combine(booking_date, end_time).replace(tzinfo=UK_TZ)
                if has_end else None
            )
            try:
                insert_event(db, title, start_dt, end_dt, source)
                st.success("Booking added.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not save booking: {exc}")

with manage_tab:
    st.subheader("Edit or delete a booking")

    if not events:
        st.info("There are no bookings to manage yet.")
    else:
        event_by_label = {}
        for event in events:
            start_dt = as_local_datetime(event.get("start"))
            start_label = (
                start_dt.strftime("%d %b %Y %H:%M")
                if start_dt else "No date"
            )
            label = (
                f"{start_label} — {event.get('title', 'Untitled')} "
                f"[{event.get('source', 'Manual')}]"
            )
            if label in event_by_label:
                label += f" — {str(event.get('id', ''))[:8]}"
            event_by_label[label] = event

        chosen_label = st.selectbox(
            "Choose booking", list(event_by_label.keys())
        )
        chosen = event_by_label[chosen_label]
        chosen_start = as_local_datetime(chosen.get("start")) or local_now()
        chosen_end = as_local_datetime(chosen.get("end"))

        with st.form("edit_booking_form"):
            edit_title = st.text_input(
                "Title", value=chosen.get("title", "")
            )
            col1, col2 = st.columns(2)
            edit_date = col1.date_input(
                "Date", value=chosen_start.date()
            )
            edit_time = col2.time_input(
                "Planned time",
                value=chosen_start.time().replace(
                    tzinfo=None, second=0, microsecond=0
                ),
            )
            col3, col4 = st.columns(2)
            current_source = chosen.get("source", "Manual")
            if current_source not in SOURCE_OPTIONS:
                current_source = "Manual"
            edit_source = col3.selectbox(
                "Source",
                SOURCE_OPTIONS,
                index=SOURCE_OPTIONS.index(current_source),
            )
            has_end = col4.checkbox(
                "Has end time", value=chosen_end is not None
            )
            edit_end_time = st.time_input(
                "End time",
                value=(
                    chosen_end.time().replace(
                        tzinfo=None, second=0, microsecond=0
                    ) if chosen_end else time(10, 0)
                ),
                disabled=not has_end,
            )
            save_edit = st.form_submit_button(
                "Save changes", type="primary"
            )

        if save_edit:
            if not edit_title.strip():
                st.error("Title cannot be blank.")
            elif has_end and edit_end_time <= edit_time:
                st.error("End time must be later than start time.")
            else:
                new_start = datetime.combine(
                    edit_date, edit_time
                ).replace(tzinfo=UK_TZ)
                new_end = (
                    datetime.combine(edit_date, edit_end_time).replace(
                        tzinfo=UK_TZ
                    ) if has_end else None
                )
                try:
                    update_event(
                        db, chosen["id"], edit_title, new_start,
                        new_end, edit_source
                    )
                    st.success("Booking updated.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not update booking: {exc}")

        st.divider()
        st.warning(
            f"Delete **{chosen.get('title', 'this booking')}**?"
        )
        confirmed = st.checkbox(
            "I understand this permanently deletes the selected booking.",
            key=f"confirm_delete_{chosen.get('id')}",
        )
        if st.button(
            "Delete selected booking",
            disabled=not confirmed,
        ):
            try:
                delete_event(db, chosen["id"])
                st.success("Booking deleted.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not delete booking: {exc}")

with import_tab:
    st.subheader("Import transport requests")
    st.write(
        "Upload your original Excel workbook as-is. The importer finds "
        "the Date and Planned Time headings on each worksheet and uses "
        "Del Site as the event title. Pallet counts are ignored."
    )

    uploaded = st.file_uploader(
        "Choose an Excel workbook",
        type=["xlsx", "xlsm", "xls"],
        help="The exact same file will not be imported twice.",
    )

    if uploaded is not None:
        st.write(f"Selected file: **{uploaded.name}**")
        if st.button("Import bookings", type="primary"):
            with st.spinner("Reading workbook and saving bookings..."):
                try:
                    count, warnings = import_workbook(db, uploaded)
                    if count:
                        st.success(f"Imported {count} bookings.")
                    for warning in warnings:
                        st.warning(warning)
                    if count:
                        st.rerun()
                except Exception as exc:
                    st.error(f"Import failed: {exc}")

st.divider()
st.caption(
    "Bookings are shared through Supabase. Keep your service-role key "
    "in Streamlit Secrets only; never commit it to GitHub."
)
