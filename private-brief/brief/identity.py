"""Local administrator tool; never exposed over HTTPS. Explicit approval required."""
import argparse
import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from .adapters import digest
from .publishing import SCOPES
from .store import Store


def main():
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=('create','revoke','status'))
    p.add_argument('--id',required=True)
    p.add_argument('--database',default=os.environ.get('BRIEF_DATABASE'))
    p.add_argument('--expires',help='Explicit timezone-aware ISO expiry, at most 30 days')
    p.add_argument('--token-file',help='New private file outside repository; never stdout')
    p.add_argument('--scopes',default=','.join(sorted(SCOPES)))
    args=p.parse_args()
    if not args.database: p.error('Private database required')
    root=Path(__file__).resolve().parents[2]
    database=Path(args.database).resolve()
    if database==root or root in database.parents: p.error('Database must be outside repository')
    store=Store(database)
    if args.action=='create':
        if not args.expires or not args.token_file: p.error('Explicit expiry and private token file required')
        expires=datetime.fromisoformat(args.expires.replace('Z','+00:00'))
        if expires.tzinfo is None or not 0 < expires.timestamp()-time.time() <= 30*86400: p.error('Expiry must be within 30 days')
        scopes=set(args.scopes.split(','))
        if not scopes or not scopes <= SCOPES: p.error('Unknown scope')
        path=Path(args.token_file).resolve()
        if path==root or root in path.parents: p.error('Token file must be outside repository')
        token=secrets.token_urlsafe(48)
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT id FROM publishers WHERE id=?',(args.id,)).fetchone(): p.error('Identity exists; use a new id')
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f: f.write(token)
            db.execute('INSERT INTO publishers VALUES (?,?,?,0,?)',(args.id,digest(token),expires.timestamp(),json.dumps(sorted(scopes))))
    elif args.action=='revoke':
        with store.connect() as db: db.execute('UPDATE publishers SET revoked=1 WHERE id=?',(args.id,))
    with store.connect() as db:
        row=db.execute('SELECT id,expires,revoked,scopes FROM publishers WHERE id=?',(args.id,)).fetchone()
    print(json.dumps(dict(row) if row else {'status':'missing'}))


if __name__=='__main__': main()
