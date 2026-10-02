"""Synthetic security/rotation tests: no production identities or data."""
import secrets,tempfile,time,unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime
from urllib.parse import urlencode
from werkzeug.security import generate_password_hash
from brief.enrollment import CLIENT_ID,DEVICE_GRANT,GRANT_SECONDS,SCOPES
from brief.publishing import PREFIX
from brief.web import create_app

class EnrollmentTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.password=secrets.token_urlsafe(32)
  self.app=create_app({'TESTING':True,'ENROLLMENT_ENABLED':True,'PUBLISHER_NOW':datetime.fromisoformat('2026-01-02T08:00:00+01:00'),'SECRET_KEY':secrets.token_urlsafe(48),'PASSWORD_HASH':generate_password_hash(self.password),'DATABASE':str(Path(self.tmp.name)/'private.sqlite3'),'PUBLIC_ORIGIN':'https://brief.test'})
  self.store=self.app.extensions['brief_store'];self.machine=self.app.test_client();self.browser=self.app.test_client()
  self.browser.get('/login',base_url='https://brief.test')
  with self.browser.session_transaction(base_url='https://brief.test') as s: csrf=s['csrf']
  self.assertEqual(self.browser.post('/login',base_url='https://brief.test',data={'password':self.password,'csrf':csrf},headers={'Origin':'https://brief.test'}).status_code,302)
  with self.browser.session_transaction(base_url='https://brief.test') as s:self.csrf=s['csrf']
 def tearDown(self): self.tmp.cleanup()
 def post(self,endpoint,fields,client=None,headers=None):
  return (client or self.machine).post(PREFIX+'/enrollment/'+endpoint,base_url='https://brief.test',data=urlencode({'client_id':CLIENT_ID,**fields}),content_type='application/x-www-form-urlencoded',headers=headers or {})
 def start(self):
  r=self.post('device',{});self.assertEqual(r.status_code,200);return r.json
 def consent(self,code,action='approve',csrf=None):
  return self.browser.post('/connect',base_url='https://brief.test',data={'userCode':code,'action':action,'csrf':self.csrf if csrf is None else csrf},headers={'Origin':'https://brief.test'})
 def exchange(self,d,client=None):return self.post('token',{'grant_type':DEVICE_GRANT,'device_code':d['device_code']},client=client)
 def enroll(self):
  d=self.start();self.assertEqual(self.consent(d['user_code']).status_code,200);r=self.exchange(d);self.assertEqual(r.status_code,200);return r.json
 def refresh(self,t,client=None):return self.post('token',{'grant_type':'refresh_token','refresh_token':t},client=client)
 def status(self,t):return self.machine.get(PREFIX+'/status/2026-01-01',headers={'Authorization':'Bearer '+t}).status_code
 def test_disabled_and_browser_machine_boundary(self):
  self.app.config['ENROLLMENT_ENABLED']=False
  self.assertEqual(self.post('device',{}).status_code,404);self.assertEqual(self.browser.get('/connect',base_url='https://brief.test').status_code,404)
  self.app.config['ENROLLMENT_ENABLED']=True
  self.assertEqual(self.post('device',{},client=self.browser).status_code,403)
  self.assertEqual(self.post('device',{},headers={'Origin':'https://brief.test'}).status_code,403)
  self.assertEqual(self.machine.get('/connect').status_code,302);self.assertEqual(self.machine.post('/connect',data={'action':'approve'}).status_code,403)
 def test_fixed_client_fields_media_type_limits_duplicates(self):
  for f in ({'client_id':'other'},{'scope':'notify'},{'redirect_uri':'https://evil.invalid'}):self.assertEqual(self.post('device',f).status_code,400)
  self.assertEqual(self.machine.post(PREFIX+'/enrollment/device',json={'client_id':CLIENT_ID}).status_code,415)
  self.assertEqual(self.machine.post(PREFIX+'/enrollment/device',data='client_id='+CLIENT_ID+'&client_id='+CLIENT_ID,content_type='application/x-www-form-urlencoded').status_code,400)
  self.assertEqual(self.machine.post(PREFIX+'/enrollment/device',data='x'*4097,content_type='application/x-www-form-urlencoded').status_code,413)
 def test_phone_consent_csrf_pending_slowdown_single_use(self):
  d=self.start();self.assertEqual(d['verification_uri'],'https://brief.test/connect');self.assertEqual(d['expires_in'],600)
  self.assertEqual(self.exchange(d).json['error'],'authorization_pending');self.assertEqual(self.exchange(d).json['error'],'slow_down')
  self.assertEqual(self.consent(d['user_code'],csrf='wrong').status_code,403)
  with self.store.connect() as db:self.assertEqual(db.execute('SELECT status FROM publisher_devices').fetchone()[0],'pending')
  self.consent(d['user_code'])
  with self.store.connect() as db:db.execute('UPDATE publisher_devices SET last_poll=0')
  self.assertEqual(self.exchange(d).status_code,200);self.assertEqual(self.exchange(d).json['error'],'invalid_grant')
 def test_denial_expiry_cannot_issue(self):
  d=self.start();self.consent(d['user_code'],'deny');self.assertEqual(self.exchange(d).json['error'],'access_denied')
  d=self.start()
  with self.store.connect() as db:db.execute('UPDATE publisher_devices SET expires=?',(time.time()-1,))
  self.consent(d['user_code']);self.assertEqual(self.exchange(d).json['error'],'expired_token')
 def test_hashes_only_scopes_fixed_no_credentials_in_browser(self):
  d=self.start();page=self.consent(d['user_code']);self.assertNotIn(d['device_code'].encode(),page.data);creds=self.exchange(d).json
  self.assertEqual(creds['scope'].split(),SCOPES);self.assertNotIn('notify',SCOPES);self.assertEqual(creds['expires_in'],900)
  with self.store.connect() as db:snapshot=repr([tuple(r) for table in ('publisher_devices','publisher_grants','publisher_refreshes','publishers') for r in db.execute('SELECT * FROM '+table)])
  for secret in (d['device_code'],d['user_code'].replace('-',''),creds['access_token'],creds['refresh_token']):self.assertNotIn(secret,snapshot)
  self.assertIn('no-store',page.headers['Cache-Control'])
  self.assertEqual(self.machine.post(PREFIX+'/notifications/2026-01-01',json={},headers={'Authorization':'Bearer '+creds['access_token']}).status_code,403)
  self.assertEqual(self.machine.get('/api/briefs',headers={'Authorization':'Bearer '+creds['access_token']}).status_code,401)
  self.assertIn('no-store',self.refresh(creds['refresh_token']).headers['Cache-Control'])
 def test_rotation_replay_revokes_family(self):
  a=self.enroll();r=self.refresh(a['refresh_token']);self.assertEqual(r.status_code,200);b=r.json
  self.assertNotEqual(a['refresh_token'],b['refresh_token']);self.assertEqual(self.status(b['access_token']),409)
  self.assertEqual(self.refresh(a['refresh_token']).json['error'],'invalid_grant')
  self.assertEqual(self.status(a['access_token']),401);self.assertEqual(self.status(b['access_token']),401);self.assertEqual(self.refresh(b['refresh_token']).status_code,400)
 def test_concurrent_refresh_single_issue_then_fail_closed(self):
  a=self.enroll()
  with ThreadPoolExecutor(2) as pool:replies=list(pool.map(lambda _:self.refresh(a['refresh_token'],self.app.test_client()),range(2)))
  self.assertEqual(sorted(r.status_code for r in replies),[200,400]);issued=next(r.json for r in replies if r.status_code==200);self.assertEqual(self.status(issued['access_token']),401)
 def test_concurrent_device_single_issue(self):
  d=self.start();self.consent(d['user_code'])
  with ThreadPoolExecutor(2) as pool:replies=list(pool.map(lambda _:self.exchange(d,self.app.test_client()),range(2)))
  self.assertEqual(sorted(r.status_code for r in replies),[200,400])
  with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM publisher_grants').fetchone()[0],1)
 def test_absolute_idle_and_revoke(self):
  a=self.enroll()
  with self.store.connect() as db:db.execute('UPDATE publisher_grants SET absolute_expires=?,expires=?',(time.time()+30,time.time()+30))
  b=self.refresh(a['refresh_token']).json;self.assertLessEqual(b['expires_in'],30);self.assertEqual(b['refresh_expires_at'],b['grant_expires_at'])
  self.assertEqual(self.consent('',action='revoke').status_code,200);self.assertEqual(self.status(b['access_token']),401);self.assertEqual(self.refresh(b['refresh_token']).status_code,400)
  with self.store.connect() as db:db.execute('UPDATE publisher_grants SET revoked=0,expires=?',(time.time()-1,))
  self.assertEqual(self.refresh(b['refresh_token']).status_code,400)
 def test_access_expiry_and_absolute_not_extended(self):
  a=self.enroll();self.assertLessEqual(a['grant_expires_at']-time.time(),GRANT_SECONDS)
  with self.store.connect() as db:db.execute('UPDATE publishers SET expires=?',(time.time()-1,))
  self.assertEqual(self.status(a['access_token']),401);b=self.refresh(a['refresh_token']);self.assertEqual(b.status_code,200);self.assertEqual(a['grant_expires_at'],b.json['grant_expires_at'])
 def test_persistent_rate_limit(self):
  for _ in range(10):self.assertEqual(self.post('device',{}).status_code,200)
  self.assertEqual(self.post('device',{}).status_code,429)

 def test_administrator_access_revocation_revokes_grant(self):
  from contextlib import redirect_stdout
  from io import StringIO
  from unittest.mock import patch
  from brief.identity import main
  from brief.adapters import digest
  creds=self.enroll()
  with self.store.connect() as db:publisher_id=db.execute('SELECT id FROM publishers WHERE token_hash=?',(digest(creds['access_token']),)).fetchone()[0]
  with patch('sys.argv',['identity','revoke','--id',publisher_id,'--database',self.store.path]),redirect_stdout(StringIO()):main()
  self.assertEqual(self.status(creds['access_token']),401);self.assertEqual(self.refresh(creds['refresh_token']).status_code,400)
 def test_expired_used_refresh_replay_still_revokes_live_child(self):
  from brief.adapters import digest
  first=self.enroll();child=self.refresh(first['refresh_token']).json
  with self.store.connect() as db:db.execute('UPDATE publisher_refreshes SET expires=? WHERE token_hash=?',(time.time()-1,digest(first['refresh_token'])))
  self.assertEqual(self.refresh(first['refresh_token']).status_code,400);self.assertEqual(self.status(child['access_token']),401)
 def test_phone_login_returns_only_to_fixed_consent_page(self):
  client=self.app.test_client();r=client.get('/connect',base_url='https://brief.test')
  self.assertEqual(r.location,'/login?next=/connect')
  for target,expected in (('/connect','/connect'),('https://evil.invalid','/')):
   client=self.app.test_client();client.get('/login',base_url='https://brief.test')
   with client.session_transaction(base_url='https://brief.test') as s:csrf=s['csrf']
   r=client.post('/login?next='+target,base_url='https://brief.test',data={'password':self.password,'csrf':csrf},headers={'Origin':'https://brief.test'})
   self.assertEqual(r.status_code,302);self.assertEqual(r.location,expected)
 def test_revoke_cancels_unredeemed_approved_and_pending_codes(self):
  approved=self.start();pending=self.start();self.consent(approved['user_code'])
  self.consent('',action='revoke')
  for device in (approved,pending):self.assertEqual(self.exchange(device).json['error'],'access_denied')
  with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM publisher_grants').fetchone()[0],0)
 def test_rotation_preserves_quota_and_single_live_access(self):
  a=self.enroll()
  for _ in range(60):self.assertEqual(self.status(a['access_token']),409)
  b=self.refresh(a['refresh_token']).json
  self.assertEqual(self.status(b['access_token']),429)
  with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM publishers WHERE revoked=0').fetchone()[0],1)
 def test_public_enrollment_exhaustion_does_not_block_refresh_or_known_device(self):
  creds=self.enroll();device=self.start();self.consent(device['user_code'])
  for _ in range(8):self.assertEqual(self.post('device',{}).status_code,200)
  self.assertEqual(self.post('device',{}).status_code,429)
  self.assertEqual(self.refresh(creds['refresh_token']).status_code,200)
  self.assertEqual(self.exchange(device).status_code,200)
 def test_new_enrollment_replaces_previous_grant(self):
  first=self.enroll();second=self.enroll()
  self.assertEqual(self.status(first['access_token']),401)
  self.assertEqual(self.refresh(first['refresh_token']).status_code,400)
  self.assertEqual(self.status(second['access_token']),409)
 def test_creation_budget_by_verified_edge_source(self):
  self.app.config['ENROLLMENT_TRUST_CLOUDFLARE']=True
  for _ in range(10):self.assertEqual(self.post('device',{},headers={'CF-Connecting-IP':'192.0.2.1'}).status_code,200)
  self.assertEqual(self.post('device',{},headers={'CF-Connecting-IP':'192.0.2.1'}).status_code,429)
  self.assertEqual(self.post('device',{},headers={'CF-Connecting-IP':'192.0.2.2'}).status_code,200)
  self.assertEqual(self.post('device',{},headers={'CF-Connecting-IP':'invalid'}).status_code,400)
if __name__=='__main__':unittest.main()
