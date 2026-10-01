import argparse
import json
import os
from pathlib import Path
from .pipeline import run, clock
from .editorial import prepare, approve, approved, reviewed_language, publish_approved
from .push import notify
from .store import Store

def main():
    parser=argparse.ArgumentParser(description='Private morning brief tools. Only statuses go to stdout.')
    parser.add_argument('command',choices=['prepare','approve','publish','status','serve','export-draft'])
    parser.add_argument('--input',help='Private normalized input file')
    parser.add_argument('--output',help='Private draft export file')
    parser.add_argument('--date',default=clock().date().isoformat())
    parser.add_argument('--hash',help='Hash of reviewed draft; required for approval')
    parser.add_argument('--database',default=os.environ.get('BRIEF_DATABASE'))
    parser.add_argument('--notify',action='store_true')
    args=parser.parse_args()
    if not args.database: parser.error('Database path required')
    root=Path(__file__).resolve().parents[2]
    def private_path(value):
        path=Path(value).resolve()
        if root in path.parents: parser.error('Runtime data must be outside repository')
        return path
    store=Store(private_path(args.database))
    # Initialise drafts even before the first prepare.
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS drafts(date TEXT PRIMARY KEY,payload TEXT,hash TEXT,approved_hash TEXT)')
    try:
        if args.command=='status':
            with store.connect() as db:
                exists=bool(db.execute('SELECT date FROM briefs WHERE date=?',(args.date,)).fetchone())
                draft=db.execute('SELECT hash,approved_hash FROM drafts WHERE date=?',(args.date,)).fetchone()
            result={'date':args.date,'ready':exists,'draftHash':draft[0] if draft else None,'approved':bool(draft and draft[0]==draft[1])}
        elif args.command=='serve':
            from waitress import serve
            from .web import create_app
            serve(create_app(),host='127.0.0.1',port=8080)
            return
        elif args.command=='prepare':
            if not args.input: parser.error('--input required')
            path=private_path(args.input)
            if path.stat().st_size>1000000: raise ValueError('Input too large')
            result=prepare(store,json.loads(path.read_text(encoding='utf-8-sig')))
        elif args.command=='approve':
            if not args.hash: parser.error('--hash required')
            result=approve(store,args.date,args.hash)
        elif args.command=='export-draft':
            if not args.output: parser.error('--output required')
            with store.connect() as db:
                row=db.execute('SELECT payload FROM drafts WHERE date=?',(args.date,)).fetchone()
            if not row: raise ValueError('Missing draft')
            private_path(args.output).write_text(row[0],encoding='utf-8')
            result={'status':'exported','date':args.date}
        else:
            with store.connect() as db:
                draft=db.execute('SELECT approved_hash FROM drafts WHERE date=?',(args.date,)).fetchone()
            if not draft: raise ValueError('No draft')
            result=publish_approved(store,args.date,draft[0])
            if args.notify and result['status'] in ('published','unchanged'):
                result['notification']=notify(store,{'VAPID_PRIVATE_KEY':os.environ.get('BRIEF_VAPID_PRIVATE_KEY'),'VAPID_SUBJECT':os.environ.get('BRIEF_VAPID_SUBJECT')})
        print(json.dumps(result))
    except Exception:
        print(json.dumps({'status':'failed','reason':'input_or_runtime_error'}))
        raise SystemExit(1)
if __name__=='__main__':main()
