
import unittest
from datetime import date, datetime, time

from utils.dates import UK, local_datetime, parse_date, parse_time


class TestParseDate(unittest.TestCase):
    def test_uk_day_first_date(self):
        self.assertEqual(
            parse_date("04/05/2026"),
            date(2026, 5, 4),
        )

    def test_iso_date(self):
        self.assertEqual(
            parse_date("2026-12-31"),
            date(2026, 12, 31),
        )

    def test_datetime_input(self):
        self.assertEqual(
            parse_date(datetime(2026, 5, 4, 14, 30)),
            date(2026, 5, 4),
        )

    def test_empty_or_invalid_date(self):
        self.assertIsNone(parse_date(None))
        self.assertIsNone(parse_date(""))
        self.assertIsNone(parse_date("not a date"))


class TestParseTime(unittest.TestCase):
    def test_text_time(self):
        self.assertEqual(parse_time("08:30"), time(8, 30))

    def test_decimal_style_time(self):
        self.assertEqual(parse_time(13.3), time(13, 30))

    def test_whole_hour(self):
        self.assertEqual(parse_time(14), time(14, 0))

    def test_excel_time_fraction(self):
        self.assertEqual(parse_time(0.5), time(12, 0))

    def test_invalid_time(self):
        self.assertIsNone(parse_time(None))
        self.assertIsNone(parse_time("not a time"))


class TestLocalDatetime(unittest.TestCase):
    def test_naive_datetime_gets_uk_timezone(self):
        result = local_datetime("2026-05-04 08:30")

        self.assertIsNotNone(result)
        self.assertEqual(result.tzinfo, UK)
        self.assertEqual(result.hour, 8)
        self.assertEqual(result.minute, 30)

    def test_invalid_datetime(self):
        self.assertIsNone(local_datetime(None))
        self.assertIsNone(local_datetime("not a date"))


if __name__ == "__main__":
    unittest.main()
