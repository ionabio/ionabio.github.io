"""The publication gate follows local Brussels time through both DST changes."""
import unittest
from datetime import datetime,timedelta
from brief.pipeline import due,clock

class PublicationTimeTests(unittest.TestCase):
 def test_six_am_boundary_and_dst(self):
  for date,utc_hour in [('2026-03-28',5),('2026-03-29',4),('2026-10-24',4),('2026-10-25',5)]:
   with self.subTest(date=date):
    at=datetime.fromisoformat(f'{date}T{utc_hour:02}:00:00+00:00')
    self.assertEqual(clock(at).hour,6)
    self.assertEqual(clock(at).date().isoformat(),date)
    self.assertFalse(due(at-timedelta(seconds=1)))
    self.assertTrue(due(at))
    self.assertTrue(due(at+timedelta(hours=2)))
 def test_next_local_day_needs_its_own_six_am(self):
  self.assertFalse(due(datetime.fromisoformat('2026-10-07T22:00:00+00:00')))
  self.assertTrue(due(datetime.fromisoformat('2026-10-08T04:00:00+00:00')))
