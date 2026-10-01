"""Cloud client using a protected network-secret placeholder, no browser cookies."""
import argparse
import json
import os
import re
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
from .publishing import PREFIX


def main():
    p=argparse.ArgumentParser()
    p.add_argument('operation',choices=('prepare','review','approve','publish','status','notify'))
    p.add_argument('--date',required=True)
    p.add_argument('--hash')
    p.add_argument('--base-hash')
    p.add_argument('--input')
    p.add_argument('--output',help='Required private output file for review')
    a=p.parse_args()
    if not re.fullmatch('[0-9]{4}-[0-9]{2}-[0-9]{2}',a.date): p.error('ISO date required')
    token=os.environ.get('BRIEF_PUBLISH_TOKEN')
    if not token: p.error('BRIEF_PUBLISH_TOKEN network secret required')
    paths={'prepare':'/drafts','review':f'/drafts/{a.date}','approve':f'/drafts/{a.date}/approve','publish':f'/drafts/{a.date}/publish','status':f'/status/{a.date}','notify':f'/notifications/{a.date}'}
    payload=None
    if a.operation=='prepare':
        if not a.input: p.error('--input required')
        path=Path(a.input).resolve()
        if path.stat().st_size>950000: p.error('Input too large')
        payload={'brief':json.loads(path.read_text()),'baseHash':a.base_hash}
        if payload['brief'].get('date')!=a.date: p.error('Date mismatch')
    elif a.operation in ('approve','publish','notify'):
        if not a.hash: p.error('--hash required')
        payload={'hash':a.hash}
    if a.operation=='review' and not a.output: p.error('--output required for private review')
    if a.output:
        target=Path(a.output).resolve()
        root=Path(__file__).resolve().parents[2]
        if target==root or root in target.parents: p.error('Private output must be outside checkout')
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self,*args): return None
    req=Request('https://brief.nabi.be'+PREFIX+paths[a.operation],data=json.dumps(payload,allow_nan=False).encode() if payload is not None else None,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','User-Agent':'NabiBrief-Cloud/1.0'})
    try:
        with build_opener(NoRedirect).open(req,timeout=45) as response:
            data=response.read(1000001)
            if len(data)>1000000: raise ValueError('Response too large')
            result=json.loads(data)
    except HTTPError as error:
        print(json.dumps({'status':'http_error','code':error.code})); raise SystemExit(1)
    except Exception:
        print(json.dumps({'status':'transport_or_response_error'})); raise SystemExit(1)
    if a.operation=='review':
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as file: file.write(data)
        print(json.dumps({'status':'review_exported','date':result['date'],'hash':result['hash']}))
    else: print(json.dumps(result))


if __name__=='__main__': main()
