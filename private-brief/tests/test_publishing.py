import copy
import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from werkzeug.security import generate_password_hash
from brief.adapters import digest
from brief.push import notify
from brief.publishing import SCOPES, PREFIX
from brief.store import Store
from brief.web import create_app
import test_brief as base_tests
NOW=base_tests.NOW


class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'private.sqlite3')
        self.bundle=json.loads((Path(__file__).parents[1]/'fixtures/synthetic.json').read_text())
        self.token='synthetic-test-token-'+('x'*48)
        with self.store.connect() as db:
            db.execute('INSERT INTO publishers VALUES (?,?,?,0,?)',('synthetic',digest(self.token),time.time()+3600,json.dumps(sorted(SCOPES))))
        self.app=create_app({'TESTING':True,'SECRET_KEY':'synthetic','PASSWORD_HASH':generate_password_hash('synthetic'),'DATABASE':self.store.path,'PUBLIC_ORIGIN':'https://brief.test','PUBLISHER_NOW':NOW,'VAPID_PRIVATE_KEY':'synthetic','VAPID_SUBJECT':'mailto:test@example.invalid'})
        self.client=self.app.test_client()
        self.headers={'Authorization':'Bearer '+self.token}
        self.date=self.bundle['date']
    def tearDown(self): self.temp.cleanup()
    def post(self,path,value,client=None):
        return (client or self.client).post(PREFIX+path,json=value,headers=self.headers)
    def prepare(self,bundle=None,base=None): return self.post('/drafts',{'brief':bundle or self.bundle,'baseHash':base})
    def ready(self):
        result=self.prepare();self.assertEqual(result.status_code,200)
        h=result.json['hash'];self.assertEqual(self.post('/drafts/'+self.date+'/approve',{'hash':h}).status_code,200)
        return h
    def test_auth_revocation_expiry_scope(self):
        for headers in ({},{'Authorization':'Bearer wrong'}):
            for method,path in [('GET','/drafts/'+self.date),('POST','/drafts')]:
                r=self.client.open(PREFIX+path,method=method,json={},headers=headers)
                self.assertEqual(r.status_code,401);self.assertIn('no-store',r.headers['Cache-Control'])
        with self.store.connect() as db: db.execute('UPDATE publishers SET expires=?',(time.time()-1,))
        self.assertEqual(self.prepare().status_code,401)
        with self.store.connect() as db: db.execute('UPDATE publishers SET expires=?,revoked=1',(time.time()+3600,))
        self.assertEqual(self.prepare().status_code,401)
        with self.store.connect() as db: db.execute('UPDATE publishers SET revoked=0,scopes=?',(json.dumps(['status']),))
        self.assertEqual(self.prepare().status_code,403)
    def test_browser_cannot_use_machine_auth_or_bypass_csrf(self):
        base_tests.Tests.login(self,self.client)
        self.assertEqual(self.prepare().status_code,403)
        machine=self.app.test_client()
        r=machine.post(PREFIX+'/drafts',json={},headers={**self.headers,'Origin':'https://brief.test'})
        self.assertEqual(r.status_code,403)
        self.assertEqual(machine.post('/api/push/unsubscribe',json={},headers=self.headers).status_code,403)
        self.assertEqual(machine.get('/api/briefs',headers=self.headers).status_code,401)
    def test_input_limit_and_malformed(self):
        self.assertEqual(self.client.post(PREFIX+'/drafts',data=b'x'*1000001,headers={**self.headers,'Content-Type':'application/json'}).status_code,413)
        self.assertEqual(self.post('/drafts',[]).status_code,400)
        self.assertEqual(self.client.post(PREFIX+'/drafts',data='invalid',headers={**self.headers,'Content-Type':'application/json'}).status_code,400)
        self.assertEqual(self.client.post(PREFIX+'/drafts',data='{}',headers=self.headers).status_code,415)
        b=copy.deepcopy(self.bundle);b['calendar']['checkedAt']='2026-09-28T08:00:00+02:00'
        self.assertEqual(self.prepare(b).status_code,409)
        b['date']='2026-09-29';self.assertEqual(self.prepare(b).status_code,409)
    def test_hash_and_malformed_exercises_rejected(self):
        b=copy.deepcopy(self.bundle);a=b['news']['items'][0]
        language={'sourceHash':'0'*64,'summaryNl':'Origineel.','vocabulary':[],'puzzle':[]};a['reviewedLanguage']=language
        self.assertEqual(self.prepare(b).status_code,409)
        language['sourceHash']=digest(a['authorizedText'])
        language['vocabulary']=[{'word':'missing meaning'}];self.assertEqual(self.prepare(b).status_code,409)
        language['vocabulary']=[];language['puzzle']=[{'question':'?','answer':'x','explanation':'x','evidence':'not in source'}]
        self.assertEqual(self.prepare(b).status_code,409)
        with self.store.connect() as db: self.assertEqual(db.execute('SELECT count(*) FROM drafts').fetchone()[0],0)
    def test_review_hash_approval_idempotent_publication(self):
        h=self.ready()
        r=self.client.get(PREFIX+'/drafts/'+self.date,headers=self.headers)
        self.assertEqual(r.json['brief'],self.bundle);self.assertEqual(r.json['hash'],h);self.assertIn('no-store',r.headers['Cache-Control'])
        self.assertEqual(self.prepare().json['hash'],h)
        self.assertEqual(self.post('/drafts/'+self.date+'/publish',{'hash':'0'*64}).status_code,409)
        self.assertEqual(self.post('/drafts/'+self.date+'/publish',{'hash':h}).json['status'],'published')
        self.assertEqual(self.post('/drafts/'+self.date+'/publish',{'hash':h}).json['status'],'unchanged')
        status=self.client.get(PREFIX+'/status/'+self.date,headers=self.headers).json
        self.assertEqual(status['publishedHash'],h);self.assertEqual(status['phoneDelivery'],'unknown')
        b=copy.deepcopy(self.bundle);b['learning']['level']='B2+'
        other=self.prepare(b,base=h).json['hash']
        self.post('/drafts/'+self.date+'/approve',{'hash':other})
        self.assertEqual(self.post('/drafts/'+self.date+'/publish',{'hash':other}).status_code,409)
    def test_six_am_gate_preserves_exact_approval_and_immutable_publication(self):
        for name in ('calendar','todos','news'):
            self.bundle[name]['checkedAt']='2026-09-30T05:50:00+02:00'
        self.app.config['PUBLISHER_NOW']=datetime.fromisoformat('2026-09-30T05:59:59+02:00')
        h=self.ready()
        self.assertEqual(self.post('/drafts/'+self.date+'/publish',{'hash':h}).json['status'],'not_due')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM briefs').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT approved_hash FROM drafts').fetchone()[0],h)
        self.app.config['PUBLISHER_NOW']=datetime.fromisoformat('2026-09-30T06:00:00+02:00')
        self.assertEqual(self.post('/drafts/'+self.date+'/publish',{'hash':'0'*64}).status_code,409)
        self.assertEqual(self.post('/drafts/'+self.date+'/publish',{'hash':h}).json['status'],'published')
        with self.store.connect() as db:
            before=tuple(db.execute('SELECT hash,payload,published_at FROM briefs').fetchone())
        self.app.config['PUBLISHER_NOW']=datetime.fromisoformat('2026-09-30T08:00:00+02:00')
        self.assertEqual(self.post('/drafts/'+self.date+'/publish',{'hash':h}).json['status'],'unchanged')
        changed=copy.deepcopy(self.bundle);changed['learning']['level']='B2+'
        other=self.prepare(changed,base=h).json['hash']
        self.post('/drafts/'+self.date+'/approve',{'hash':other})
        self.assertEqual(self.post('/drafts/'+self.date+'/publish',{'hash':other}).status_code,409)
        with self.store.connect() as db:
            self.assertEqual(tuple(db.execute('SELECT hash,payload,published_at FROM briefs').fetchone()),before)
    def test_concurrent_draft_edits_and_old_approval(self):
        h=self.ready();bundles=[copy.deepcopy(self.bundle) for _ in range(2)]
        for i,b in enumerate(bundles): b['learning']['level']='B'+str(i)
        def edit(b): return self.post('/drafts',{'brief':b,'baseHash':h},self.app.test_client()).status_code
        with ThreadPoolExecutor(2) as executor: codes=list(executor.map(edit,bundles))
        self.assertEqual(sorted(codes),[200,409])
        self.assertEqual(self.post('/drafts/'+self.date+'/approve',{'hash':h}).status_code,409)
        self.assertEqual(self.post('/drafts/'+self.date+'/publish',{'hash':h}).status_code,409)
        with self.store.connect() as db: self.assertEqual(db.execute('SELECT count(*) FROM briefs').fetchone()[0],0)
    def test_expired_source_at_publication(self):
        h=self.ready();self.app.config['PUBLISHER_NOW']=datetime.fromisoformat('2026-09-30T23:59:00+02:00')
        with self.store.connect() as db:
            b=json.loads(db.execute('SELECT payload FROM drafts').fetchone()[0]);b['calendar']['checkedAt']='2026-09-29T23:58:00+02:00'
        # Re-prepare at a fresh earlier instant, then try after freshness expires.
        self.app.config['PUBLISHER_NOW']=datetime.fromisoformat('2026-09-30T08:00:00+02:00')
        other=self.prepare(b,h).json['hash'];self.post('/drafts/'+self.date+'/approve',{'hash':other})
        self.app.config['PUBLISHER_NOW']=datetime.fromisoformat('2026-09-30T23:59:00+02:00')
        self.assertEqual(self.post('/drafts/'+self.date+'/publish',{'hash':other}).status_code,409)
    def test_notification_after_publish_and_duplicate_concurrent_calls(self):
        with self.store.connect() as db: db.execute('INSERT INTO subscriptions VALUES (?,?)',('synthetic',json.dumps(base_tests.Tests.sub(self))))
        h=self.ready();sent=[]
        def mock_notify(store,config,**kwargs): return notify(store,config,sender=lambda **kw:sent.append(kw),**kwargs)
        with patch('brief.publishing.notify',mock_notify):
            self.assertEqual(self.post('/notifications/'+self.date,{'hash':h}).status_code,409)
            self.post('/drafts/'+self.date+'/publish',{'hash':h})
            def send(_):return self.post('/notifications/'+self.date,{'hash':h},self.app.test_client())
            with ThreadPoolExecutor(2) as executor: responses=list(executor.map(send,range(2)))
            self.assertEqual([r.status_code for r in responses],[200,200]);self.assertEqual(len(sent),1)
            self.assertEqual(sum(r.json['providerAccepted'] for r in responses),1)
            self.assertEqual(self.post('/notifications/'+self.date,{'hash':h}).json['attempted'],0)
        self.assertEqual(json.loads(sent[0]['data']),{'date':self.date})
    def test_ambiguous_push_not_retried(self):
        h=self.ready();self.post('/drafts/'+self.date+'/publish',{'hash':h})
        with self.store.connect() as db: db.execute('INSERT INTO subscriptions VALUES (?,?)',('synthetic',json.dumps(base_tests.Tests.sub(self))))
        attempts=[]
        def fail(**kw): attempts.append(1);raise TimeoutError()
        for _ in range(2): notify(self.store,self.app.config,NOW,sender=fail,expected_hash=h)
        self.assertEqual(len(attempts),1)
    def test_persistent_rate_limit(self):
        for _ in range(60): self.assertEqual(self.client.get(PREFIX+'/status/'+self.date,headers=self.headers).status_code,200)
        self.assertEqual(self.client.get(PREFIX+'/status/'+self.date,headers=self.headers).status_code,429)
