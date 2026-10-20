from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from io import BytesIO
import re

import pandas as pd
import requests
import streamlit as st

from config import SAVED_LOADS_API, UK
from services.events import supabase


GITHUB_TIMEOUT = 30
MAX_WORKERS = 8
UPSERT_BATCH_SIZE = 100


@st.cache_data(ttl=60, show_spinner=False)
def _list_saved_load_csvs():
    """List CSV files in the sender repository's saved_loads folder."""
    response = requests.get(
        SAVED_LOADS_API,
        headers={"Accept": "application/vnd.github+json"},
        timeout=GITHUB_TIMEOUT,
    )
    response.raise_for_status()

    items = response.json()

    if not isinstance(items, list):
        raise RuntimeError(
            "GitHub returned an unexpected response for saved_loads."
        )

    return [
        {
            "filename": item["name"],
            "download_url": item["download_url"],
        }
        for item in items
        if item.get("type") == "file"
        and item.get("name", "").lower().endswith(".csv")
        and item.get("download_url")
    ]


def _to_uk_datetime(value):
    """Convert a date value into a timezone-aware UK datetime."""
    timestamp = pd.to_datetime(value, dayfirst=True, errors="coerce")

    if pd.isna(timestamp):
        return None

    timestamp = timestamp.to_pydatetime()

    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=UK)

    return timestamp.astimezone(UK)


def _parse_saved_load_csv(item):
    """Download and parse one CSV into a Supabase-ready record."""
    response = requests.get(
        item["download_url"],
        timeout=GITHUB_TIMEOUT,
    )
    response.raise_for_status()

    df = pd.read_csv(
        BytesIO(response.content),
        dtype=str,
        encoding="utf-8-sig",
    )

    # Make matching tolerant of spaces and different column capitalisation.
    column_lookup = {
        str(column).strip().casefold(): column
        for column in df.columns
    }

    load_ref_column = column_lookup.get("load ref")
    date_column = column_lookup.get("date")

    if not load_ref_column:
        raise ValueError("CSV does not contain a 'Load Ref' column.")

    load_refs = (
        df[load_ref_column]
        .dropna()
        .astype(str)
        .str.strip()
    )
    load_refs = load_refs[load_refs != ""]

    if load_refs.empty:
        raise ValueError("CSV does not contain a load reference.")

    load_ref = load_refs.iloc[0]
    sent_at = None

    if date_column:
        for value in df[date_column].dropna():
            sent_at = _to_uk_datetime(value)
            if sent_at is not None:
                break

    # If the CSV's Date column is missing or unparseable, use a timestamp
    # embedded in filenames such as saved_load_20261020_142530.csv.
    if sent_at is None:
        match = re.search(
            r"_(\d{8})_(\d{6})\.csv$",
            item["filename"],
            flags=re.IGNORECASE,
        )

        if match:
            sent_at = _to_uk_datetime(
                f"{match.group(1)} {match.group(2)}"
            )

    if sent_at is None:
        raise ValueError(
            "Could not determine the sent date from the CSV or filename."
        )

    return {
        "filename": item["filename"],
        "load_ref": load_ref,
        "sent_at": sent_at.isoformat(),
    }


def _get_existing_filenames():
    """Read all filenames already imported into Supabase."""
    filenames = set()
    page_size = 1000
    start = 0

    while True:
        response = (
            supabase.table("sent_loads")
            .select("filename")
            .range(start, start + page_size - 1)
            .execute()
        )

        rows = response.data or []
        filenames.update(
            row["filename"]
            for row in rows
            if row.get("filename")
        )

        if len(rows) < page_size:
            break

        start += page_size

    return filenames


def sync_sent_loads():
    """
    Import new saved-load CSVs from GitHub into Supabase.

    Existing filenames are not downloaded again.
    Returns a summary containing found, added and errors.
    """
    github_items = _list_saved_load_csvs()
    existing_filenames = _get_existing_filenames()

    new_items = [
        item
        for item in github_items
        if item["filename"] not in existing_filenames
    ]

    parsed_rows = []
    errors = []

    if new_items:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(_parse_saved_load_csv, item): item
                for item in new_items
            }

            for future in as_completed(futures):
                item = futures[future]

                try:
                    parsed_rows.append(future.result())
                except Exception as exc:
                    errors.append(
                        f"{item['filename']}: {exc}"
                    )

    # Insert records in batches. The filename primary key prevents
    # duplicate records if a sync is retried.
    for start in range(0, len(parsed_rows), UPSERT_BATCH_SIZE):
        batch = parsed_rows[
            start : start + UPSERT_BATCH_SIZE
        ]

        (
            supabase.table("sent_loads")
            .upsert(batch, on_conflict="filename")
            .execute()
        )

    if parsed_rows:
        get_sent_loads.clear()

    return {
        "found": len(github_items),
        "added": len(parsed_rows),
        "errors": errors,
    }


@st.cache_data(ttl=30, show_spinner=False)
def get_sent_loads():
    """
    Read sent-load records from Supabase in the format expected
    by the calendar's existing decorate_events() function.
    """
    rows = []
    page_size = 1000
    start = 0

    while True:
        response = (
            supabase.table("sent_loads")
            .select("load_ref,sent_at,filename")
            .order("sent_at")
            .order("filename")
            .range(start, start + page_size - 1)
            .execute()
        )

        batch = response.data or []
        rows.extend(batch)

        if len(batch) < page_size:
            break

        start += page_size

    sent_loads = []

    for row in rows:
        sent_at = _to_uk_datetime(row.get("sent_at"))

        if sent_at is None:
            continue

        sent_loads.append(
            {
                "load_ref": row["load_ref"],
                "sent_at": sent_at,
                "filename": row["filename"],
            }
        )

    return sent_loads