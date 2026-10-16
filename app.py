
import hashlib
import io
import re
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st
from supabase import create_client
from streamlit_calendar import calendar

# ============================================================
# DDS TRANSPORT CALENDAR
# ============================================================

st.set_page_config(
    page_title="DDS Transport Calendar",
    page_icon="🚚",
    layout="wide",
)

UK = ZoneInfo("Europe/London")

SENDER_REPO = "lukeoreillykp/Kp-to-campeys-sscc-conversion"
SAVED_LOADS_API = (
    f"https://api.github.com/repos/{SENDER_REPO}/contents/saved_loads"
)

BLUE = "#2878c8"
GREEN = "#239653"
RED = "#d64545"
PURPLE = "#7b61a8"
AMBER = "#e0a126"

SOURCE_COLOURS = {
    "Campey": BLUE,
    "Bulk": AMBER,
    "Manual": PURPLE,
}


# ============================================================
# SUPABASE
# ============================================================

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


def local_datetime(value):
    if not value:
        return None

    try:
        result = pd.to_datetime(value, errors="coerce")

        if pd.isna(result):
            return None

        result = result.to_pydatetime()

        if result.tzinfo is None:
            return result.replace(tzinfo=UK)

        return result.astimezone(UK)

    except Exception:
        return None


def parse_date(value):
    if value is None or pd.isna(value):
        return None

    try:
        result = pd.to_datetime(value, dayfirst=True, errors="coerce")
        return None if pd.isna(result) else result.date()
    except Exception:
        return None


def parse_time(value):
    if value is None or pd.isna(value):
        return None

    if isinstance(value, time):
        return value.replace(tzinfo=None)

    if isinstance(value, datetime):
        return value.time().replace(tzinfo=None)

    # Excel sometimes stores times as a fraction of one day.
    try:
        number = float(value)

        if 0 <= number < 1:
            seconds = round(number * 86400)
            return (datetime.min + timedelta(seconds=seconds)).time()
    except (ValueError, TypeError):
        pass

    try:
        result = pd.to_datetime(str(value).strip(), errors="coerce")

        if not pd.isna(result):
            return result.to_pydatetime().time().replace(tzinfo=None)
    except Exception:
        pass

    return None


def get_events():
    response = (
        supabase.table("events")
        .select("id,title,start,end,colour,source,created_at")
        .order("start")
        .execute()
    )

    return response.data or []


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


def save_event(
    title,
    start_dt,
    end_dt,
    source,
    event_id=None,
):
    payload = {
        "title": title.strip(),
        "start": start_dt.astimezone(UK).isoformat(),
        "end": end_dt.astimezone(UK).isoformat() if end_dt else None,
        "source": source,
        "colour": SOURCE_COLOURS.get(source, PURPLE),
    }

    if event_id:
        supabase.table("events").update(payload).eq(
            "id", event_id
        ).execute()
    else:
        payload["id"] = str(uuid.uuid4())

        supabase.table("events").insert(payload).execute()


# ============================================================
# CAMPEY EXCEL IMPORT
# ============================================================

def parse_workbook(file_bytes):
    workbook = pd.ExcelFile(io.BytesIO(file_bytes))
    bookings = {}

    for sheet_name in workbook.sheet_names:
        raw = pd.read_excel(
            workbook,
            sheet_name=sheet_name,
            header=None,
        )

        header_row = None

        # Find the real headings; workbook tabs may have title rows first.
        for index, row in raw.iterrows():
            values = [
                str(value).strip().lower()
                for value in row.tolist()
                if not pd.isna(value)
            ]

            has_date = any(
                value == "date" or value.startswith("date ")
                for value in values
            )
            has_time = any("planned time" in value for value in values)

            if has_date and has_time:
                header_row = index
                break

        if header_row is None:
            continue

        headers = [
            "" if pd.isna(value) else str(value).strip()
            for value in raw.iloc[header_row].tolist()
        ]

        data = raw.iloc[header_row + 1:].copy()
        data.columns = headers

        lookup = {
            str(column).strip().lower(): column
            for column in data.columns
        }

        date_col = next(
            (
                column for name, column in lookup.items()
                if name == "date" or name.startswith("date ")
            ),
            None,
        )

        time_col = next(
            (
                column for name, column in lookup.items()
                if "planned time" in name
            ),
            None,
        )

        site_col = next(
            (
                column for name, column in lookup.items()
                if name in ("del site", "delivery site", "site")
            ),
            None,
        )

        if not date_col or not time_col or not site_col:
            continue

        for _, row in data.iterrows():
            booking_date = parse_date(row.get(date_col))
            booking_time = parse_time(row.get(time_col))
            site = row.get(site_col)

            if booking_date is None or booking_time is None:
                continue

            if site is None or pd.isna(site):
                continue

            site = str(site).strip()

            if not site or site.lower() in {"nan", "none", "del site"}:
                continue

            start_dt = datetime.combine(
                booking_date, booking_time
            ).replace(tzinfo=UK)

            key = (site.casefold(), start_dt.isoformat())

            bookings[key] = {
                "title": site,
                "start": start_dt,
                "end": start_dt + timedelta(hours=1),
                "source": "Campey",
                "colour": BLUE,
            }

    return list(bookings.values())


# ============================================================
# READ-ONLY SENT-LOAD CHECK
# A CSV appearing in saved_loads means the load was sent.
# ============================================================

@st.cache_data(ttl=60, show_spinner=False)
def get_sent_loads():
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "DDS-Transport-Calendar",
    }

    response = requests.get(
        SAVED_LOADS_API,
        headers=headers,
        timeout=20,
    )
    response.raise_for_status()

    files = response.json()
    loads = []

    for item in files:
        filename = item.get("name", "")

        if (
            item.get("type") != "file"
            or not filename.lower().endswith(".csv")
        ):
            continue

        download_url = item.get("download_url")

        if not download_url:
            continue

        csv_response = requests.get(
            download_url,
            headers={"User-Agent": "DDS-Transport-Calendar"},
            timeout=20,
        )
        csv_response.raise_for_status()

        try:
            frame = pd.read_csv(
                io.BytesIO(csv_response.content),
                dtype=str,
            )
        except Exception:
            continue

        if frame.empty:
            continue

        columns = {
            str(column).strip().lower(): column
            for column in frame.columns
        }

        ref_col = columns.get("load ref")
        date_col = columns.get("date")

        if not ref_col:
            continue

        refs = frame[ref_col].dropna().astype(str).str.strip()
        refs = refs[refs != ""]

        if refs.empty:
            continue

        load_ref = refs.iloc[0]
        sent_at = None

        # Prefer the Date recorded inside the CSV.
        if date_col:
            dates = frame[date_col].dropna().astype(str).str.strip()
            dates = dates[dates != ""]

            if not dates.empty:
                parsed = pd.to_datetime(
                    dates.iloc[0],
                    dayfirst=True,
                    errors="coerce",
                )

                if not pd.isna(parsed):
                    sent_at = parsed.to_pydatetime()

        # Fallback for names like H43474_20261002_224101.csv.
        if sent_at is None:
            match = re.search(
                r"_(\d{8})_(\d{6})\.csv$",
                filename,
                re.IGNORECASE,
            )

            if match:
                try:
                    sent_at = datetime.strptime(
                        match.group(1) + match.group(2),
                        "%Y%m%d%H%M%S",
                    )
                except ValueError:
                    pass

        if sent_at is None:
            continue

        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=UK)
        else:
            sent_at = sent_at.astimezone(UK)

        loads.append({
            "load_ref": load_ref,
            "sent_at": sent_at,
            "filename": filename,
        })

    # Earliest sent CSV first.
    loads.sort(key=lambda load: (load["sent_at"], load["filename"]))

    return loads


def decorate_events(events, sent_loads, now):
    """Match sent loads to same-day Campey bookings, one-to-one."""

    loads_by_day = {}

    for load in sent_loads:
        day = load["sent_at"].date()
        loads_by_day.setdefault(day, []).append(load)

    bookings_by_day = {}

    for event in events:
        if event.get("source", "Manual") != "Campey":
            continue

        start_dt = local_datetime(event.get("start"))

        if start_dt:
            bookings_by_day.setdefault(
                start_dt.date(), []
            ).append((start_dt, event))

    decorated = []

    for day, bookings in bookings_by_day.items():
        # Earliest planned booking gets the earliest sent load.
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

                # Red only once the planned time has passed.
                item["_display_colour"] = (
                    RED if start_dt < now else BLUE
                )

            decorated.append(item)

    # Bulk and manual bookings keep their original titles and colours.
    for event in events:
        if event.get("source", "Manual") == "Campey":
            continue

        item = dict(event)
        item["_display_title"] = event.get("title", "")
        item["_sent"] = False
        item["_sent_filename"] = ""
        item["_display_colour"] = (
            event.get("colour")
            or SOURCE_COLOURS.get(event.get("source", "Manual"), PURPLE)
        )

        decorated.append(item)

    return decorated


# ============================================================
# MAIN PAGE
# ============================================================

st.title("🚚 DDS Transport Calendar")
st.caption(
    "Shared calendar • Supabase bookings • automatic sent-load checking"
)

try:
    events = get_events()
except Exception as exc:
    st.error(f"Could not load bookings from Supabase: {exc}")
    st.stop()

github_error = None

try:
    sent_loads = get_sent_loads()
except Exception as exc:
    sent_loads = []
    github_error = str(exc)

now = datetime.now(UK)
display_events = decorate_events(events, sent_loads, now)

if github_error:
    st.warning(
        "Could not check the saved-load CSVs in GitHub. "
        "Sent/overdue colours may be out of date. "
        f"Details: {github_error}"
    )

sent_count = sum(event.get("_sent", False) for event in display_events)

overdue_count = sum(
    1
    for event in display_events
    if event.get("source") == "Campey"
    and not event.get("_sent")
    and (local_datetime(event.get("start")) or now) < now
)

m1, m2, m3 = st.columns(3)
m1.metric("Total bookings", len(events))
m2.metric("Sent loads matched", sent_count)
m3.metric("Past unmatched bookings", overdue_count)

with st.expander("Calendar colour key"):
    st.markdown(
        f'<span style="color:{BLUE}">■</span> Upcoming Campey booking &nbsp; '
        f'<span style="color:{GREEN}">■</span> Sent — load reference shown &nbsp; '
        f'<span style="color:{RED}">■</span> Planned time passed, no matching load &nbsp; '
        f'<span style="color:{AMBER}">■</span> Bulk &nbsp; '
        f'<span style="color:{PURPLE}">■</span> Manual',
        unsafe_allow_html=True,
    )

calendar_events = []

for event in display_events:
    start_dt = local_datetime(event.get("start"))

    if not start_dt:
        continue

    end_dt = (
        local_datetime(event.get("end"))
        or start_dt + timedelta(hours=1)
    )

    calendar_events.append({
        "id": str(event.get("id", "")),
        "title": event.get(
            "_display_title", event.get("title", "")
        ),
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "backgroundColor": event.get("_display_colour", PURPLE),
        "borderColor": event.get("_display_colour", PURPLE),
        "extendedProps": {
            "source": event.get("source", "Manual"),
            "originalTitle": event.get("title", ""),
            "sent": event.get("_sent", False),
            "sentFilename": event.get("_sent_filename", ""),
        },
    })

calendar_options = {
    "initialView": "dayGridMonth",
    "headerToolbar": {
        "left": "prev,next today",
        "center": "title",
        "right": "dayGridMonth,timeGridWeek,timeGridDay,listMonth",
    },
    "editable": False,
    "selectable": False,
    "nowIndicator": True,
    "height": 760,
    "eventDisplay": "block",
    "dayMaxEvents": True,
    "slotMinTime": "05:00:00",
    "slotMaxTime": "22:00:00",
    "firstDay": 1,
    "timeZone": "local",
}

calendar(
    events=calendar_events,
    options=calendar_options,
    key="dds_transport_calendar",
)


# ============================================================
# ADD A BOOKING
# ============================================================

with st.expander("➕ Add a booking"):
    with st.form("add_booking", clear_on_submit=True):
        title = st.text_input("Booking / delivery site")

        c1, c2 = st.columns(2)
        booking_date = c1.date_input("Date", value=date.today())
        booking_time = c2.time_input(
            "Planned time",
            value=time(8, 0),
        )

        c3, c4 = st.columns(2)
        source = c3.selectbox(
            "Source",
            ["Manual", "Campey", "Bulk"],
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
                st.error("Please enter a booking or delivery site.")
            else:
                start_dt = datetime.combine(
                    booking_date, booking_time
                ).replace(tzinfo=UK)

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
# IMPORT CAMPEY WORKBOOK
# ============================================================

with st.expander("📥 Import Campey transport workbook"):
    uploaded = st.file_uploader(
        "Select the Excel workbook",
        type=["xlsx", "xls"],
        key="campey_excel",
    )

    if uploaded is not None:
        file_bytes = uploaded.getvalue()
        file_hash = hashlib.sha256(file_bytes).hexdigest()

        try:
            imported_hashes = get_imported_hashes()
        except Exception as exc:
            imported_hashes = set()
            st.warning(f"Could not check import history: {exc}")

        if file_hash in imported_hashes:
            st.info("This exact workbook has already been imported.")
        else:
            try:
                parsed = parse_workbook(file_bytes)

                st.write(f"Found **{len(parsed)}** booking rows.")

                if parsed:
                    preview = pd.DataFrame([
                        {
                            "Date": row["start"].strftime("%d/%m/%Y"),
                            "Planned time": row["start"].strftime("%H:%M"),
                            "Del Site": row["title"],
                        }
                        for row in parsed
                    ])

                    st.dataframe(
                        preview,
                        use_container_width=True,
                        hide_index=True,
                    )

                    if st.button(
                        "Import these bookings",
                        type="primary",
                        key="import_campey_button",
                    ):
                        try:
                            existing = get_events()

                            existing_keys = set()

                            for event in existing:
                                start_dt = local_datetime(event.get("start"))

                                if start_dt:
                                    existing_keys.add((
                                        str(event.get("title", "")).strip().casefold(),
                                        start_dt.isoformat(),
                                        event.get("source", "Manual"),
                                    ))

                            rows_to_insert = []

                            for row in parsed:
                                key = (
                                    row["title"].strip().casefold(),
                                    row["start"].isoformat(),
                                    "Campey",
                                )

                                if key in existing_keys:
                                    continue

                                rows_to_insert.append({
                                    "id": str(uuid.uuid4()),
                                    "title": row["title"],
                                    "start": row["start"].isoformat(),
                                    "end": row["end"].isoformat(),
                                    "source": "Campey",
                                    "colour": BLUE,
                                })

                                existing_keys.add(key)

                            if rows_to_insert:
                                supabase.table("events").insert(
                                    rows_to_insert
                                ).execute()

                            supabase.table("imported_files").upsert({
                                "file_hash": file_hash,
                                "filename": uploaded.name,
                                "imported_at": datetime.now(UK).isoformat(),
                            }).execute()

                            st.success(
                                f"Imported {len(rows_to_insert)} new bookings. "
                                "Existing matching bookings were skipped."
                            )
                            st.rerun()

                        except Exception as exc:
                            st.error(f"Import failed: {exc}")

                else:
                    st.warning(
                        "No bookings found. The workbook needs Date, "
                        "Planned Time and Del Site columns."
                    )

            except Exception as exc:
                st.error(f"Could not read the workbook: {exc}")


# ============================================================
# EDIT OR DELETE A BOOKING
# ============================================================

with st.expander("✏️ Edit or delete a booking"):
    if not events:
        st.info("There are no bookings to edit.")
    else:
        choices = {}

        for event in events:
            start_dt = local_datetime(event.get("start"))

            if start_dt:
                label = (
                    f"{start_dt:%d/%m/%Y %H:%M} — "
                    f"{event.get('title', '(untitled)')} "
                    f"[{event.get('source', 'Manual')}]"
                )
            else:
                label = event.get("title", "(untitled)")

            # Avoid label collisions if two bookings look identical.
            choices[f"{label} · {event['id'][:8]}"] = event

        selected_label = st.selectbox(
            "Choose a booking",
            list(choices.keys()),
        )

        selected = choices[selected_label]
        selected_start = local_datetime(selected.get("start")) or now
        selected_end = (
            local_datetime(selected.get("end"))
            or selected_start + timedelta(hours=1)
        )

        with st.form("edit_booking"):
            edit_title = st.text_input(
                "Booking / delivery site",
                value=selected.get("title", ""),
            )

            ec1, ec2 = st.columns(2)
            edit_date = ec1.date_input(
                "Date",
                value=selected_start.date(),
                key="edit_booking_date",
            )
            edit_time = ec2.time_input(
                "Planned time",
                value=selected_start.time().replace(tzinfo=None),
                key="edit_booking_time",
            )

            ec3, ec4 = st.columns(2)

            source_options = ["Manual", "Campey", "Bulk"]
            old_source = selected.get("source", "Manual")

            edit_source = ec3.selectbox(
                "Source",
                source_options,
                index=(
                    source_options.index(old_source)
                    if old_source in source_options else 0
                ),
                key="edit_booking_source",
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
                key="edit_booking_duration",
            )

            save_clicked = st.form_submit_button(
                "Save changes",
                type="primary",
            )

            if save_clicked:
                if not edit_title.strip():
                    st.error("Please enter a booking or delivery site.")
                else:
                    new_start = datetime.combine(
                        edit_date, edit_time
                    ).replace(tzinfo=UK)

                    try:
                        save_event(
                            edit_title,
                            new_start,
                            new_start + timedelta(hours=float(edit_duration)),
                            edit_source,
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
                    "id", selected["id"]
                ).execute()

                st.success("Booking deleted.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not delete booking: {exc}")


# ============================================================
# SENT LOAD DETAILS
# ============================================================

with st.expander("📄 Sent-load CSV details"):
    st.caption(
        f"Read-only source: {SENDER_REPO}/saved_loads. "
        "Results are cached for 60 seconds."
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
        "Matching rule: sent CSVs are grouped by their Date, then matched "
        "to Campey bookings on the same day. The earliest sent load is "
        "assigned to the earliest planned booking, one load per booking."
    )

st.caption(
    "Bookings are shared through Supabase. If another user changes the "
    "calendar, refresh the page to see the latest bookings."
)
