
import io
import unittest
from datetime import date, datetime, time

import pandas as pd

from parsers.campey import parse_campey_workbook
from parsers.bulk import parse_bulk_workbook
from parsers.dhl import (
    get_week_start_from_sheet,
    is_one,
    parse_dhl_header_date,
    parse_dhl_workbook,
)


def make_workbook(sheets):
    """Create an Excel workbook in memory for parser tests."""
    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for name, rows in sheets.items():
            pd.DataFrame(rows).to_excel(
                writer,
                sheet_name=name,
                header=False,
                index=False,
            )

    return output.getvalue()


class TestCampeyParser(unittest.TestCase):
    def test_parses_booking_and_uk_date(self):
        workbook = make_workbook({
            "Bookings": [
                ["Date", "Planned Time", "Del Site"],
                ["04/05/2026", "08:30", "Pontefract"],
            ]
        })

        events = parse_campey_workbook(workbook)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Pontefract")
        self.assertEqual(events[0]["source"], "Campey")
        self.assertEqual(
            events[0]["start"].date(),
            date(2026, 5, 4),
        )
        self.assertEqual(
            events[0]["start"].time().replace(tzinfo=None),
            time(8, 30),
        )

    def test_skips_rows_without_valid_booking_details(self):
        workbook = make_workbook({
            "Bookings": [
                ["Date", "Planned Time", "Del Site"],
                ["04/05/2026", "08:30", "Pontefract"],
                ["04/05/2026", "09:30", None],
                ["not a date", "10:00", "Invalid date"],
            ]
        })

        events = parse_campey_workbook(workbook)

        self.assertEqual(len(events), 1)


class TestBulkParser(unittest.TestCase):
    def test_parses_booking_and_skips_holiday_phrases(self):
        workbook = make_workbook({
            "Monday": [
                ["", "04/05/2026", "", ""],
                ["", "", "", ""],
                ["", "", "", ""],
                ["08:00", "Supplier A", "Product X", "10"],
                ["09:00", "Bank Holiday", "", ""],
                ["10:00", "No deliveries today", "", ""],
            ]
        })

        events = parse_bulk_workbook(workbook)

        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0]["title"],
            "Supplier A · Product X · 10",
        )
        self.assertEqual(events[0]["source"], "Bulk")
        self.assertEqual(
            events[0]["start"].date(),
            date(2026, 5, 4),
        )
        self.assertEqual(
            events[0]["start"].time().replace(tzinfo=None),
            time(8, 0),
        )

    def test_skips_excluded_sheets(self):
        workbook = make_workbook({
            "Maris": [
                ["", "04/05/2026", "", ""],
                ["", "", "", ""],
                ["", "", "", ""],
                ["08:00", "Supplier A", "Product X", "10"],
            ],
            "Rejected": [
                ["", "04/05/2026", "", ""],
                ["", "", "", ""],
                ["", "", "", ""],
                ["08:00", "Supplier B", "Product Y", "5"],
            ],
        })

        self.assertEqual(parse_bulk_workbook(workbook), [])


class TestDHLHelpers(unittest.TestCase):
    def test_parses_uk_header_date(self):
        self.assertEqual(
            parse_dhl_header_date("31/05/2026"),
            date(2026, 5, 31),
        )

    def test_parses_week_start_from_sheet_name(self):
        self.assertEqual(
            get_week_start_from_sheet("Factory W.C. 31052026"),
            date(2026, 5, 31),
        )

    def test_only_numeric_one_counts(self):
        self.assertTrue(is_one(1))
        self.assertTrue(is_one(1.0))
        self.assertFalse(is_one(2))
        self.assertFalse(is_one("1"))
        self.assertFalse(is_one(None))


class TestDHLParser(unittest.TestCase):
    def test_imports_only_pontefract_cells_equal_to_one(self):
        workbook = make_workbook({
            "Week": [
                ["Week commencing", "31/05/2026", "", ""],
                ["MON", 8, 9, "TOTAL"],
                ["PONTEFRACT", 1, 2, 1],
                ["OTHER SITE", 1, 1, 1],
            ]
        })

        events = parse_dhl_workbook(workbook)

        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0]["title"],
            "DHL Collection - Pontefract",
        )
        self.assertEqual(events[0]["source"], "DHL")
        self.assertEqual(
            events[0]["start"],
            datetime(2026, 6, 1, 8, 0, tzinfo=events[0]["start"].tzinfo),
        )

    def test_ignores_workbook_without_week_date(self):
        workbook = make_workbook({
            "Week": [
                ["MON", 8, 9],
                ["PONTEFRACT", 1, 1],
            ]
        })

        self.assertEqual(parse_dhl_workbook(workbook), [])


if __name__ == "__main__":
    unittest.main()
