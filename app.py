
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
# CONFIGURATION
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

SOURCE_LABELS = {
    "Campey": "Campey",
    "Bulk": "Bulk Inbound",
    "Manual": "Manual",
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

    # Excel time stored as a fraction of a day.
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


def clean_cell(value):
    if value is None or pd.isna(value):
        return ""

    if isinstance(value, float) and value.is_integer():
        return str(int(value))

    return str(value).strip()


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


def save_event(title, start_dt, end_dt, source, event_id=None):
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


def remember_import(file_hash, filename):
    supabase.table("imported_files").upsert({
        "file_hash": file_hash,
        "filename": filename,
        "imported_at": datetime.now(UK).isoformat(),
    }).execute()


# ============================================================
# CAMPEY WORKBOOK IMPORTER
# ============================================================

def parse_campey_workbook(file_bytes):
    workbook = pd.ExcelFile(io.BytesIO(file_bytes))
    bookings = {}

    for sheet_name in workbook.sheet_names:
        raw = pd.read_excel(
            workbook,
            sheet_name=sheet_name,
            header=None,
        )

        header_row = None

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
# BULK INBOUND WORKBOOK IMPORTER
# Reads weekly sheets with weekday column groups:
# time | supplier | product | order / quantity
# ============================================================

def parse_bulk_workbook(file_bytes):
    workbook = pd.ExcelFile(io.BytesIO(file_bytes))
    bookings = {}

    # The weekly tabs contain five weekday groups, normally four
    # columns per group. The date is in the first row of each group,
    # in the supplier/detail column immediately after the time column.
    for sheet_name in workbook.sheet_names:
        if sheet_name.strip().lower() in {"maris", "rejected"}:
            continue

        raw = pd.read_excel(
            workbook,
            sheet_name=sheet_name,
            header=None,
        )

        if raw.empty or len(raw.index) < 4:
            continue

        # Monday-Friday groups start at columns 0, 4, 8, 12 and 16.
        for base_col in (0, 4, 8, 12, 16):
            date_col = base_col + 1

            if date_col >= raw.shape[1]:
                continue

            booking_date = parse_date(raw.iat[0, date_col])

            if booking_date is None:
                continue

            # Rows 0-2 contain dates, weekday names and summary headings.
            for row_index in range(3, len(raw.index)):
                if base_col >= raw.shape[1]:
                    continue

                booking_time = parse_time(raw.iat[row_index, base_col])

                # Entries such as TBC or text instructions do not
                # have a definite planned time, so they are skipped.
                if booking_time is None:
                    continue

                details = []

                for col_index in range(base_col + 1, base_col + 4):
                    if col_index >= raw.shape[1]:
                        continue

                    text_value = clean_cell(
                        raw.iat[row_index, col_index]
                    )

                    if text_value:
                        details.append(text_value)

                if not details:
                    continue

                title = " · ".join(details)
                title_lower = title.casefold()

                # Avoid importing instruction-only cells as bookings.
                if any(
                    phrase in title_lower
                    for phrase in (
                        "no deliveries",
                        "engagement day",
                        "bank holiday",
                        "do not forget",
                    )
                ):
                    continue

                start_dt = datetime.combine(
                    booking_date, booking_time
                ).replace(tzinfo=UK)

                key = (
                    title.casefold(),
                    start_dt.isoformat(),
                )

                bookings[key] = {
                    "title": title,
                    "start": start_dt,
                    "end": start_dt + timedelta(hours=1),
                    # The existing Supabase source constraint permits
                    # Campey, Bulk and Manual. Bulk is displayed as
                    # "Bulk Inbound" throughout the app.
                    "source": "Bulk",
                    "colour": AMBER,
                }

    return list(bookings.values())


# ============================================================
# SENT-LOAD CSV CHECK — CAMPEY ONLY
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

        # Fallback for filenames such as H43474_20261002_224101.csv.
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

    loads.sort(key=lambda load: (load["sent_at"], load["filename"]))
    return loads


def decorate_events(events, sent_loads, now):
    """Only Campey events receive green/red sent-load status."""

    loads_by_day = {}

    for load in sent_loads:
        loads_by_day.setdefault(load["sent_at"].date(), []).append(load)

    campey_by_day = {}

    for event in events:
        if event.get("source", "Manual") != "Campey":
            continue

        start_dt = local_datetime(event.get("start"))

        if start_dt:
            campey_by_day.setdefault(
                start_dt.date(), []
            ).append((start_dt, event))

    decorated = []

    # Match CSV loads to Campey bookings, one-to-one, by day.
    for day, bookings in campey_by_day.items():
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
                item["_display_colour"] = RED if start_dt < now else BLUE

            decorated.append(item)

    # Bulk Inbound and Manual events keep their own colours.
    # They are never assigned green or red status.
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
# SHARED IMPORT HANDLER
# ============================================================

def import_bookings(parsed, file_hash, filename):
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
        source = row["source"]

        key = (
            row["title"].strip().casefold(),
            row["start"].isoformat(),
            source,
        )

        if key in existing_keys:
            continue

        rows_to_insert.append({
            "id": str(uuid.uuid4()),
            "title": row["title"],
            "start": row["start"].isoformat(),
            "end": row["end"].isoformat(),
            "source": source,
            "colour": SOURCE_COLOURS.get(source, PURPLE),
        })

        existing_keys.add(key)

    if rows_to_insert:
        # Insert in batches to avoid oversized requests.
        for offset in range(0, len(rows_to_insert), 100):
            supabase.table("events").insert(
                rows_to_insert[offset:offset + 100]
            ).execute()

    remember_import(file_hash, filename)

    return len(rows_to_insert)


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
        "Campey sent/overdue colours may be out of date. "
        f"Details: {github_error}"
    )

sent_count = sum(
    1 for event in display_events if event.get("_sent")
)

overdue_count = sum(
    1
    for event in display_events
    if event.get("source") == "Campey"
    and not event.get("_sent")
    and (local_datetime(event.get("start")) or now) < now
)

m1, m2, m3 = st.columns(3)
m1.metric("Total bookings", len(events))
m2.metric("Campey loads matched", sent_count)
m3.metric("Past unmatched Campey bookings", overdue_count)

with st.expander("Calendar colour key"):
    st.markdown(
        f'<span style="color:{BLUE}">■</span> Upcoming Campey &nbsp; '
        f'<span style="color:{GREEN}">■</span> Campey sent &nbsp; '
        f'<span style="color:{RED}">■</span> Campey overdue &nbsp; '
        f'<span style="color:{AMBER}">■</span> Bulk Inbound &nbsp; '
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
            "source": SOURCE_LABELS.get(
                event.get("source", "Manual"),
                event.get("source", "Manual"),
            ),
            "originalTitle": event.get("title", ""),
            "sent": event.get("_sent", False),
            "sentFilename": event.get("_sent_filename", ""),
        },
    })

calendar(
    events=calendar_events,
    options={
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
    },
    key="dds_transport_calendar",
)


# ============================================================
# ADD A BOOKING
# ============================================================

with st.expander("➕ Add a booking"):
    with st.form("add_booking", clear_on_submit=True):
        title = st.text_input("Booking / delivery details")

        c1, c2 = st.columns(2)
        booking_date = c1.date_input("Date", value=date.today())
        booking_time = c2.time_input("Planned time", value=time(8, 0))

        c3, c4 = st.columns(2)
        source_label = c3.selectbox(
            "Source",
            ["Manual", "Campey", "Bulk Inbound"],
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
                    booking_date, booking_time
                ).replace(tzinfo=UK)

                source = (
                    "Bulk" if source_label == "Bulk Inbound"
                    else source_label
                )

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
    campey_file = st.file_uploader(
        "Select the Campey Excel workbook",
        type=["xlsx", "xls"],
        key="campey_excel",
    )

    if campey_file is not None:
        file_bytes = campey_file.getvalue()
        file_hash = hashlib.sha256(file_bytes).hexdigest()

        try:
            already_imported = file_hash in get_imported_hashes()
        except Exception as exc:
            already_imported = False
            st.warning(f"Could not check import history: {exc}")

        if already_imported:
            st.info("This exact workbook has already been imported.")
        else:
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
                        "Import Campey bookings",
                        type="primary",
                        key="import_campey",
                    ):
                        try:
                            count = import_bookings(
                                parsed, file_hash, campey_file.name
                            )
                            st.success(
                                f"Imported {count} new Campey bookings. "
                                "Matching existing bookings were skipped."
                            )
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Import failed: {exc}")
                else:
                    st.warning(
                        "No bookings found. Check the Date, Planned Time "
                        "and Del Site columns in the workbook."
                    )

            except Exception as exc:
                st.error(f"Could not read the Campey workbook: {exc}")


# ============================================================
# IMPORT BULK INBOUND WORKBOOK
# ============================================================

with st.expander("📦 Import Bulk Inbound schedule"):
    st.write(
        "Upload the weekly Bulk Schedule workbook. Entries are imported "
        "in amber and are not affected by Campey sent-load or overdue status."
    )

    bulk_file = st.file_uploader(
        "Select the Bulk Schedule Excel workbook",
        type=["xlsx", "xls"],
        key="bulk_excel",
    )

    if bulk_file is not None:
        bulk_bytes = bulk_file.getvalue()
        bulk_hash = hashlib.sha256(bulk_bytes).hexdigest()

        try:
            already_imported = bulk_hash in get_imported_hashes()
        except Exception as exc:
            already_imported = False
            st.warning(f"Could not check import history: {exc}")

        if already_imported:
            st.info("This exact Bulk Schedule workbook has already been imported.")
        else:
            try:
                parsed_bulk = parse_bulk_workbook(bulk_bytes)

                st.write(
                    f"Found **{len(parsed_bulk)}** timed Bulk Inbound entries."
                )

                if parsed_bulk:
                    bulk_preview = pd.DataFrame([
                        {
                            "Date": row["start"].strftime("%d/%m/%Y"),
                            "Time": row["start"].strftime("%H:%M"),
                            "Bulk Inbound details": row["title"],
                        }
                        for row in parsed_bulk
                    ])

                    st.dataframe(
                        bulk_preview,
                        use_container_width=True,
                        hide_index=True,
                    )

                    if st.button(
                        "Import Bulk Inbound bookings",
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
                                f"Imported {count} new Bulk Inbound entries. "
                                "Matching existing entries were skipped."
                            )
                            st.rerun()

                        except Exception as exc:
                            st.error(f"Bulk import failed: {exc}")

                else:
                    st.warning(
                        "No timed entries were found. Check that the workbook "
                        "contains weekly tabs with dates, times and booking details."
                    )

            except Exception as exc:
                st.error(f"Could not read the Bulk Schedule workbook: {exc}")


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
        selected_start = local_datetime(selected.get("start")) or now
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

            source_options = ["Manual", "Campey", "Bulk Inbound"]
            current_source = selected.get("source", "Manual")
            current_label = SOURCE_LABELS.get(
                current_source, current_source
            )

            edit_source_label = ec3.selectbox(
                "Source",
                source_options,
                index=(
                    source_options.index(current_label)
                    if current_label in source_options else 0
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
                        edit_date, edit_time
                    ).replace(tzinfo=UK)

                    new_source = (
                        "Bulk"
                        if edit_source_label == "Bulk Inbound"
                        else edit_source_label
                    )

                    try:
                        save_event(
                            edit_title,
                            new_start,
                            new_start + timedelta(hours=float(edit_duration)),
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
                    "id", selected["id"]
                ).execute()

                st.success("Booking deleted.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not delete booking: {exc}")


# ============================================================
# SENT-LOAD DETAILS
# ============================================================

with st.expander("📄 Campey sent-load CSV details"):
    st.caption(
        f"Read-only source: {SENDER_REPO}/saved_loads. "
        "The CSV list refreshes every 60 seconds."
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
        "Only Campey bookings are matched to sent-load CSVs. "
        "Bulk Inbound bookings remain amber regardless of date."
    )

st.caption(
    "Bookings are shared through Supabase. Refresh the page to load "
    "changes made by other users."
)
