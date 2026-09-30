import base64,copy,json,tempfile,unittest
from datetime import datetime
from pathlib import Path
from werkzeug.security import generate_password_hash
from brief.store import Store
from brief.pipeline import run,clock
from brief.adapters import canonical_url,newsletter_text,deal,digest
from brief.editorial import prepare,approve,approved,reviewed_language
from brief.push import notify,validate_subscription
from brief.web import create_app
NOW=datetime.fromisoformat('2026-09-30T08:00:00+02:00')
class Tests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'state.sqlite');self.bundle=json.loads((Path(__file__).parents[1]/'fixtures/synthetic.json').read_text(encoding='utf-8-sig'))
 def tearDown(self):self.temp.cleanup()
 def test_dst(self):
  for date,hour in [('2026-03-28',7),('2026-03-29',6),('2026-10-24',6),('2026-10-25',7)]:self.assertEqual(clock(datetime.fromisoformat(f'{date}T{hour:02}:00:00+00:00')).hour,8)
 def test_early_and_idempotent(self):
  self.assertEqual(run(self.store,self.bundle,datetime.fromisoformat('2026-09-30T07:59:00+02:00'))['status'],'not_due');self.assertEqual(run(self.store,self.bundle,NOW)['status'],'published');self.assertEqual(run(self.store,self.bundle,NOW)['status'],'unchanged')
 def test_stale_and_wrong_date(self):
  self.bundle['calendar']['checkedAt']='2026-09-28T07:50:00+02:00'
  with self.assertRaises(ValueError):run(self.store,self.bundle,NOW)
  self.bundle['date']='2026-09-29'
  with self.assertRaises(ValueError):run(self.store,self.bundle,NOW)
 def test_tracking_and_html(self):
  self.assertIsNone(canonical_url('https://interactief.standaard.be/optiext/optiextension.dll?ID=secret'));self.assertEqual(canonical_url('https://www.standaard.be/cnt/test?recipient=secret'),'https://www.standaard.be/cnt/test');self.assertIsNone(canonical_url('https://evil.test/cnt/test'))
  self.assertEqual(newsletter_text('dshelpdesk@mail.standaard.be','<!--secret--><style>secret</style><p>Kop</p><img src="tracking?ID=secret"><script>secret</script><footer>secret</footer>'),'Kop')
  with self.assertRaises(ValueError):newsletter_text('info@service.nieuwsblad.be','hello')
 def test_scope_and_dedup(self):
  self.bundle['news']['items']*=2;run(self.store,self.bundle,NOW)
  with self.store.connect() as db:payload=json.loads(db.execute('SELECT payload FROM briefs').fetchone()[0])
  self.assertEqual(len(payload['news']),1);self.assertNotIn('authorizedText',json.dumps(payload));self.bundle['news']['items'][0]['contentScope']='full_authorized'
  with self.assertRaises(ValueError):run(self.store,self.bundle,NOW)
 def test_language_cache_and_selection(self):
  text=self.bundle['news']['items'][0]['authorizedText'];self.bundle['news']['items'][0]['reviewedLanguage']={'sourceHash':digest(text),'summaryNl':'Fictieve bewoners delen boeken.','vocabulary':[{'word':'samenbrengen','meaning':'verbinden'}],'puzzle':[{'question':'Wat wisselen ze uit?','answer':'Boeken.','explanation':'Dat staat in de teaser.','evidence':'bewoners boeken uit'}]};run(self.store,self.bundle,NOW,generator=reviewed_language);self.bundle['todos']['items'].append({'title':'Fictieve taak'})
  self.assertEqual(run(self.store,self.bundle,NOW,generator=lambda *a:self.fail('Should use cache'))['languageCalls'],0);self.bundle['news']['items'][0]['include']=False;run(self.store,self.bundle,NOW)
  with self.store.connect() as db:self.assertEqual(json.loads(db.execute('SELECT payload FROM briefs').fetchone()[0])['news'],[])
 def test_approval_invalidated(self):
  p=prepare(self.store,self.bundle);approve(self.store,self.bundle['date'],p['hash']);self.assertEqual(approved(self.store,self.bundle['date']),self.bundle);self.bundle['learning']['level']='B2+';prepare(self.store,self.bundle)
  with self.assertRaises(ValueError):approved(self.store,self.bundle['date'])
 def test_deals(self):
  offer={'retailer':'AH Belgium','product':'Fictief','price':2,'quantity':.5,'unit':'kg','validFrom':'2026-09-29','validTo':'2026-10-01','sourceUrl':'https://www.ah.be/bonus','checkedAt':NOW.isoformat(),'availability':'fictief'};result=deal(offer,'2026-09-30');self.assertEqual(result['unitPrice'],4);self.assertNotIn('discountPercent',result);self.assertIsNone(deal(offer,'2026-10-02'))
 def app(self):return create_app({'TESTING':True,'SECRET_KEY':'synthetic','PASSWORD_HASH':generate_password_hash('synthetic'),'DATABASE':self.store.path,'PUBLIC_ORIGIN':'https://brief.test'})
 def login(self,c):
  c.get('/login')
  with c.session_transaction() as s:token=s['csrf']
  return c.post('/login',data={'password':'synthetic','csrf':token},headers={'Origin':'https://brief.test'})
 def test_auth_boundary(self):
  c=self.app().test_client()
  for path in ['/','/assets/app.js','/sw.js','/api/briefs','/api/briefs/2026-09-30','/api/push/config']:
   r=c.get(path);self.assertIn(r.status_code,(302,401));self.assertIn('no-store',r.headers['Cache-Control'])
 def test_cookie_csrf_logout(self):
  c=self.app().test_client();r=self.login(c);self.assertEqual(r.status_code,302)
  for flag in ('Secure','HttpOnly','SameSite=Strict'):self.assertIn(flag,r.headers['Set-Cookie'])
  self.assertEqual(c.get('/api/briefs').status_code,200);self.assertEqual(c.post('/logout').status_code,403)
  with c.session_transaction() as s:token=s['csrf']
  self.assertEqual(c.post('/logout',data={'csrf':token},headers={'Origin':'https://evil.test'}).status_code,403);c.post('/logout',data={'csrf':token},headers={'Origin':'https://brief.test'});self.assertEqual(c.get('/api/briefs').status_code,401)
 def test_rate_limit(self):
  c=self.app().test_client();c.get('/login')
  with c.session_transaction() as s:token=s['csrf']
  for i in range(5):self.assertEqual(c.post('/login',data={'password':'wrong','csrf':token},headers={'Origin':'https://brief.test'}).status_code,401)
  self.assertEqual(c.post('/login',data={'password':'wrong','csrf':token},headers={'Origin':'https://brief.test'}).status_code,429)
 def sub(self):
  enc=lambda n:base64.urlsafe_b64encode(bytes(n)).decode().rstrip('=');return {'endpoint':'https://fcm.googleapis.com/fcm/send/synthetic','keys':{'auth':enc(16),'p256dh':enc(65)}}
 def test_push_ssrf(self):
  sub=self.sub();sub['endpoint']='https://127.0.0.1/private'
  with self.assertRaises(ValueError):validate_subscription(sub)
 def test_notify_only_ready_once(self):
  with self.store.connect() as db:db.execute('INSERT INTO subscriptions VALUES (?,?)',('test',json.dumps(self.sub())))
  config={'VAPID_PRIVATE_KEY':'synthetic','VAPID_SUBJECT':'mailto:test@example.invalid'};sent=[];sender=lambda **kw:sent.append(kw)
  self.assertEqual(notify(self.store,config,NOW,sender=sender)['sent'],0);run(self.store,self.bundle,NOW);self.assertEqual(notify(self.store,config,NOW,sender=sender)['sent'],1);self.assertEqual(notify(self.store,config,NOW,sender=sender)['sent'],0);self.assertEqual(json.loads(sent[0]['data']),{'date':'2026-09-30'})
 def test_unsubscribe(self):
  c=self.app().test_client();self.login(c)
  with c.session_transaction() as s:token=s['csrf']
  h={'Origin':'https://brief.test','X-CSRF-Token':token};sub=self.sub();self.assertEqual(c.post('/api/push/subscribe',json=sub,headers=h).status_code,200);self.assertEqual(c.post('/api/push/unsubscribe',json={'endpoint':sub['endpoint']},headers=h).status_code,200);self.assertEqual(c.post('/api/push/subscribe',json={},headers=h).status_code,400)


class FailureTests(unittest.TestCase):
 setUp=Tests.setUp
 tearDown=Tests.tearDown
 def test_revoked_push_removed(self):
  with self.store.connect() as db:db.execute('INSERT INTO subscriptions VALUES (?,?)',('test',json.dumps(Tests.sub(self))))
  run(self.store,self.bundle,NOW)
  class Revoked(Exception):
   response=type('Response',(),{'status_code':410})()
  def sender(**kw):raise Revoked()
  result=notify(self.store,{'VAPID_PRIVATE_KEY':'synthetic','VAPID_SUBJECT':'mailto:test@example.invalid'},NOW,sender=sender)
  self.assertEqual(result['sent'],0)
  with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM subscriptions').fetchone()[0],0)
 def test_failed_publish_no_notification(self):
  self.bundle['calendar']['checkedAt']='2026-09-01T08:00:00+02:00'
  with self.assertRaises(ValueError):run(self.store,self.bundle,NOW)
  self.assertEqual(notify(self.store,{'VAPID_PRIVATE_KEY':'synthetic'},NOW,sender=lambda **kw:self.fail('Must not send'))['status'],'no_current_brief')
 def test_cached_ingestion(self):
  from brief.ingest import cached_source
  count=[]
  fetch=lambda:count.append(1) or []
  cached_source(self.store,'synthetic','2026-09-30',fetch);cached_source(self.store,'synthetic','2026-09-30',fetch)
  self.assertEqual(len(count),1)
 def test_unavailable_items_do_not_leak(self):
  self.bundle['calendar']['status']='unavailable'
  run(self.store,self.bundle,NOW)
  with self.store.connect() as db:p=json.loads(db.execute('SELECT payload FROM briefs').fetchone()[0])
  self.assertEqual(p['calendar']['items'],[])



if __name__=='__main__':unittest.main()
