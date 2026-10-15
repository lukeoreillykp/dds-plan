
from datetime import date, datetime, time, timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import re
import sqlite3
import uuid

import pandas as pd
import streamlit as st
from streamlit_calendar import calendar


# --------------------------------------------------
# Configuration
# --------------------------------------------------

st.set_page_config(
    page_title="Operations Planner",
    page_icon="📅",
    layout="wide",
)

DB = Path(__file__).resolve().parent / "scheduler.db"

COLOURS = {
    "Campey": "#2ecc71",
    "Bulk": "#3498db",
    "Manual": "#ff9900",
}

DATE_NAMES = (
    "date",
    "start date",
    "delivery date",
    "collection date",
    "appointment date",
    "scheduled date",
    "job date",
)

START_NAMES = (
    "start",
    "start datetime",
    "start date time",
    "appointment",
    "scheduled start",
    "begin",
)

TIME_NAMES = (
    "time",
    "start time",
    "arrival time",
    "collection time",
)

END_NAMES = (
    "end",
    "end datetime",
    "finish",
    "end time",
    "finish time",
)

DURATION_NAMES = (
    "duration",
    "hours",
    "duration hours",
    "length",
)

TITLE_NAMES = (
    "title",
    "event",
    "description",
    "job",
    "job name",
    "customer",
    "customer name",
    "site",
    "site name",
    "order",
    "order number",
    "reference",
    "vehicle",
)


# --------------------------------------------------
# Database
# --------------------------------------------------

def connect_db():
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db():
    with connect_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                start TEXT NOT NULL,
                end TEXT NOT NULL,
                colour TEXT,
                source TEXT NOT NULL DEFAULT 'Manual'
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS imported_files (
                filename TEXT PRIMARY KEY,
                content_hash TEXT
            )
        """)

        # Migrate an existing database created by the older app.
        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(imported_files)"
            ).fetchall()
        }

        if "content_hash" not in columns:
            conn.execute(
                "ALTER TABLE imported_files ADD COLUMN content_hash TEXT"
            )


def get_events():
    with connect_db() as conn:
        rows = conn.execute("""
            SELECT id, title, start, end, colour, source
            FROM events
            ORDER BY start
        """).fetchall()

    events = []

    for event_id, title, start, end, colour, source in rows:
        events.append({
            "id": event_id,
            "title": title,
            "start": start,
            "end": end,
            "backgroundColor": colour or "#3498db",
            "borderColor": colour or "#3498db",
            "extendedProps": {
                "source": source,
            },
        })

    return events


def add_event(title, start, end, colour, source="Manual"):
    event_id = str(uuid.uuid4())

    with connect_db() as conn:
        conn.execute("""
            INSERT INTO events
                (id, title, start, end, colour, source)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            event_id,
            title,
            start.isoformat(),
            end.isoformat(),
            colour,
            source,
        ))

    return event_id


def update_event(event_id, start, end):
    with connect_db() as conn:
        conn.execute("""
            UPDATE events
            SET start = ?, end = ?
            WHERE id = ?
        """, (
            start.isoformat(),
            end.isoformat(),
            str(event_id),
        ))


def delete_event(event_id):
    with connect_db() as conn:
        conn.execute(
            "DELETE FROM events WHERE id = ?",
            (str(event_id),),
        )


def clear_calendar():
    with connect_db() as conn:
        conn.execute("DELETE FROM events")
        conn.execute("DELETE FROM imported_files")


def imported_hash(content_hash):
    with connect_db() as conn:
        row = conn.execute("""
            SELECT filename
            FROM imported_files
            WHERE content_hash = ?
        """, (content_hash,)).fetchone()

    return row is not None


def save_import(filename, content_hash, events):
    with connect_db() as conn:
        for event in events:
            conn.execute("""
                INSERT INTO events
                    (id, title, start, end, colour, source)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                str(uuid.uuid4()),
                event["title"],
                event["start"].isoformat(),
                event["end"].isoformat(),
                event["colour"],
                event["source"],
            ))

        conn.execute("""
            INSERT INTO imported_files (filename, content_hash)
            VALUES (?, ?)
            ON CONFLICT(filename)
            DO UPDATE SET content_hash = excluded.content_hash
        """, (filename, content_hash))


# --------------------------------------------------
# Excel parsing helpers
# --------------------------------------------------

def normalise(value):
    text = str(value).strip().lower()
    text = re.sub(r"[_\-]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def find_column(columns, candidates, contains=False):
    normalised = {
        column: normalise(column)
        for column in columns
    }

    # Prefer exact matches.
    for candidate in candidates:
        candidate = normalise(candidate)

        for column, name in normalised.items():
            if name == candidate:
                return column

    if contains:
        for candidate in candidates:
            candidate = normalise(candidate)

            for column, name in normalised.items():
                if candidate in name:
                    return column

    return None


def valid_value(value):
    if value is None:
        return False

    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass

    return str(value).strip() != ""


def parse_datetime(value):
    if not valid_value(value):
        return None

    try:
        result = pd.to_datetime(value, errors="coerce")
    except (ValueError, TypeError, OverflowError):
        return None

    if pd.isna(result):
        return None

    result = result.to_pydatetime()

    if result.tzinfo is not None:
        result = result.astimezone().replace(tzinfo=None)

    return result


def parse_time(value):
    if not valid_value(value):
        return None

    if isinstance(value, time):
        return value.replace(tzinfo=None)

    if isinstance(value, datetime):
        return value.time().replace(tzinfo=None)

    if isinstance(value, (int, float)) and 0 <= value < 1:
        seconds = int(round(value * 24 * 60 * 60))
        seconds %= 24 * 60 * 60
        return (
            datetime.min + timedelta(seconds=seconds)
        ).time()

    parsed = parse_datetime(value)

    if parsed is not None:
        return parsed.time()

    return None


def parse_workbook(content, filename):
    source = (
        "Campey"
        if "campey" in filename.lower()
        else "Bulk"
    )

    colour = COLOURS[source]
    workbook = pd.ExcelFile(BytesIO(content))

    parsed_events = []
    errors = []

    for sheet_name in workbook.sheet_names:
        try:
            df = pd.read_excel(
                workbook,
                sheet_name=sheet_name,
            )
        except Exception as exc:
            errors.append(
                f"{sheet_name}: couldn't read sheet ({exc})"
            )
            continue

        if df.empty:
            continue

        df.columns = [
            str(column).strip()
            for column in df.columns
        ]

        # Detect the date/start column.
        start_col = find_column(
            df.columns,
            START_NAMES,
            contains=True,
        )

        date_col = find_column(
            df.columns,
            DATE_NAMES,
            contains=True,
        )

        time_col = find_column(
            df.columns,
            TIME_NAMES,
            contains=True,
        )

        end_col = find_column(
            df.columns,
            END_NAMES,
            contains=True,
        )

        duration_col = find_column(
            df.columns,
            DURATION_NAMES,
            contains=True,
        )

        title_col = find_column(
            df.columns,
            TITLE_NAMES,
            contains=True,
        )

        # Prefer a combined start/date-time column.
        if start_col is None:
            start_col = date_col

        if start_col is None:
            errors.append(
                f"{sheet_name}: no recognised date/start column. "
                "Check the spreadsheet headers."
            )
            continue

        sheet_events = 0

        for _, row in df.iterrows():
            start_dt = parse_datetime(row.get(start_col))

            if start_dt is None:
                continue

            # If date and time are separate, combine them.
            if (
                date_col is not None
                and time_col is not None
                and date_col != start_col
            ):
                parsed_time = parse_time(row.get(time_col))

                if parsed_time is not None:
                    start_dt = datetime.combine(
                        start_dt.date(),
                        parsed_time,
                    )

            # Determine event end.
            end_dt = None

            if (
                end_col is not None
                and valid_value(row.get(end_col))
            ):
                end_value = row.get(end_col)

                # An end-time-only column uses the start date.
                parsed_end_time = parse_time(end_value)

                if (
                    isinstance(end_value, time)
                    or (
                        isinstance(end_value, (int, float))
                        and 0 <= end_value < 1
                    )
                ):
                    if parsed_end_time is not None:
                        end_dt = datetime.combine(
                            start_dt.date(),
                            parsed_end_time,
                        )
                else:
                    end_dt = parse_datetime(end_value)

                    # If parsing gives a time on a different date,
                    # treat it as a time-only end when appropriate.
                    if (
                        end_dt is not None
                        and end_dt.date() != start_dt.date()
                        and parsed_end_time is not None
                    ):
                        end_dt = datetime.combine(
                            start_dt.date(),
                            parsed_end_time,
                        )

            elif (
                duration_col is not None
                and valid_value(row.get(duration_col))
            ):
                try:
                    hours = float(row.get(duration_col))
                    if hours > 0:
                        end_dt = start_dt + timedelta(hours=hours)
                except (ValueError, TypeError, OverflowError):
                    end_dt = None

            # Default duration: 1 hour.
            if end_dt is None or end_dt <= start_dt:
                end_dt = start_dt + timedelta(hours=1)

            # Choose a useful title.
            title = None

            if (
                title_col is not None
                and valid_value(row.get(title_col))
            ):
                title = str(row.get(title_col)).strip()

            if not title:
                values = []

                for value in row.tolist():
                    if valid_value(value):
                        values.append(str(value).strip())

                title = " - ".join(values[:3])

            if not title:
                title = f"{source} event"

            parsed_events.append({
                "title": title[:200],
                "start": start_dt,
                "end": end_dt,
                "colour": colour,
                "source": source,
            })

            sheet_events += 1

        if sheet_events == 0:
            errors.append(
                f"{sheet_name}: no valid events found."
            )

    return parsed_events, errors


# --------------------------------------------------
# Initialise app
# --------------------------------------------------

init_db()

st.title("📅 Operations Planner")
st.caption(
    "Plan, import and manage your scheduled work in one calendar."
)


# --------------------------------------------------
# Sidebar: import schedules
# --------------------------------------------------

with st.sidebar:
    st.header("Import schedules")

    uploaded_files = st.file_uploader(
        "Upload Excel schedules",
        type=["xlsx", "xlsm", "xls"],
        accept_multiple_files=True,
    )

    if uploaded_files:
        for uploaded_file in uploaded_files:
            content = uploaded_file.getvalue()
            content_hash = sha256(content).hexdigest()

            if imported_hash(content_hash):
                st.info(
                    f"Already imported: {uploaded_file.name}"
                )
                continue

            with st.spinner(
                f"Importing {uploaded_file.name}..."
            ):
                try:
                    events, errors = parse_workbook(
                        content,
                        uploaded_file.name,
                    )

                    if events:
                        save_import(
                            uploaded_file.name,
                            content_hash,
                            events,
                        )

                        st.success(
                            f"Imported {len(events)} events "
                            f"from {uploaded_file.name}."
                        )
                    else:
                        st.warning(
                            f"No events imported from "
                            f"{uploaded_file.name}."
                        )

                    for error in errors:
                        st.warning(error)

                except Exception as exc:
                    st.error(
                        f"Could not import "
                        f"{uploaded_file.name}: {exc}"
                    )

    st.divider()

    st.header("Add an event")

    with st.form("manual_event_form", clear_on_submit=True):
        event_title = st.text_input("Event title")

        event_date = st.date_input(
            "Date",
            value=date.today(),
        )

        event_start_time = st.time_input(
            "Start time",
            value=time(9, 0),
        )

        event_duration = st.number_input(
            "Duration (hours)",
            min_value=0.25,
            max_value=168.0,
            value=1.0,
            step=0.25,
        )

        event_colour = st.selectbox(
            "Colour",
            options=list(COLOURS.keys()),
            index=2,
        )

        submitted = st.form_submit_button(
            "Add event",
            use_container_width=True,
        )

        if submitted:
            if not event_title.strip():
                st.error("Please enter an event title.")
            else:
                start_dt = datetime.combine(
                    event_date,
                    event_start_time,
                )
                end_dt = start_dt + timedelta(
                    hours=float(event_duration)
                )

                add_event(
                    event_title.strip(),
                    start_dt,
                    end_dt,
                    COLOURS[event_colour],
                    source="Manual",
                )

                st.success("Event added.")
                st.rerun()


# --------------------------------------------------
# Load and filter events
# --------------------------------------------------

all_events = get_events()

col1, col2, col3 = st.columns(3)

col1.metric("Total events", len(all_events))

col2.metric(
    "Imported events",
    sum(
        1 for event in all_events
        if event["extendedProps"]["source"] != "Manual"
    ),
)

col3.metric(
    "Manual events",
    sum(
        1 for event in all_events
        if event["extendedProps"]["source"] == "Manual"
    ),
)

filter_col1, filter_col2 = st.columns([2, 1])

with filter_col1:
    search_text = st.text_input(
        "Search events",
        placeholder="Search by title...",
    )

with filter_col2:
    source_filter = st.selectbox(
        "Show source",
        ["All", "Campey", "Bulk", "Manual"],
    )

events = []

for event in all_events:
    source = event["extendedProps"]["source"]

    if (
        source_filter != "All"
        and source != source_filter
    ):
        continue

    if (
        search_text.strip()
        and search_text.lower() not in event["title"].lower()
    ):
        continue

    events.append(event)


# --------------------------------------------------
# Calendar
# --------------------------------------------------

st.subheader("Schedule calendar")

st.caption(
    "Drag an event to reschedule it. "
    "Use the buttons above the calendar to change views."
)

calendar_options = {
    "initialView": "dayGridMonth",
    "headerToolbar": {
        "left": "prev,next today",
        "center": "title",
        "right": "dayGridMonth,timeGridWeek,timeGridDay",
    },
    "height": 700,
    "contentHeight": 650,
    "editable": True,
    "selectable": True,
    "nowIndicator": True,
    "slotMinTime": "06:00:00",
    "slotMaxTime": "30:00:00",
    "dayMaxEvents": True,
    "navLinks": True,
}

calendar_state = calendar(
    events=events,
    options=calendar_options,
    custom_css="""
        .fc {
            min-height: 650px;
        }

        .fc .fc-toolbar-title {
            font-size: 1.2rem;
        }
    """,
    callbacks=["eventChange"],
    key="operations_calendar",
)


# --------------------------------------------------
# Persist drag-and-drop changes
# --------------------------------------------------

if isinstance(calendar_state, dict):
    if calendar_state.get("callback") == "eventChange":
        change_data = calendar_state.get("eventChange", {})
        changed_event = change_data.get("event", {})

        event_id = changed_event.get("id")
        new_start = parse_datetime(changed_event.get("start"))
        new_end = parse_datetime(changed_event.get("end"))

        if event_id and new_start:
            if new_end is None or new_end <= new_start:
                new_end = new_start + timedelta(hours=1)

            update_event(
                event_id,
                new_start,
                new_end,
            )

            st.rerun()


# --------------------------------------------------
# Delete events
# --------------------------------------------------

st.divider()
st.subheader("Manage events")

if not all_events:
    st.info(
        "No events yet. Add one from the sidebar "
        "or import an Excel schedule."
    )
else:
    event_options = {}

    for event in all_events:
        start_dt = parse_datetime(event["start"])
        display_start = (
            start_dt.strftime("%d %b %Y %H:%M")
            if start_dt
            else event["start"]
        )

        source = event["extendedProps"]["source"]

        label = (
            f"{display_start} — {event['title']} "
            f"({source})"
        )

        # Include ID so duplicate titles remain distinct.
        event_options[label + f" [{event['id'][:8]}]"] = event["id"]

    selected_label = st.selectbox(
        "Choose an event to delete",
        options=list(event_options.keys()),
    )

    if st.button(
        "Delete selected event",
        type="secondary",
    ):
        delete_event(event_options[selected_label])
        st.success("Event deleted.")
        st.rerun()


# --------------------------------------------------
# Clear calendar
# --------------------------------------------------

with st.expander("Advanced: clear the calendar"):
    st.warning(
        "This removes all events and import records from the database."
    )

    confirm_clear = st.checkbox(
        "I understand that this cannot be undone."
    )

    if st.button(
        "Clear all events",
        disabled=not confirm_clear,
    ):
        clear_calendar()
        st.success("Calendar cleared.")
        st.rerun()
