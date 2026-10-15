import streamlit as st
import pandas as pd
import sqlite3
import uuid

from datetime import datetime, timedelta
from streamlit_calendar import calendar

DB = "scheduler.db"


# --------------------------
# DATABASE
# --------------------------

def init_db():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS events(
        id TEXT PRIMARY KEY,
        title TEXT,
        start TEXT,
        end TEXT,
        colour TEXT,
        source TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS imported_files(
        filename TEXT PRIMARY KEY
    )
    """)

    conn.commit()
    conn.close()


def file_imported(filename):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute(
        "SELECT filename FROM imported_files WHERE filename=?",
        (filename,)
    )

    result = cur.fetchone()

    conn.close()

    return result is not None


def mark_file_imported(filename):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute(
        "INSERT OR IGNORE INTO imported_files VALUES(?)",
        (filename,)
    )

    conn.commit()
    conn.close()


def add_event(event):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO events VALUES(?,?,?,?,?,?)",
        (
            event["id"],
            event["title"],
            event["start"],
            event["end"],
            event["backgroundColor"],
            event["source"]
        )
    )

    conn.commit()
    conn.close()


def delete_event(event_id):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute(
        "DELETE FROM events WHERE id=?",
        (event_id,)
    )

    conn.commit()
    conn.close()


def get_events():

    conn = sqlite3.connect(DB)

    df = pd.read_sql(
        "SELECT * FROM events",
        conn
    )

    conn.close()

    if df.empty:
        return []

    events = []

    for _, row in df.iterrows():

        events.append({
            "id": row["id"],
            "title": row["title"],
            "start": row["start"],
            "end": row["end"],
            "backgroundColor": row["colour"]
        })

    return events


# --------------------------
# FILE PARSERS
# --------------------------

def parse_campey(file):

    events = []

    xls = pd.ExcelFile(file)

    for sheet in xls.sheet_names:

        try:
            df = pd.read_excel(
                xls,
                sheet_name=sheet
            )

            for _, row in df.iterrows():

                if pd.isna(row.iloc[0]):
                    continue

                title = "Campey Collection"

                start = datetime.now()

                events.append({
                    "id": str(uuid.uuid4()),
                    "title": title,
                    "start": start.isoformat(),
                    "end": (start + timedelta(hours=1)).isoformat(),
                    "backgroundColor": "#2ecc71",
                    "source": "Campey"
                })

        except Exception as e:
            st.error(f"Campey import error: {e}")

    return events


def parse_bulk(file):

    events = []

    xls = pd.ExcelFile(file)

    for sheet in xls.sheet_names:

        try:

            df = pd.read_excel(
                xls,
                sheet_name=sheet
            )

            for _, row in df.iterrows():

                txt = " ".join(
                    [str(x) for x in row.tolist()]
                )

                if len(txt.strip()) < 10:
                    continue

                start = datetime.now()

                events.append({
                    "id": str(uuid.uuid4()),
                    "title": txt[:50],
                    "start": start.isoformat(),
                    "end": (start + timedelta(hours=1)).isoformat(),
                    "backgroundColor": "#3498db",
                    "source": "Bulk"
                })

        except Exception as e:
            st.error(f"Bulk import error: {e}")

    return events


# --------------------------
# APP
# --------------------------

st.set_page_config(
    page_title="Operations Planner",
    layout="wide"
)

init_db()

st.title(
    "Operations Planning Calendar"
)

# --------------------------
# SIDEBAR UPLOAD
# --------------------------

st.sidebar.header("Upload")

if st.sidebar.button("Clear Calendar"):

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute("DELETE FROM events")
    cur.execute("DELETE FROM imported_files")

    conn.commit()
    conn.close()

    st.rerun()

files = st.sidebar.file_uploader(
    "Upload Excel Schedules",
    type=["xlsx"],
    accept_multiple_files=True
)

if files:

    for file in files:

        if file_imported(file.name):
            st.sidebar.warning(f"{file.name} already imported")
            continue

        if "Campey" in file.name:
            events = parse_campey(file)
        else:
            events = parse_bulk(file)

        for e in events:
            add_event(e)

        mark_file_imported(file.name)

    st.sidebar.success(
        "Schedules Imported"
    )

# --------------------------
# MANUAL EVENT
# --------------------------

st.sidebar.header("Add Event")

title = st.sidebar.text_input("Title")

date = st.sidebar.date_input("Date")

start_time = st.sidebar.time_input("Start Time")

duration = st.sidebar.number_input(
    "Hours",
    value=1
)

colour = st.sidebar.color_picker(
    "Colour",
    "#ff9900"
)

if st.sidebar.button("Add Event"):

    start = datetime.combine(date, start_time)

    end = start + timedelta(hours=duration)

    add_event({
        "id": str(uuid.uuid4()),
        "title": title,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "backgroundColor": colour,
        "source": "Manual"
    })

    st.rerun()

# --------------------------
# CALENDAR
# --------------------------

events = get_events()

calendar_options = {
    "editable": True,
    "selectable": True,
    "headerToolbar": {
        "left": "prev,next today",
        "center": "title",
        "right": "timeGridWeek,timeGridDay"
    },
    "slotMinTime": "06:00:00",
    "slotMaxTime": "30:00:00",
    "initialView": "timeGridWeek",
    "allDaySlot": False,
    "height": 900
}

state = calendar(
    events=events,
    options=calendar_options,
    key="calendar"
)

# --------------------------
# DELETE EVENT
# --------------------------

st.subheader("Delete Event")

if events:

    selected = st.selectbox(
        "Select Event",
        events,
        format_func=lambda x: x["title"]
    )

    if st.button("Delete Selected Event"):

        delete_event(selected["id"])

        st.rerun()
