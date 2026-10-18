import unittest
from datetime import datetime

from config import BLUE, GREEN, RED
from services.calendar_events import decorate_events
from utils.dates import UK

class TestDecorateEvents(unittest.TestCase):
def setUp(self):
self.now = datetime(2026, 5, 4, 12, 0, tzinfo=UK)

def test_campey_booking_matches_sent_load(self):
    events = [{
        "id": "1",
        "title": "Pontefract",
        "start": "2026-05-04T08:30:00+01:00",
        "source": "Campey",
    }]
    sent_loads = [{
        "sent_at": datetime(2026, 5, 4, 9, 0, tzinfo=UK),
        "load_ref": "LOAD123",
        "filename": "load123.csv",
    }]

    result = decorate_events(events, sent_loads, self.now)

    self.assertEqual(len(result), 1)
    self.assertEqual(result[0]["_display_title"], "LOAD123")
    self.assertEqual(result[0]["_display_colour"], GREEN)
    self.assertTrue(result[0]["_sent"])
    self.assertEqual(result[0]["_sent_filename"], "load123.csv")

def test_unsent_past_campey_booking_is_red(self):
    events = [{
        "id": "1",
        "title": "Pontefract",
        "start": "2026-05-04T08:30:00+01:00",
        "source": "Campey",
    }]

    result = decorate_events(events, [], self.now)

    self.assertEqual(result[0]["_display_colour"], RED)
    self.assertFalse(result[0]["_sent"])
    self.assertEqual(result[0]["_display_title"], "Pontefract")

def test_future_campey_booking_is_blue(self):
    events = [{
        "id": "1",
        "title": "Pontefract",
        "start": "2026-05-04T14:30:00+01:00",
        "source": "Campey",
    }]

    result = decorate_events(events, [], self.now)

    self.assertEqual(result[0]["_display_colour"], BLUE)
    self.assertFalse(result[0]["_sent"])

def test_completed_bulk_booking_is_green(self):
    events = [{
        "title": "Supplier A",
        "start": "2026-05-04T08:00:00+01:00",
        "source": "Bulk",
        "status": "complete",
    }]

    result = decorate_events(events, [], self.now)

    self.assertEqual(result[0]["_display_colour"], GREEN)
    self.assertEqual(result[0]["_display_title"], "Supplier A")

def test_cancelled_dhl_booking_is_red_and_shows_load_header(self):
    events = [{
        "title": "DHL Collection - Pontefract",
        "start": "2026-05-04T08:00:00+01:00",
        "source": "DHL",
        "status": "cancelled",
        "load_header": "Load 42",
    }]

    result = decorate_events(events, [], self.now)

    self.assertEqual(result[0]["_display_colour"], RED)
    self.assertEqual(
        result[0]["_display_title"],
        "DHL Collection - Pontefract · Load 42",
    )

def test_event_input_is_not_modified(self):
    events = [{
        "title": "Supplier A",
        "start": "2026-05-04T08:00:00+01:00",
        "source": "Bulk",
        "status": "scheduled",
    }]
    original = dict(events[0])

    decorate_events(events, [], self.now)

    self.assertEqual(events[0], original)

if name == "main":
unittest.main()