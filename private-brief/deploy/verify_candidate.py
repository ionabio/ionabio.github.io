#!/usr/bin/python3.13
"""Production-mode auth regression with isolated synthetic state; prints no secrets."""
import os
import pwd
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

if len(sys.argv)==2:
    candidate=Path(sys.argv[1]).resolve()
    if candidate.parent!=Path('/opt/nabi-brief-releases'): raise SystemExit(1)
    for line in Path('/etc/nabi-brief/environment').read_text().splitlines():
        if line and not line.startswith('#'):
            key,value=line.split('=',1);os.environ[key]=value
    with tempfile.TemporaryDirectory(dir='/var/lib/nabi-brief',prefix='release-qa-') as tmp:
        identity=pwd.getpwnam('nabi-brief')
        os.chown(tmp,identity.pw_uid,identity.pw_gid)
        subprocess.run(['/usr/sbin/runuser','-u','nabi-brief','--',str(candidate/'.venv/bin/python'),__file__,str(candidate),tmp],check=True,cwd=candidate)
else:
    sys.path.insert(0,sys.argv[1])
    from brief.web import create_app
    from werkzeug.security import generate_password_hash
    qa_password=secrets.token_urlsafe(32)
    app=create_app({'DATABASE':str(Path(sys.argv[2])/'test.sqlite3'),'PASSWORD_HASH':generate_password_hash(qa_password)})
    client=app.test_client();origin=app.config['PUBLIC_ORIGIN']
    for path in ('/','/assets/app.js','/api/briefs','/media/'+('a'*64)+'.png','/api/publisher/v1/status/2026-01-01'):
        r=client.get(path,base_url=origin);assert r.status_code in (302,401);assert 'no-store' in r.headers['Cache-Control']
    client.get('/login',base_url=origin)
    with client.session_transaction(base_url=origin) as session: csrf=session['csrf']
    r=client.post('/login',base_url=origin,data={'password':qa_password,'csrf':csrf},headers={'Origin':origin})
    assert r.status_code==302
    for flag in ('Secure','HttpOnly','SameSite=Strict'): assert flag in r.headers['Set-Cookie']
    assert client.get('/api/briefs',base_url=origin).status_code==200
    assert client.post('/logout',base_url=origin).status_code==403
    with client.session_transaction(base_url=origin) as session: csrf=session['csrf']
    assert client.post('/logout',base_url=origin,data={'csrf':csrf},headers={'Origin':origin}).status_code==302
    assert client.get('/api/briefs',base_url=origin).status_code==401
