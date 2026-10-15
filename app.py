
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


# ============================================================
# CONFIGURATION
# ============================================================

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
    "date", "start date", "delivery date", "collection date",
    "appointment date", "scheduled date", "job date",
)

START_NAMES = (
    "start", "start datetime", "start date time",
    "appointment", "scheduled start", "begin",
)

TIME_NAMES = ("time", "start time", "arrival time", "collection time")

END_NAMES = ("end", "end datetime", "finish", "end time", "finish time")

DURATION_NAMES = ("duration", "hours", "duration hours", "length")

TITLE_NAMES = (
    "title", "event", "description", "job", "job name",
    "customer", "customer name", "site", "site name",
    "order", "order number", "reference", "vehicle",
)


# ============================================================
# DATABASE
# ============================================================

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
                colour TEXT NOT NULL,
                source TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS imported_files (
                filename TEXT PRIMARY KEY,
                content_hash TEXT
            )
        """)

        # Migrate databases created by the original app.
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

    return [
        {
            "id": row[0],
            "title": row[1],
            "start": row[2],
            "end": row[3],
            "backgroundColor": row[4],
            "extendedProps": {"source": row[5]},
        }
        for row in rows
    ]


def add_event(event):
    with connect_db() as conn:
        conn.execute("""
            INSERT INTO events (id, title, start, end, colour, source)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            event["id"],
            event["title"],
            event["start"],
            event["end"],
            event["backgroundColor"],
            event["source"],
        ))


def update_event(event_id, start, end):
    with connect_db() as conn:
        cursor = conn.execute("""
            UPDATE events
            SET start = ?, end = ?
            WHERE id = ?
        """, (start, end, event_id))

        return cursor.rowcount > 0


def delete_event(event_id):
    with connect_db() as conn:
        conn.execute("DELETE FROM events WHERE id = ?", (event_id,))


def clear_calendar():
    with connect_db() as conn:
        conn.execute("DELETE FROM events")
        conn.execute("DELETE FROM imported_files")


def imported_hash(content_hash):
    with connect_db() as conn:
        return conn.execute(
            "SELECT 1 FROM imported_files WHERE content_hash = ? LIMIT 1",
            (content_hash,),
        ).fetchone() is not None


def save_import(filename, content_hash, events):
    """Save an import atomically; never mark a failed import as complete."""
    with connect_db() as conn:
        conn.executemany("""
            INSERT INTO events (id, title, start, end, colour, source)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [
            (
                event["id"],
                event["title"],
                event["start"],
                event["end"],
                event["backgroundColor"],
                event["source"],
            )
            for event in events
        ])

        conn.execute("""
            INSERT INTO imported_files (filename, content_hash)
            VALUES (?, ?)
            ON CONFLICT(filename) DO UPDATE SET
                content_hash = excluded.content_hash
        """, (filename, content_hash))


# ============================================================
# EXCEL IMPORT
# ============================================================

def normalise(value):
    value = str(value).strip().lower()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def find_column(columns, candidates, contains=False):
    """Find a likely column name without assuming a fixed workbook layout."""
    normalised = {column: normalise(column) for column in columns}

    for candidate in candidates:
        for column, name in normalised.items():
            if name == candidate:
                return column

    if contains:
        for candidate in candidates:
            for column, name in normalised.items():
                if candidate in name.split():
                    return column

    return None


def valid_value(value):
    if value is None:
        return False
    try:
        return not pd.isna(value) and str(value).strip() != ""
    except (TypeError, ValueError):
        return False


def parse_datetime(value):
    if not valid_value(value):
        return None

    try:
        result = pd.to_datetime(value, errors="coerce")
        if pd.isna(result):
            return None

        result = result.to_pydatetime() if hasattr(
            result, "to_pydatetime"
        ) else result

        # Keep stored calendar values consistent and timezone-naive.
        if result.tzinfo is not None:
            result = result.astimezone().replace(tzinfo=None)

        return result
    except (TypeError, ValueError, OverflowError):
        return None


def parse_time(value):
    if not valid_value(value):
        return None

    if isinstance(value, time):
        return value.replace(tzinfo=None)

    if isinstance(value, (datetime, pd.Timestamp)):
        return value.time().replace(tzinfo=None)

    # Excel can represent a time as a fraction of one day.
    if isinstance(value, (int, float)) and 0 <= value < 1:
        seconds = round(value * 24 * 60 * 60)
        seconds %= 24 * 60 * 60
        return (
            datetime.min + timedelta(seconds=seconds)
        ).time()

    parsed = pd.to_datetime(str(value), errors="coerce")
    if pd.isna(parsed):
        return None

    return parsed.to_pydatetime().time()


def parse_workbook(content, filename):
    """
    Detect common date/time and title columns.

    Returns (events, errors). Workbooks with no recognisable schedule
    date are rejected instead of assigning a fabricated date.
    """
    events = []
    errors = []

    source = "Campey" if "campey" in filename.lower() else "Bulk"
    colour = COLOURS[source]

    try:
        workbook = pd.ExcelFile(BytesIO(content))
    except Exception as exc:
        return [], [f"Could not open workbook: {exc}"]

    for sheet in workbook.sheet_names:
        try:
            df = pd.read_excel(workbook, sheet_name=sheet)
        except Exception as exc:
            errors.append(f"{sheet}: could not read sheet ({exc})")
            continue

        if df.empty or len(df.columns) == 0:
            continue

        # Remove completely blank rows and columns.
        df = df.dropna(how="all").dropna(axis=1, how="all")
        if df.empty:
            continue

        columns = list(df.columns)

        start_col = find_column(columns, START_NAMES)
        date_col = find_column(columns, DATE_NAMES)
        time_col = find_column(columns, TIME_NAMES)
        end_col = find_column(columns, END_NAMES)
        duration_col = find_column(columns, DURATION_NAMES)
        title_col = find_column(columns, TITLE_NAMES, contains=True)

        # A combined start date/time column takes precedence.
        if start_col is None and date_col is None:
            errors.append(
                f"{sheet}: no recognised start/date column. "
                f"Available columns: {', '.join(map(str, columns))}"
            )
            continue

        sheet_count = 0

        for _, row in df.iterrows():
            start = None

            if start_col is not None:
                start = parse_datetime(row[start_col])

            if start is None and date_col is not None:
                day = parse_datetime(row[date_col])

                if day is not None:
                    clock = (
                        parse_time(row[time_col])
                        if time_col is not None
                        else None
                    )
                    start = datetime.combine(
                        day.date(),
                        clock or time(9, 0),
                    )

            if start is None:
                continue

            end = None

            if end_col is not None:
                end_value = row[end_col]

                # An end column may contain a full date/time or only a time.
                end = parse_datetime(end_value)

                if end is None:
                    clock = parse_time(end_value)
                    if clock is not None:
                        end = datetime.combine(start.date(), clock)
                        if end <= start:
                            end += timedelta(days=1)

            if end is None and duration_col is not None:
                try:
                    hours = float(row[duration_col])
                    if 0 < hours <= 168:
                        end = start + timedelta(hours=hours)
                except (TypeError, ValueError):
                    pass

            if end is None:
                end = start + timedelta(hours=1)

            if end <= start:
                errors.append(
                    f"{sheet}: skipped row with end before start."
                )
                continue

            title = None
            if title_col is not None and valid_value(row[title_col]):
                title = str(row[title_col]).strip()

            if not title:
                # Fallback to useful row data, not pandas' "nan" text.
                parts = [
                    str(value).strip()
                    for value in row.tolist()
                    if valid_value(value)
                ]
                title = " - ".join(parts[:3])[:100]

            if not title:
                continue

            events.append({
                "id": str(uuid.uuid4()),
                "title": title,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "backgroundColor": colour,
                "source": source,
            })
            sheet_count += 1

        if sheet_count == 0:
            errors.append(f"{sheet}: no valid scheduled events found.")

    return events, errors


# ============================================================
# APP STARTUP
# ============================================================

init_db()

st.title("📅 Operations Planning Calendar")
st.caption("Import schedules, plan operations and manage events.")

# ============================================================
# SIDEBAR: IMPORT
# ============================================================

st.sidebar.header("Import schedules")

files = st.sidebar.file_uploader(
    "Upload Excel schedules",
    type=["xlsx"],
    accept_multiple_files=True,
    key="schedule_upload",
)

if files:
    for uploaded in files:
        content = uploaded.getvalue()
        digest = sha256(content).hexdigest()

        if imported_hash(digest):
            st.sidebar.info(f"Already imported: {uploaded.name}")
            continue

        with st.spinner(f"Reading {uploaded.name}..."):
            parsed_events, errors = parse_workbook(
                content, uploaded.name
            )

        for error in errors:
            st.warning(f"{uploaded.name}: {error}")

        if not parsed_events:
            st.sidebar.error(
                f"No valid events imported from {uploaded.name}."
            )
            continue

        try:
            save_import(uploaded.name, digest, parsed_events)
            st.sidebar.success(
                f"{uploaded.name}: imported {len(parsed_events)} events."
            )
        except sqlite3.Error as exc:
            st.sidebar.error(f"Database import failed: {exc}")

# ============================================================
# SIDEBAR: MANUAL EVENT
# ============================================================

st.sidebar.divider()
st.sidebar.header("Add event")

with st.sidebar.form("add_event_form", clear_on_submit=True):
    title = st.text_input("Event title")
    event_date = st.date_input("Date", value=date.today())
    start_time = st.time_input("Start time", value=time(9, 0))
    duration = st.number_input(
        "Duration (hours)",
        min_value=0.25,
        max_value=168.0,
        value=1.0,
        step=0.25,
    )
    colour = st.color_picker("Event colour", COLOURS["Manual"])

    submitted = st.form_submit_button(
        "Add event",
        type="primary",
        use_container_width=True,
    )

if submitted:
    if not title.strip():
        st.sidebar.error("Enter an event title.")
    else:
        start = datetime.combine(event_date, start_time)
        end = start + timedelta(hours=float(duration))

        try:
            add_event({
                "id": str(uuid.uuid4()),
                "title": title.strip(),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "backgroundColor": colour,
                "source": "Manual",
            })
            st.sidebar.success("Event added.")
            st.rerun()
        except sqlite3.Error as exc:
            st.sidebar.error(f"Could not save event: {exc}")

# ============================================================
# LOAD AND FILTER EVENTS
# ============================================================

try:
    events = get_events()
except sqlite3.Error as exc:
    st.error(f"Could not load events: {exc}")
    st.stop()

total = len(events)
manual_count = sum(
    event["extendedProps"]["source"] == "Manual"
    for event in events
)
imported_count = total - manual_count

metric1, metric2, metric3 = st.columns(3)
metric1.metric("Total events", total)
metric2.metric("Manual events", manual_count)
metric3.metric("Imported events", imported_count)

search = st.text_input(
    "Search events",
    placeholder="Search by title...",
)

source_filter = st.selectbox(
    "Filter by source",
    ["All", "Campey", "Bulk", "Manual"],
)

visible_events = [
    event for event in events
    if search.strip().lower() in event["title"].lower()
    and (
        source_filter == "All"
        or event["extendedProps"]["source"] == source_filter
    )
]

# ============================================================
# CALENDAR
# ============================================================

st.subheader("Planning calendar")

calendar_options = {
    "editable": True,
    "selectable": True,
    "headerToolbar": {
        "left": "prev,next today",
        "center": "title",
        "right": "timeGridWeek,timeGridDay,dayGridMonth",
    },
    "slotMinTime": "06:00:00",
    "slotMaxTime": "30:00:00",
    "initialView": "timeGridWeek",
    "allDaySlot": False,
    "height": 750,
    "nowIndicator": True,
}

calendar_state = calendar(
    events=visible_events,
    options=calendar_options,
    callbacks=["eventChange"],
    key="operations_calendar",
)

# Persist drag-and-drop and resize changes.
if (
    isinstance(calendar_state, dict)
    and calendar_state.get("callback") == "eventChange"
):
    changed = calendar_state.get("eventChange", {}).get("event", {})

    event_id = changed.get("id")
    new_start = changed.get("start")
    new_end = changed.get("end")

    if event_id and new_start:
        parsed_start = parse_datetime(new_start)
        parsed_end = parse_datetime(new_end) if new_end else None

        # FullCalendar may omit the end when the event has no explicit end.
        if parsed_start is not None:
            if parsed_end is None:
                parsed_end = parsed_start + timedelta(hours=1)

            if parsed_end > parsed_start:
                try:
                    if update_event(
                        event_id,
                        parsed_start.isoformat(),
                        parsed_end.isoformat(),
                    ):
                        st.toast("Calendar change saved.")
                        st.rerun()
                    else:
                        st.warning(
                            "The changed event could not be found in the database."
                        )
                except sqlite3.Error as exc:
                    st.error(f"Could not save calendar change: {exc}")
            else:
                st.error("The event end must be after its start.")

# ============================================================
# EVENT LIST AND DELETE
# ============================================================

st.divider()
st.subheader("Manage events")

if not visible_events:
    st.info("No events match your filters.")
else:
    options = {
        event["id"]: (
            f'{event["title"]} — '
            f'{event["start"][:16].replace("T", " ")} '
            f'({event["extendedProps"]["source"]})'
        )
        for event in visible_events
    }

    selected_id = st.selectbox(
        "Select an event",
        options=list(options),
        format_func=lambda event_id: options[event_id],
    )

    if st.button("Delete selected event", type="secondary"):
        try:
            delete_event(selected_id)
            st.success("Event deleted.")
            st.rerun()
        except sqlite3.Error as exc:
            st.error(f"Could not delete event: {exc}")

with st.expander("Clear the entire calendar"):
    st.warning(
        "This permanently deletes all calendar events and import history. "
        "It does not delete the original Excel files."
    )

    confirm_clear = st.checkbox(
        "I understand that all calendar events will be deleted."
    )

    if st.button(
        "Clear all events",
        disabled=not confirm_clear,
        type="primary",
    ):
        try:
            clear_calendar()
            st.success("Calendar cleared.")
            st.rerun()
        except sqlite3.Error as exc:
            st.error(f"Could not clear calendar: {exc}")
