
import uuid
from datetime import datetime

import streamlit as st
from supabase import create_client

from config import PURPLE, SOURCE_COLOURS, UK


@st.cache_resource
def get_supabase():
    url = st.secrets.get("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_SERVICE_ROLE_KEY")

    if not url or not key:
        st.error(
            "Supabase credentials are missing. Add SUPABASE_URL and "
            "SUPABASE_SERVICE_ROLE_KEY under Streamlit Cloud → "
            "App settings → Secrets."
        )
        st.stop()

    return create_client(url, key)


supabase = get_supabase()


def get_events():
    """Fetch every event from Supabase in batches of 1,000."""
    all_events = []
    page_size = 1000
    start = 0

    while True:
        response = (
            supabase.table("events")
            .select(
                "id,title,start,end,colour,source,created_at,"
                "status,load_header"
            )
            .order("start")
            .order("id")
            .range(start, start + page_size - 1)
            .execute()
        )

        batch = response.data or []
        all_events.extend(batch)

        if len(batch) < page_size:
            break

        start += page_size

    # Protect against older rows or unexpected null values.
    for event in all_events:
        if not event.get("status"):
            event["status"] = "scheduled"

    return all_events


def get_imported_hashes():
    response = (
        supabase.table("imported_files")
        .select("file_hash")
        .execute()
    )

    return {
        row["file_hash"]
        for row in (response.data or [])
    }


def remember_import(file_hash, filename):
    supabase.table("imported_files").upsert({
        "file_hash": file_hash,
        "filename": filename,
        "imported_at": datetime.now(UK).isoformat(),
    }).execute()


def save_event(
    title,
    start_dt,
    end_dt,
    source,
    event_id=None,
    status=None,
    load_header=None,
):
    payload = {
        "title": title.strip(),
        "start": start_dt.astimezone(UK).isoformat(),
        "end": (
            end_dt.astimezone(UK).isoformat()
            if end_dt else None
        ),
        "source": source,
        "colour": SOURCE_COLOURS.get(source, PURPLE),
    }

    # Only change status/header when explicitly supplied.
    # Ordinary edits therefore preserve existing status.
    if status is not None:
        payload["status"] = status

    if load_header is not None:
        payload["load_header"] = load_header.strip() or None

    if event_id:
        supabase.table("events").update(payload).eq(
            "id", event_id
        ).execute()
    else:
        payload["id"] = str(uuid.uuid4())
        supabase.table("events").insert(payload).execute()


def update_event_status(event_id, status, load_header=None):
    """Update a Bulk/DHL status without changing booking details."""
    payload = {"status": status}

    if load_header is not None:
        payload["load_header"] = load_header.strip() or None

    supabase.table("events").update(payload).eq(
        "id", event_id
    ).execute()
