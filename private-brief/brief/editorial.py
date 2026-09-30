"""Draft, editorial selection, and publish approval are separate persistent stages."""
import json
from datetime import datetime
from .adapters import digest

def prepare(store,bundle):
    date=bundle['date']
    datetime.strptime(date,'%Y-%m-%d')
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS drafts(date TEXT PRIMARY KEY, payload TEXT, hash TEXT, approved_hash TEXT)')
        payload=json.dumps(bundle,sort_keys=True,ensure_ascii=False)
        h=digest(payload)
        db.execute('INSERT INTO drafts VALUES (?,?,?,NULL) ON CONFLICT(date) DO UPDATE SET payload=excluded.payload,hash=excluded.hash,approved_hash=CASE WHEN drafts.hash=excluded.hash THEN drafts.approved_hash ELSE NULL END',(date,payload,h))
    return {'status':'draft','date':date,'hash':h}

def approve(store,date,expected_hash):
    with store.connect() as db:
        row=db.execute('SELECT hash FROM drafts WHERE date=?',(date,)).fetchone()
        if not row or row[0]!=expected_hash: raise ValueError('Draft changed; review current draft')
        db.execute('UPDATE drafts SET approved_hash=hash WHERE date=?',(date,))
    return {'status':'approved','date':date,'hash':expected_hash}

def approved(store,date):
    with store.connect() as db:
        row=db.execute('SELECT payload,hash,approved_hash FROM drafts WHERE date=?',(date,)).fetchone()
    if not row or row['hash']!=row['approved_hash']: raise ValueError('No approved current draft')
    return json.loads(row['payload'])

def reviewed_language(item,preferences):
    """Original AI-authored output is supplied with evidence and checked locally, no API call."""
    language=item.get('reviewedLanguage')
    if not language: raise ValueError('No reviewed language result')
    if language.get('sourceHash')!=digest(item['text']): raise ValueError('Source changed')
    for question in language.get('puzzle',[]):
        evidence=question.get('evidence','')
        if not evidence or evidence not in item['text']: raise ValueError('Question has no accessible evidence')
    return {k:language[k] for k in ('summaryNl','vocabulary','puzzle')}
