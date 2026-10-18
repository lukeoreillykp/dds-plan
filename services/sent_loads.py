
import io
import re
from datetime import datetime

import pandas as pd
import requests
import streamlit as st

from config import SAVED_LOADS_API, UK


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

    loads = []

    for item in response.json():
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

    loads.sort(
        key=lambda load: (load["sent_at"], load["filename"])
    )

    return loads
