"""Explicit owner refresh preserves exact approval, API immutability and rollback."""
import copy,json,tempfile,unittest,time
from pathlib import Path
from datetime import timedelta
from unittest.mock import patch
from brief.store import Store
from brief.editorial import prepare,approve,publish_approved,replace_approved
from brief.adapters import digest
from brief.web import create_app
from brief.publishing import SCOPES,PREFIX
from werkzeug.security import generate_password_hash
import test_brief

NOW=test_brief.NOW

class ReplacementTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'state.sqlite3')
  self.bundle=json.loads((Path(__file__).parents[1]/'fixtures/synthetic.json').read_text(encoding='utf-8-sig'))
  self.date=self.bundle['date'];self.old=self.save(self.bundle)
  publish_approved(self.store,self.date,self.old,now=NOW)
  self.before=self.snapshot()
  self.changed=copy.deepcopy(self.bundle);self.changed['todos']['items'].append({'title':'A genuine new task in this synthetic source','due':self.date})
  self.new=self.save(self.changed)
 def tearDown(self):self.temp.cleanup()
 def save(self,bundle):
  h=prepare(self.store,bundle,now=NOW)['hash'];approve(self.store,self.date,h);return h
 def snapshot(self):
  with self.store.connect() as db:return tuple(db.execute('SELECT hash,payload,published_at FROM briefs WHERE date=?',(self.date,)).fetchone())
 def history(self):
  with self.store.connect() as db:return list(db.execute('SELECT * FROM brief_revisions WHERE date=?',(self.date,)))
 def test_explicit_replacement_is_atomic_and_retry_does_not_duplicate(self):
  with self.assertRaises(ValueError):publish_approved(self.store,self.date,self.new,now=NOW)
  result=replace_approved(self.store,self.date,self.new,self.old,now=NOW)
  self.assertEqual(result['status'],'replaced');self.assertEqual(result['hash'],self.new)
  self.assertEqual(self.snapshot()[0],self.new)
  self.assertEqual(len(json.loads(self.snapshot()[1])['todos']['items']),2)
  old=self.history()[0];self.assertEqual((old['hash'],old['payload'],old['published_at']),self.before)
  self.assertEqual(old['replacement_hash'],self.new)
  self.assertEqual(replace_approved(self.store,self.date,self.new,self.old,now=NOW)['status'],'unchanged')
  self.assertEqual(len(self.history()),1)
 def test_requires_exact_approved_hash_and_current_publication_lease(self):
  for new,old in [('0'*64,self.old),(self.new,'0'*64),(self.new,'bad')]:
   with self.assertRaises(ValueError):replace_approved(self.store,self.date,new,old,now=NOW)
  with self.store.connect() as db:db.execute('UPDATE drafts SET approved_hash=NULL WHERE date=?',(self.date,))
  with self.assertRaises(ValueError):replace_approved(self.store,self.date,self.new,self.old,now=NOW)
  self.assertEqual(self.snapshot(),self.before);self.assertEqual(self.history(),[])
 def test_source_content_tampering_invalidates_approval(self):
  self.changed['learning']['level']='B2+'
  with self.store.connect() as db:db.execute('UPDATE drafts SET payload=? WHERE date=?',(json.dumps(self.changed),self.date))
  with self.assertRaises(ValueError):replace_approved(self.store,self.date,self.new,self.old,now=NOW)
  self.assertEqual(self.snapshot(),self.before);self.assertEqual(self.history(),[])
 def test_failure_after_snapshot_insert_rolls_back_everything(self):
  with patch('brief.pipeline.run',side_effect=RuntimeError('synthetic failure')):
   with self.assertRaises(RuntimeError):replace_approved(self.store,self.date,self.new,self.old,now=NOW)
  self.assertEqual(self.snapshot(),self.before);self.assertEqual(self.history(),[])
 def test_freshness_date_and_six_am_gate_are_not_bypassed(self):
  with self.assertRaises(ValueError):replace_approved(self.store,self.date,self.new,self.old,now=NOW+timedelta(days=1))
  self.assertEqual(replace_approved(self.store,self.date,self.new,self.old,now=NOW.replace(hour=5,minute=59))['status'],'not_due')
  stale=copy.deepcopy(self.changed);stale['calendar']['checkedAt']='2026-09-29T07:50:00+02:00'
  h=prepare(self.store,stale,now=NOW.replace(hour=7,minute=50))['hash'];approve(self.store,self.date,h)
  with self.assertRaises(ValueError):replace_approved(self.store,self.date,h,self.old,now=NOW)
  self.assertEqual(self.snapshot(),self.before);self.assertEqual(self.history(),[])
 def test_no_notification_or_other_date_is_changed(self):
  with self.store.connect() as db:
   db.execute('INSERT INTO deliveries VALUES (?,?,?)',(self.date,'synthetic-subscription','sent'))
   db.execute('INSERT INTO briefs VALUES (?,?,?,?)',('2026-09-29','other-hash','{}','other-time'))
  replace_approved(self.store,self.date,self.new,self.old,now=NOW)
  with self.store.connect() as db:
   self.assertEqual(list(map(tuple,db.execute('SELECT * FROM deliveries'))),[(self.date,'synthetic-subscription','sent')])
   self.assertEqual(tuple(db.execute('SELECT hash,payload,published_at FROM briefs WHERE date=?',('2026-09-29',)).fetchone()),('other-hash','{}','other-time'))
 def test_machine_api_stays_immutable_and_status_tracks_active_payload(self):
  token='synthetic-replacement-test-'+('x'*48)
  with self.store.connect() as db:
   db.execute('INSERT INTO publishers VALUES (?,?,?,0,?)',('synthetic',digest(token),time.time()+3600,json.dumps(sorted(SCOPES))))
  app=create_app({'TESTING':True,'SECRET_KEY':'synthetic','PASSWORD_HASH':generate_password_hash('synthetic'),'DATABASE':self.store.path,'PUBLIC_ORIGIN':'https://brief.test','PUBLISHER_NOW':NOW})
  c=app.test_client();headers={'Authorization':'Bearer '+token}
  self.assertEqual(c.post(PREFIX+'/drafts/'+self.date+'/publish',json={'hash':self.new},headers=headers).status_code,409)
  self.assertEqual(c.post(PREFIX+'/drafts/'+self.date+'/replace',json={'hash':self.new,'publishedHash':self.old},headers=headers).status_code,404)
  self.assertEqual(self.snapshot(),self.before)
  replace_approved(self.store,self.date,self.new,self.old,now=NOW)
  result=c.get(PREFIX+'/status/'+self.date,headers=headers)
  self.assertEqual(result.status_code,200);self.assertEqual(result.json['publishedHash'],self.new);self.assertTrue(result.json['approved'])
  self.assertEqual(set(result.json),{'date','draftHash','approved','publishedHash','publishedAt','notifications','phoneDelivery'})
  self.assertEqual(SCOPES,{'prepare','review','approve','publish','status','notify'})
  owner=app.test_client();test_brief.Tests.login(self,owner)
  self.assertEqual(len(owner.get('/api/briefs/'+self.date).json['todos']['items']),2)
