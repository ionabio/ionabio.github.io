"""Test the real packaged ARM64 wheelhouse offline, using isolated synthetic state."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from updater import extract

SMOKE=r'''
import json,secrets,sys,time
from pathlib import Path
from werkzeug.security import generate_password_hash
from brief.web import create_app
from brief.pipeline import clock
from brief.adapters import digest
from brief.publishing import SCOPES,PREFIX
qa_password=secrets.token_urlsafe(32)
app=create_app({'SECRET_KEY':secrets.token_urlsafe(48),'PASSWORD_HASH':generate_password_hash(qa_password),'PUBLIC_ORIGIN':'https://brief.test','DATABASE':sys.argv[1]})
store=app.extensions['brief_store'];client=app.test_client();date=clock().date().isoformat()
assert client.get('/api/briefs').status_code==401
assert client.get(PREFIX+'/status/'+date).status_code==401
token=secrets.token_urlsafe(48)
with store.connect() as db:db.execute('INSERT INTO publishers VALUES (?,?,?,0,?)',('test',digest(token),time.time()+60,json.dumps(sorted(SCOPES))))
h={'Authorization':'Bearer '+token}
bundle={'date':date,**{k:{'status':'unavailable','items':[]} for k in ('calendar','todos','news','deals')}}
r=client.post(PREFIX+'/drafts',json={'brief':bundle,'baseHash':None},headers=h);assert r.status_code==200
hash=r.json['hash'];assert client.get(PREFIX+'/drafts/'+date,headers=h).json['hash']==hash
assert client.post(PREFIX+'/drafts/'+date+'/approve',json={'hash':hash},headers=h).status_code==200
result=client.post(PREFIX+'/drafts/'+date+'/publish',json={'hash':hash},headers=h)
assert result.status_code==200 and result.json['status'] in ('published','not_due')
client.get('/login',base_url='https://brief.test')
with client.session_transaction(base_url='https://brief.test') as s: csrf=s['csrf']
r=client.post('/login',base_url='https://brief.test',data={'password':qa_password,'csrf':csrf},headers={'Origin':'https://brief.test'});assert r.status_code==302
assert client.get('/assets/app.js',base_url='https://brief.test').status_code==200
assert client.get('/',base_url='https://brief.test').status_code==200
assert client.post('/logout',base_url='https://brief.test').status_code==403
print('PACKAGED_OFFLINE_DEPENDENCIES_AUTH_AND_SYNTHETIC_PUBLICATION_VERIFIED')
'''

def main():
    p=argparse.ArgumentParser();p.add_argument('--archive',required=True);a=p.parse_args()
    with tempfile.TemporaryDirectory() as tmp,tempfile.TemporaryDirectory() as data:
        app=Path(tmp)/'app';app.mkdir();extract(Path(a.archive),app)
        subprocess.run([sys.executable,'-m','venv',str(app/'.venv')],check=True)
        python=app/'.venv/bin/python'
        subprocess.run([str(python),'-m','pip','install','--no-index','--find-links',str(app/'wheels'),'-r',str(app/'requirements.txt')],check=True,stdout=subprocess.DEVNULL)
        subprocess.run([str(python),'-m','pip','check'],check=True)
        env={**os.environ,'PYTHONPATH':str(app)}
        subprocess.run([str(python),'-c',SMOKE,str(Path(data)/'synthetic.sqlite3')],check=True,cwd=app,env=env)

if __name__=='__main__':main()
