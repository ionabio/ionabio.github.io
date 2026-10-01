"""Draft, editorial selection, and publish approval are separate persistent stages."""
import json
from datetime import datetime
from .adapters import digest

def prepare(store,bundle,now=None,base_hash=None,compare=False):
    from .validation import validate_bundle
    validate_bundle(bundle,now)
    date=bundle['date']
    datetime.strptime(date,'%Y-%m-%d')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS drafts(date TEXT PRIMARY KEY, payload TEXT, hash TEXT, approved_hash TEXT)')
        payload=json.dumps(bundle,sort_keys=True,ensure_ascii=False)
        h=digest(payload)
        previous=db.execute('SELECT hash FROM drafts WHERE date=?',(date,)).fetchone()
        if compare and previous and previous[0]!=base_hash and previous[0]!=h:
            raise ValueError('Draft changed')
        if compare and not previous and base_hash is not None: raise ValueError('Draft changed')
        db.execute('INSERT INTO drafts VALUES (?,?,?,NULL) ON CONFLICT(date) DO UPDATE SET payload=excluded.payload,hash=excluded.hash,approved_hash=CASE WHEN drafts.hash=excluded.hash THEN drafts.approved_hash ELSE NULL END',(date,payload,h))
    return {'status':'draft','date':date,'hash':h}

def approve(store,date,expected_hash):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT hash FROM drafts WHERE date=?',(date,)).fetchone()
        if not row or row[0]!=expected_hash: raise ValueError('Draft changed; review current draft')
        db.execute('UPDATE drafts SET approved_hash=hash WHERE date=?',(date,))
    return {'status':'approved','date':date,'hash':expected_hash}

def approved(store,date):
    with store.connect() as db:
        row=db.execute('SELECT payload,hash,approved_hash FROM drafts WHERE date=?',(date,)).fetchone()
    if not row or row['hash']!=row['approved_hash']: raise ValueError('No approved current draft')
    return json.loads(row['payload'])

def publish_approved(store,date,expected_hash,now=None):
    from .pipeline import clock, run
    if date!=clock(now).date().isoformat(): raise ValueError('Wrong Brussels date')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM drafts WHERE date=?',(date,)).fetchone()
        if not row or row['hash']!=expected_hash or row['approved_hash']!=expected_hash:
            raise ValueError('Approval or draft changed')
        existing=db.execute('SELECT hash FROM briefs WHERE date=?',(date,)).fetchone()
        if existing and existing[0]!=expected_hash: raise ValueError('Date already published')
        return {**run(store,json.loads(row['payload']),now=now,transaction=db),'hash':expected_hash}

def reviewed_language(item,preferences):
    """Original AI-authored output is supplied with evidence and checked locally, no API call."""
    language=item.get('reviewedLanguage')
    if not isinstance(language,dict) or set(language)!={'sourceHash','summaryNl','vocabulary','puzzle'}:
        raise ValueError('Invalid reviewed language result')
    if language.get('sourceHash')!=digest(item['text']): raise ValueError('Source changed')
    def text(value, maximum):
        if not isinstance(value,str) or not value.strip() or len(value)>maximum: raise ValueError('Invalid exercise text')
    text(language['summaryNl'],700)
    if not isinstance(language['vocabulary'],list) or len(language['vocabulary'])>12: raise ValueError('Invalid vocabulary')
    if not isinstance(language['puzzle'],list) or len(language['puzzle'])>5: raise ValueError('Invalid puzzle')
    for word in language['vocabulary']:
        if not isinstance(word,dict) or not {'word','meaning'} <= set(word) or set(word)-{'word','meaning','example'}:
            raise ValueError('Invalid vocabulary entry')
        text(word['word'],100); text(word['meaning'],500)
        if 'example' in word: text(word['example'],500)
    for question in language.get('puzzle',[]):
        if not isinstance(question,dict) or set(question)!={'question','answer','explanation','evidence'}:
            raise ValueError('Invalid puzzle entry')
        for key in question: text(question[key],1000)
        evidence=question.get('evidence','')
        if not evidence or evidence not in item['text']: raise ValueError('Question has no accessible evidence')
    return {k:language[k] for k in ('summaryNl','vocabulary','puzzle')}
