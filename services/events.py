
import uuid
from datetime import datetime

import streamlit as st
from supabase import create_client

from config import PURPLE, SOURCE_COLOURS, UK


@st.cache_resource
def get_supabase():
    """Create and cache the Supabase client."""
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
    """Fetch all events in batches of 1,000, including saved Campey matches."""
    all_events = []
    page_size = 1000
    start = 0

    while True:
        # Try the updated schema first. The fallback keeps the app working
        # until the campey_load_ref column has been added to Supabase.
        try:
            response = (
                supabase.table("events")
                .select(
                    "id,title,start,end,colour,source,created_at,"
                    "status,load_header,campey_load_ref"
                )
                .order("start")
                .order("id")
                .range(start, start + page_size - 1)
                .execute()
            )
        except Exception as exc:
            if "campey_load_ref" not in str(exc):
                raise

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

    # Normalise optional fields so existing calendar code can rely on them.
    for event in all_events:
        if not event.get("status"):
            event["status"] = "scheduled"

        event.setdefault("load_header", None)
        event.setdefault("campey_load_ref", None)

    return all_events


def get_imported_hashes():
    """Return hashes of files already imported."""
    response = (
        supabase.table("imported_files")
        .select("file_hash")
        .execute()
    )

    return {
        row["file_hash"]
        for row in (response.data or [])
        if row.get("file_hash")
    }


def remember_import(file_hash, filename):
    """Record an imported file so it is not imported twice."""
    supabase.table("imported_files").upsert(
        {
            "file_hash": file_hash,
            "filename": filename,
            "imported_at": datetime.now(UK).isoformat(),
        },
        on_conflict="file_hash",
    ).execute()


def save_event(
    title,
    start_dt,
    end_dt,
    source,
    event_id=None,
    status=None,
    load_header=None,
    campey_load_ref=None,
):
    """
    Insert or update a calendar event.

    Optional fields are only updated when explicitly supplied. This prevents
    ordinary edits from accidentally resetting an existing status, load
    header, or saved Campey load-reference association.
    """
    payload = {
        "title": title.strip(),
        "start": start_dt.astimezone(UK).isoformat(),
        "end": (
            end_dt.astimezone(UK).isoformat()
            if end_dt
            else None
        ),
        "source": source,
        "colour": SOURCE_COLOURS.get(source, PURPLE),
    }

    if status is not None:
        payload["status"] = status

    if load_header is not None:
        payload["load_header"] = load_header.strip() or None

    if campey_load_ref is not None:
        payload["campey_load_ref"] = campey_load_ref.strip() or None

    if event_id:
        supabase.table("events").update(payload).eq(
            "id", event_id
        ).execute()
    else:
        payload["id"] = str(uuid.uuid4())

        if status is None:
            payload["status"] = "scheduled"

        supabase.table("events").insert(payload).execute()


def update_event_status(event_id, status, load_header=None):
    """Update an event's status without changing its booking details."""
    payload = {"status": status}

    if load_header is not None:
        payload["load_header"] = load_header.strip() or None

    supabase.table("events").update(payload).eq(
        "id", event_id
    ).execute()


def update_campey_load_ref(event_id, load_ref):
    """
    Save or clear the Load Ref associated with a Campey booking.

    Pass an empty string or None to clear the association.
    Requires the campey_load_ref column in the events table.
    """
    value = str(load_ref or "").strip() or None

    supabase.table("events").update(
        {"campey_load_ref": value}
    ).eq("id", event_id).execute()


def clear_campey_load_refs_for_load(load_ref):
    """
    Clear a Load Ref association from any booking currently using it.

    Useful when a CSV association needs to be reassigned. This operation
    does not delete calendar bookings.
    """
    value = str(load_ref or "").strip()

    if not value:
        return

    supabase.table("events").update(
        {"campey_load_ref": None}
    ).eq("campey_load_ref", value).eq(
        "source", "Campey"
    ).execute()
