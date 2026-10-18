
import sys
import types
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from utils.dates import UK


# Provide a fake events module so importing the service does not
# require live Supabase credentials.
_fake_events = types.ModuleType("services.events")
_fake_events.get_events = Mock(return_value=[])
_fake_events.remember_import = Mock()
_fake_events.supabase = Mock()

_previous_events_module = sys.modules.get("services.events")
sys.modules["services.events"] = _fake_events

try:
    from services import imports as booking_imports
finally:
    if _previous_events_module is None:
        sys.modules.pop("services.events", None)
    else:
        sys.modules["services.events"] = _previous_events_module


class TestImportBookings(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 5, 4, 8, 30, tzinfo=UK)
        self.parsed = [{
            "title": "Pontefract",
            "start": self.start,
            "end": None,
            "source": "Campey",
        }]

        self.database = Mock()

        self.table = self.database.table.return_value
        self.insert_query = self.table.insert.return_value

        self.get_events_patcher = patch.object(
            booking_imports, "get_events", return_value=[]
        )
        self.remember_patcher = patch.object(
            booking_imports, "remember_import"
        )
        self.supabase_patcher = patch.object(
            booking_imports, "supabase", self.database
        )

        self.get_events_mock = self.get_events_patcher.start()
        self.remember_mock = self.remember_patcher.start()
        self.supabase_patcher.start()

        self.addCleanup(self.get_events_patcher.stop)
        self.addCleanup(self.remember_patcher.stop)
        self.addCleanup(self.supabase_patcher.stop)

    def test_inserts_new_booking(self):
        count = booking_imports.import_bookings(
            self.parsed, "hash-123", "bookings.xlsx"
        )

        self.assertEqual(count, 1)
        self.database.table.assert_called_with("events")

        inserted_rows = self.table.insert.call_args.args[0]
        self.assertEqual(len(inserted_rows), 1)
        self.assertEqual(inserted_rows[0]["title"], "Pontefract")
        self.assertEqual(inserted_rows[0]["source"], "Campey")
        self.assertEqual(inserted_rows[0]["status"], "scheduled")
        self.assertEqual(
            inserted_rows[0]["start"],
            self.start.isoformat(),
        )
        self.assertIsNone(inserted_rows[0]["end"])
        self.assertTrue(inserted_rows[0]["id"])

        self.remember_mock.assert_called_once_with(
            "hash-123", "bookings.xlsx"
        )
        self.insert_query.execute.assert_called_once()

    def test_skips_existing_booking(self):
        self.get_events_mock.return_value = [{
            "title": "PONTEFRACT",
            "start": self.start.isoformat(),
            "source": "Campey",
        }]

        count = booking_imports.import_bookings(
            self.parsed, "hash-456", "duplicate.xlsx"
        )

        self.assertEqual(count, 0)
        self.table.insert.assert_not_called()
        self.remember_mock.assert_called_once_with(
            "hash-456", "duplicate.xlsx"
        )

    def test_skips_duplicates_within_same_upload(self):
        duplicate_rows = self.parsed + [dict(self.parsed[0])]

        count = booking_imports.import_bookings(
            duplicate_rows, "hash-789", "repeated.xlsx"
        )

        self.assertEqual(count, 1)
        inserted_rows = self.table.insert.call_args.args[0]
        self.assertEqual(len(inserted_rows), 1)

    def test_different_sources_are_not_duplicates(self):
        self.get_events_mock.return_value = [{
            "title": "Pontefract",
            "start": self.start.isoformat(),
            "source": "Campey",
        }]

        bulk_booking = [{
            "title": "Pontefract",
            "start": self.start,
            "end": None,
            "source": "Bulk",
        }]

        count = booking_imports.import_bookings(
            bulk_booking, "hash-101", "bulk.xlsx"
        )

        self.assertEqual(count, 1)
        inserted_rows = self.table.insert.call_args.args[0]
        self.assertEqual(inserted_rows[0]["source"], "Bulk")


if __name__ == "__main__":
    unittest.main()
