"""Weekly validity must remain separate from source re-verification."""
import json,unittest
from datetime import datetime
from pathlib import Path
from brief.adapters import deal

class WeeklyDealsTests(unittest.TestCase):
 def offer(self):
  return dict(retailer='AH Belgium',product='Synthetic weekly product',price=2,quantity=.5,unit='kg',validFrom='2026-09-28',validTo='2026-10-04',sourceUrl='https://www.ah.be/bonus',checkedAt='2026-09-30T07:50:00+02:00',availability='Synthetic test')
 def test_weeklong_offer_is_valid_on_each_day_and_expires(self):
  for date in ['2026-09-28','2026-09-30','2026-10-04']:
   self.assertEqual(deal(self.offer(),date)['unitPrice'],4)
  self.assertIsNone(deal(self.offer(),'2026-09-27'))
  self.assertIsNone(deal(self.offer(),'2026-10-05'))
 def test_weekly_offer_requires_reverification_but_not_new_validity_dates(self):
  from brief.validation import validate_bundle
  now=datetime.fromisoformat('2026-09-30T08:00:00+02:00')
  b=json.loads((Path(__file__).parents[1]/'fixtures/synthetic.json').read_text(encoding='utf-8-sig'))
  b['deals']={'status':'verified','checkedAt':now.isoformat(),'items':[self.offer()]}
  validate_bundle(b,now)
  b['deals']['items'][0]['checkedAt']='2026-09-28T07:50:00+02:00'
  with self.assertRaises(ValueError):validate_bundle(b,now)
  b['deals']['items'][0]['checkedAt']=now.isoformat()
  validate_bundle(b,now)
