import streamlit as st
import pandas as pd
import sqlite3
import uuid

from datetime import datetime, timedelta
from streamlit_calendar import calendar

DB = "scheduler.db"


# ==================================================
# DATABASE
# ==================================================

@st.cache_resource
def get_connection():
    return sqlite3.connect(
        DB,
        check_same_thread=False
    )


def init_db():

    conn = get_connection()
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


def file_already_imported(filename):

    conn = get_connection()

    cur = conn.cursor()

    cur.execute(
        "SELECT filename FROM imported_files WHERE filename=?",
        (filename,)
    )

    return cur.fetchone() is not None


def mark_imported(filename):

    conn = get_connection()

    cur = conn.cursor()

    cur.execute(
        "INSERT OR IGNORE INTO imported_files VALUES(?)",
        (filename,)
    )

    conn.commit()


def add_event(event):

    conn = get_connection()

    cur = conn.cursor()

    cur.execute("""
    INSERT OR REPLACE INTO events
    VALUES(?,?,?,?,?,?)
    """, (
        event["id"],
        event["title"],
        event["start"],
        event["end"],
        event["backgroundColor"],
        event["source"]
    ))

    conn.commit()


def update_event(
    event_id,
    title,
    start,
    end,
    colour
):

    conn = get_connection()

    cur = conn.cursor()

    cur.execute("""
    UPDATE events
    SET title=?,
        start=?,
        end=?,
        colour=?
    WHERE id=?
    """, (
        title,
        start,
        end,
        colour,
        event_id
    ))

    conn.commit()


def delete_event(event_id):

    conn = get_connection()

    cur = conn.cursor()

    cur.execute(
        "DELETE FROM events WHERE id=?",
        (event_id,)
    )

    conn.commit()


def clear_database():

    conn = get_connection()

    cur = conn.cursor()

    cur.execute("DELETE FROM events")
    cur.execute("DELETE FROM imported_files")

    conn.commit()


def get_events():

    conn = get_connection()

    df = pd.read_sql(
        "SELECT * FROM events",
        conn
    )

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


# ==================================================
# FILE PARSERS
# ==================================================

def parse_campey(file):

    events = []

    try:

        xls = pd.ExcelFile(file)

        for sheet in xls.sheet_names:

            df = pd.read_excel(
                xls,
                sheet_name=sheet
            )

            for _, row in df.iterrows():

                row_data = [
                    str(x)
                    for x in row.tolist()
                    if pd.notna(x)
                ]

                if not row_data:
                    continue

                title = (
                    "Campey - " +
                    " ".join(row_data[:3])
                )

                start = datetime.now()

                events.append({
                    "id": str(uuid.uuid4()),
                    "title": title[:100],
                    "start": start.isoformat(),
                    "end": (
  
