import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from brief.offerhunter import read_snapshot
from brief.adapters import deal
from brief.validation import validate_bundle
from brief.publishing import PREFIX
from test_publishing import PublishingTests

NOW = datetime.fromisoformat('2026-10-08T06:01:00+02:00')


def snapshot():
    at = '2026-10-08T04:00:20Z'
    evidence = dict(sourceUrl='https://www.action.com/nl-be/weekactie/', retrievedAt=at, httpStatus=200, cacheAgeSeconds=0, sha256='a'*64)
    item = dict(id='synthetic-promotion', retailer='Action Belgium', productKey='synthetic', name='Synthetic product', pack='500 g', bundlePrice=2, bundleCount=1, quantity=.5, unit='kg', validFrom='2026-10-07', validTo='2026-10-13', checkedAt=at, conditions='Synthetic conditions', sourceUrl='https://www.action.com/nl-be/p/1234/synthetic/', exactVariant=True, evidence=evidence, watchId='never-return', secret='never-return')
    return dict(schemaVersion=2, consumer='daily-brief', preparedAt='2026-10-08T04:00:30Z', receipt='b'*64, returning=[{'offer':item,'watchId':'never-return'}], topOffers=[item], upcomingOffers=[], priceAlerts=[], newFolders=[], coverage=dict(finishedAt=at, sources=[dict(retailer='Action Belgium',state='partial',detail='Synthetic partial coverage',evidence=evidence),dict(retailer='private-source',private=True)]), secret='never-return')


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'summary.json'
        self.data = snapshot()
    def tearDown(self): self.temp.cleanup()
    def read(self, now=NOW):
        self.path.write_text(json.dumps(self.data), encoding='utf-8')
        return read_snapshot(now=now, path=self.path)
    def test_ready_dedup_provenance_no_watchlist_and_validated_deal(self):
        result = self.read()
        self.assertEqual(result['status'], 'ready')
        self.assertNotIn('never-return', json.dumps(result))
        self.assertEqual(len(result['offerhunter']['offers']), 1)
        self.assertEqual(result['offerhunter']['coverage']['sources'][0]['state'], 'partial')
        item = result['offerhunter']['offers'][0]
        self.assertEqual(item['evidence']['sha256'], 'a'*64)
        self.assertEqual(item['briefDeal']['unitPrice'], 4)
        self.assertFalse(result['offerhunter']['deliveryAcknowledged'])
    def test_missing_invalid_oversized_and_timezone_less_are_unavailable(self):
        self.assertEqual(read_snapshot(NOW, self.path)['status'], 'unavailable')
        for raw in ('bad json', 'x'*1000001):
            self.path.write_text(raw); self.assertEqual(read_snapshot(NOW, self.path)['status'], 'unavailable')
        for field in ('preparedAt',):
            self.data = snapshot(); self.data[field] = '2026-10-08T04:00:30'; self.assertEqual(self.read()['status'], 'unavailable')
        self.data = snapshot(); self.data['coverage']['finishedAt'] = '2026-10-08T04:00:20'; self.assertEqual(self.read()['status'], 'unavailable')
        self.data = snapshot(); self.data['schemaVersion'] = 999; self.assertEqual(self.read()['status'], 'unavailable')
    def test_stale_future_boundary_and_dst(self):
        self.data['preparedAt'] = self.data['coverage']['finishedAt'] = '2026-10-07T20:00:00Z'
        self.assertEqual(self.read()['status'], 'stale')
        self.data = snapshot(); self.data['preparedAt'] = '2026-10-08T04:02:00Z'; self.assertEqual(self.read()['status'], 'unavailable')
        self.data = snapshot(); self.data['coverage']['finishedAt'] = '2026-10-08T04:00:40Z'; self.assertEqual(self.read()['status'], 'unavailable')
        for date, offset in [('2026-03-29', '+02:00'), ('2026-10-25', '+01:00')]:
            self.data = snapshot(); self.data['returning'] = []; self.data['topOffers'] = []; self.data['coverage']['sources'] = []
            self.data['preparedAt'] = self.data['coverage']['finishedAt'] = date+'T05:59:59'+offset
            self.assertEqual(self.read(datetime.fromisoformat(date+'T05:59:59'+offset))['status'], 'stale')
            self.assertEqual(self.read(datetime.fromisoformat(date+'T06:00:00'+offset))['status'], 'stale')
            self.data['preparedAt'] = self.data['coverage']['finishedAt'] = date+'T06:00:00'+offset
            self.assertEqual(self.read(datetime.fromisoformat(date+'T06:00:00'+offset))['status'], 'ready')
    def test_expired_future_evidence_and_family_offer_not_promoted_to_exact_deal(self):
        for field,value in [('validTo','2026-10-07'), ('validFrom','2026-10-09'), ('checkedAt','2026-10-08T04:02:00Z'), ('sourceUrl','https://www.action.com/nl-nl/p/1234/x')]:
            self.data = snapshot(); self.data['returning'] = []; self.data['topOffers'][0][field] = value
            r = self.read(); self.assertEqual(r['status'], 'ready'); self.assertEqual(r['offerhunter']['offers'], [])
            self.assertEqual(r['offerhunter']['coverage']['rejectedRecords'], 1)
        self.data = snapshot(); self.data['topOffers'][0]['exactVariant'] = False
        self.assertNotIn('briefDeal', self.read()['offerhunter']['offers'][0])
        self.data = snapshot(); self.data['returning'] = []; future = self.data['topOffers'].pop(); future['validFrom']='2026-10-09'; self.data['upcomingOffers']=[future]
        r=self.read(); self.assertEqual(r['offerhunter']['offers'], []); self.assertNotIn('briefDeal',r['offerhunter']['upcoming'][0])
    def test_action_allowlist_and_existing_publication_validation(self):
        item = self.read()['offerhunter']['offers'][0]['briefDeal']
        for source in ('https://www.action.com/nl-nl/p/1234/x', 'https://www.action.com/nl-be/weekactie/', 'https://evil.test/nl-be/p/1234/x'):
            with self.assertRaises(ValueError): deal({**item,'sourceUrl':source}, NOW.date().isoformat())
        bundle = dict(date=NOW.date().isoformat(), **{k:dict(status='unavailable',items=[]) for k in ('news','todos','calendar')}, deals=dict(status='verified',checkedAt=item['checkedAt'],items=[item]))
        validate_bundle(bundle,NOW)
        bundle['deals']['items'][0]['validFrom']='2026-10-09'
        with self.assertRaises(ValueError): validate_bundle(bundle,NOW)
    def test_current_price_alert_is_not_a_promotion_and_removes_private_fields(self):
        observation=dict(productKey='synthetic',name='Synthetic current price',retailer='Maxi Zoo Belgium',pack='1 kg',quantity=1,unit='kg',price=10,currency='EUR',sourceUrl='https://www.maxizoo.be/nl/p/synthetic/',observedAt='2026-10-08T04:00:20Z',priceType='current_price',promotion=False,secret='never-return',evidence=dict(sourceUrl='https://www.maxizoo.be/nl/p/synthetic/',retrievedAt='2026-10-08T04:00:20Z',httpStatus=200,cacheAgeSeconds=0,sha256='c'*64))
        self.data['priceAlerts']=[dict(id='synthetic',kind='price_drop',previousPrice=12,watchId='never-return',observation=observation)]
        result=self.read();alert=result['offerhunter']['priceAlerts'][0]
        self.assertFalse(alert['observation']['promotion']);self.assertNotIn('briefDeal',alert)
        self.assertNotIn('never-return',json.dumps(result))
        observation['observedAt']='2026-10-08T04:02:00Z'
        self.assertEqual(self.read()['offerhunter']['priceAlerts'],[])


class ReaderBoundaryTests(unittest.TestCase):
    setUp = PublishingTests.setUp
    tearDown = PublishingTests.tearDown
    def test_summary_boundary_fixed_reader_and_unchanged_publication_state(self):
        endpoint = PREFIX+'/sources/offerhunter'
        with patch('brief.offerhunter.read_snapshot', return_value={'status':'stale','retryAfterSeconds':10}) as reader:
            self.assertEqual(self.client.get(endpoint).status_code,401)
            self.assertEqual(self.client.get(endpoint,headers={**self.headers,'Origin':'https://brief.test'}).status_code,403)
            response=self.client.get(endpoint+'?path=/etc/passwd',headers=self.headers)
            self.assertEqual(response.status_code,200); self.assertIn('no-store',response.headers['Cache-Control'])
            reader.assert_called_once_with(now=self.app.config['PUBLISHER_NOW'])
            with self.store.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM drafts').fetchone()[0],0)
                self.assertEqual(db.execute('SELECT count(*) FROM briefs').fetchone()[0],0)
                db.execute('UPDATE publishers SET scopes=?',(json.dumps(['status']),))
            self.assertEqual(self.client.get(endpoint,headers=self.headers).status_code,403)
        self.assertEqual(self.client.post(endpoint,json={},headers=self.headers).status_code,405)

del PublishingTests
