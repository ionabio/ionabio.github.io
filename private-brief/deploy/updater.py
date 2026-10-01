#!/usr/bin/python3
"""Outbound, fixed-repository release updater. No remote commands or credentials."""
import hashlib
import json
import os
import platform
import re
import sqlite3
import subprocess
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.request import Request, urlopen
from urllib.error import HTTPError

REPO='ionabio/ionabio.github.io'
IDENTITY='https://github.com/'+REPO+'/.github/workflows/brief-release.yml@refs/heads/main'
STATE=Path('/var/lib/nabi-release')
RELEASES=Path('/opt/nabi-brief-releases')
CURRENT=Path('/opt/nabi-brief-releases/current')
GH='/usr/local/lib/nabi-release/gh'
MAX_ARCHIVE=256*1024*1024


def download(url, target=None, limit=1000000):
    raw=re.fullmatch('https://raw.githubusercontent.com/'+re.escape(REPO)+'/[a-f0-9]{40}/.github/workflows/brief-release.yml',url)
    if not raw and not url.startswith(('https://api.github.com/repos/'+REPO+'/', 'https://github.com/'+REPO+'/releases/download/')):
        raise ValueError('Unexpected download destination')
    req=Request(url,headers={'Accept':'application/vnd.github+json','User-Agent':'NabiBrief-updater/1'})
    with urlopen(req,timeout=30) as response:
        # GitHub release assets redirect only to its HTTPS CDN.
        from urllib.parse import urlsplit
        final=urlsplit(response.url)
        if final.scheme!='https' or final.hostname not in ('api.github.com','github.com','raw.githubusercontent.com','release-assets.githubusercontent.com','objects.githubusercontent.com'):
            raise ValueError('Unexpected download redirect')
        if target is None:
            data=response.read(limit+1)
            if len(data)>limit: raise ValueError('Download too large')
            return data if raw else json.loads(data)
        total=0
        with target.open('xb') as out:
            while chunk:=response.read(65536):
                total+=len(chunk)
                if total>limit: raise ValueError('Download too large')
                out.write(chunk)


def manifest_check(value, run_id, now=None):
    now=now or datetime.now(timezone.utc)
    required={'schema','repository','commit','workflowCommit','runId','artifact','sha256','issuedAt','expiresAt'}
    if set(value)!=required or value['schema']!=1 or value['repository']!=REPO or value['runId']!=run_id or value['artifact']!='brief-arm64.tar.gz':
        raise ValueError('Unexpected approval manifest')
    for key,size in (('commit',40),('workflowCommit',40),('sha256',64)):
        if not re.fullmatch('[a-f0-9]{'+str(size)+'}',value[key]): raise ValueError('Invalid digest')
    issued=datetime.fromisoformat(value['issuedAt']);expires=datetime.fromisoformat(value['expiresAt'])
    if issued.tzinfo is None or expires.tzinfo is None or not issued<=now<expires or not 0<(expires-issued).total_seconds()<=86400:
        raise ValueError('Approval expired or invalid')


def extract(archive,destination):
    with tarfile.open(archive,'r:gz') as tar:
        members=tar.getmembers()
        if len(members)>20000 or sum(m.size for m in members)>512*1024*1024: raise ValueError('Archive exceeds budget')
        seen=set()
        for member in members:
            parts=PurePosixPath(member.name).parts
            if not parts or member.name.startswith('/') or any(p in ('..','.') for p in parts) or '\\' in member.name or parts[0] not in ('brief','assets','templates','wheels','requirements.txt','BUILD.json'):
                raise ValueError('Unexpected archive path')
            if member.name in seen or not (member.isfile() or member.isdir()): raise ValueError('Duplicate or nonregular archive member')
            seen.add(member.name)
        for member in members:
            path=destination/member.name
            if member.isdir(): path.mkdir(parents=True,exist_ok=True)
            else:
                path.parent.mkdir(parents=True,exist_ok=True)
                with tar.extractfile(member) as src,path.open('xb') as out:
                    import shutil
                    shutil.copyfileobj(src,out)
                path.chmod(0o644)


def switch(link,target):
    temporary=link.with_name(link.name+'.next')
    if temporary.is_symlink(): temporary.unlink()
    temporary.symlink_to(target,target_is_directory=True)
    os.replace(temporary,link)


def rollout(link,candidate,restart,health):
    previous=link.resolve(strict=True)
    switch(link,candidate)
    try:
        restart()
        if not health(): raise RuntimeError('Health/auth verification failed')
    except BaseException:
        switch(link,previous)
        restart()
        if not health(): raise RuntimeError('Rollback health failed') from None
        raise


def verify_live():
    for attempt in range(20):
        try:
            with urlopen('http://127.0.0.1:8080/health',timeout=3) as r:
                result=json.load(r)
                build=CURRENT/'BUILD.json'
                expected=json.loads(build.read_text())['commit'] if build.exists() else None
                if result.get('status')!='ok' or (expected and result.get('commit')!=expected): raise ValueError('Wrong running commit')
            routes=['/api/briefs']
            if build.exists(): routes.append('/api/publisher/v1/status/2026-01-01')
            for route in routes:
                try: urlopen('http://127.0.0.1:8080'+route,timeout=3);raise ValueError('Anonymous API served')
                except HTTPError as error:
                    if error.code!=401 or 'no-store' not in error.headers.get('Cache-Control',''): raise
            return True
        except Exception: time.sleep(1)
    return False


def write_state(value):
    temporary=STATE/'state.next'
    temporary.write_text(json.dumps(value,sort_keys=True))
    temporary.chmod(0o600)
    os.replace(temporary,STATE/'state.json')


def update():
    if platform.machine()!='aarch64' or os.geteuid()!=0: raise RuntimeError('Root ARM64 updater required')
    STATE.mkdir(mode=0o700,exist_ok=True)
    import fcntl
    with (STATE/'update.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return {'status':'busy'}
        state=json.loads((STATE/'state.json').read_text()) if (STATE/'state.json').exists() else {'highestRun':0}
        releases=download('https://api.github.com/repos/'+REPO+'/releases?per_page=100',limit=2000000)
        candidates=[r for r in releases if not r['draft'] and not r['prerelease'] and re.fullmatch('brief-production-[0-9]+',r['tag_name'])]
        if not candidates: return {'status':'no_approved_release'}
        release=max(candidates,key=lambda r:int(r['tag_name'].rsplit('-',1)[1]))
        run_id=int(release['tag_name'].rsplit('-',1)[1])
        if run_id<=state['highestRun']: return {'status':'unchanged','commit':state.get('commit')}
        with tempfile.TemporaryDirectory(prefix='download-',dir=STATE) as tmp:
            work=Path(tmp)
            assets={a['name']:a for a in release['assets']}
            for name in ('approval.json','approval.sigstore.jsonl'):
                download(assets[name]['browser_download_url'],work/name)
            # Verify signed bytes before parsing or trusting any manifest fields.
            subprocess.run([GH,'attestation','verify',str(work/'approval.json'),'--repo',REPO,'--bundle',str(work/'approval.sigstore.jsonl'),'--cert-identity',IDENTITY,'--deny-self-hosted-runners'],check=True,timeout=90,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            manifest=json.loads((work/'approval.json').read_text());manifest_check(manifest,run_id)
            # Bind the actual attested source to a locally pinned reviewer-gated workflow.
            # A repo writer cannot substitute a same-name workflow with the gate removed.
            subprocess.run([GH,'attestation','verify',str(work/'approval.json'),'--repo',REPO,'--bundle',str(work/'approval.sigstore.jsonl'),'--cert-identity',IDENTITY,'--deny-self-hosted-runners','--source-ref','refs/heads/main','--source-digest',manifest['workflowCommit']],check=True,timeout=90,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            workflow=download('https://raw.githubusercontent.com/'+REPO+'/'+manifest['workflowCommit']+'/.github/workflows/brief-release.yml')
            if hashlib.sha256(workflow).hexdigest()!=Path('/usr/local/lib/nabi-release/workflow.sha256').read_text().strip():
                raise ValueError('Signer workflow policy changed; local administrator review required')
            archive=work/'brief-arm64.tar.gz'
            download(assets[archive.name]['browser_download_url'],archive,MAX_ARCHIVE)
            if hashlib.file_digest(archive.open('rb'),'sha256').hexdigest()!=manifest['sha256']: raise ValueError('Archive digest mismatch')
            state.update(highestRun=run_id,status='staging',requestedCommit=manifest['commit'])
            write_state(state)
            candidate=RELEASES/(manifest['commit']+'-'+str(run_id))
            os.umask(0o022)  # Public, root-owned code must be readable by service user.
            candidate.mkdir(mode=0o755)
            extract(archive,candidate)
            build=json.loads((candidate/'BUILD.json').read_text())
            if build!={'commit':manifest['commit'],'architecture':'aarch64','python':'3.13'}: raise ValueError('Wrong release target')
            subprocess.run(['/usr/bin/python3','-m','venv',str(candidate/'.venv')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            subprocess.run([str(candidate/'.venv/bin/python'),'-m','pip','install','--no-index','--find-links',str(candidate/'wheels'),'-r',str(candidate/'requirements.txt')],check=True,timeout=180,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            subprocess.run([str(candidate/'.venv/bin/python'),'-m','pip','check'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            subprocess.run(['/usr/bin/python3','/usr/local/lib/nabi-release/verify_candidate.py',str(candidate)],check=True,timeout=30,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            os.umask(0o077)
            # Schema changes are additive. Never restore an old database over new private writes.
            with sqlite3.connect('/var/lib/nabi-brief/brief.sqlite3') as db,sqlite3.connect(STATE/('backup-'+str(run_id)+'.sqlite3')) as backup: db.backup(backup)
            state.update(highestRun=run_id,status='deploying',requestedCommit=manifest['commit'])
            write_state(state)
            restart=lambda:subprocess.run(['/bin/systemctl','restart','nabi-brief.service'],check=True,timeout=30)
            try:
                rollout(CURRENT,candidate,restart,verify_live)
            except BaseException:
                state.update(status='rolled_back');write_state(state);raise
            state.update(status='deployed',commit=manifest['commit']);write_state(state)
            return {'status':'deployed','commit':manifest['commit'],'runId':run_id}


if __name__=='__main__':
    os.umask(0o077)
    try: print(json.dumps(update()))
    except Exception:
        if (STATE/'state.json').exists():
            state=json.loads((STATE/'state.json').read_text())
            if state.get('status')=='staging': state['status']='failed_before_switch';write_state(state)
        print(json.dumps({'status':'failed','reason':'verification_or_deployment_failed'}));raise SystemExit(1)
