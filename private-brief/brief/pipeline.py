import json
import copy
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from .adapters import article, deal, digest
from .ingest import normalize_tasks, normalize_calendar
from .editorial import reviewed_language
ZONE = ZoneInfo('Europe/Brussels')
def clock(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(ZONE)
def due(now=None):
    return clock(now).hour >= 8

def limits(preferences):
    result = {}
    for name, default, ceiling in (('maxArticles',3,10),('maxVocabularyPerArticle',6,12),('maxQuestionsPerArticle',3,5)):
        value = preferences.get(name)
        if value is None or value == '':
            value = default
        if type(value) is not int or not 0 <= value <= ceiling:
            raise ValueError(f'{name} must be an integer from 0 to {ceiling}')
        result[name] = value
    return result

def run(store, bundle, now=None, generator=None, max_calls=2, max_input_chars=6000):
    local = clock(now)
    date = local.date().isoformat()
    if not due(now):
        return {'status':'not_due','date':date}
    if bundle.get('date') != date:
        raise ValueError('Input date must match current Brussels date')
    for name in ('calendar','todos','news','deals'):
        source = bundle.get(name,{})
        if source.get('status') not in ('verified','unavailable'):
            raise ValueError('Source status required')
        if source['status'] == 'verified':
            stamp = datetime.fromisoformat(source['checkedAt'].replace('Z','+00:00'))
            if not 0 <= (local-stamp).total_seconds() <= 86400:
                raise ValueError('Source verification expired')
    effective = limits(bundle.get('preferences') or {})
    content_hash = digest(json.dumps(bundle,sort_keys=True,ensure_ascii=False))
    # Hold an SQLite transaction for the entire run. One writer, no overlapping runs.
    # Language adapters must have bounded timeouts; a crashed process releases the lock.
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT hash FROM briefs WHERE date=?',(date,)).fetchone()
        if existing and existing[0] == content_hash:
            return {'status':'unchanged','date':date}
        articles, seen, calls = [], set(), 0
        for raw in bundle['news'].get('items',[]) if bundle['news']['status']=='verified' else []:
            if raw.get('include', True) is False or len(articles) >= effective['maxArticles']:
                continue
            item = article(raw)
            if item['key'] in seen:
                continue
            seen.add(item['key'])
            language_key = digest(json.dumps([item['key'],item['text'],item['contentScope'],bundle.get('learning',{}),item.get('reviewedLanguage'),effective,'v2']))
            cached = db.execute('SELECT payload FROM cache WHERE key=?',(language_key,)).fetchone()
            if cached:
                language = json.loads(cached[0])
            elif item.get('reviewedLanguage') or (generator and calls < max_calls and item['text']):
                try:
                    if item.get('reviewedLanguage'):
                        # Local hash/evidence validation uses the complete retained source.
                        language = reviewed_language(item, {**bundle.get('learning',{}), **effective})
                    else:
                        calls += 1
                        language = generator({**item,'text':item['text'][:max_input_chars]}, {**bundle.get('learning',{}), **effective})
                    if set(language) != {'summaryNl','vocabulary','puzzle'} or len(language['summaryNl'])>700 or len(language['vocabulary'])>12 or len(language['puzzle'])>5:
                        raise ValueError('Language output exceeds budget')
                    language = {**language, 'vocabulary':language['vocabulary'][:effective['maxVocabularyPerArticle']], 'puzzle':language['puzzle'][:effective['maxQuestionsPerArticle']]}
                    db.execute('INSERT OR REPLACE INTO cache VALUES (?,?)',(language_key,json.dumps(language,ensure_ascii=False)))
                except Exception:
                    language = {'summaryNl':'Taaloefening tijdelijk niet beschikbaar.','vocabulary':[],'puzzle':[]}
            else:
                language = {'summaryNl':'Geen taaloefening beschikbaar voor deze bron.','vocabulary':[],'puzzle':[]}
            item.pop('text'); item.pop('key'); item.pop('reviewedLanguage',None)
            language=copy.deepcopy(language)
            for question in language.get('puzzle',[]):
                question.pop('evidence',None)
            item.update(language)
            item['generatedAt']=local.isoformat()
            articles.append(item)
        offers=[]
        for raw in bundle['deals'].get('items',[]) if bundle['deals']['status']=='verified' else []:
            offer=deal(raw,date)
            if offer and offer not in offers:
                offers.append(offer)
        calendar = {**{k:bundle['calendar'].get(k) for k in ('status','checkedAt')}, 'items':normalize_calendar(bundle['calendar'].get('items',[])) if bundle['calendar']['status']=='verified' else []}
        todos = {**{k:bundle['todos'].get(k) for k in ('status','checkedAt')}, 'items':normalize_tasks(bundle['todos'].get('items',[])) if bundle['todos']['status']=='verified' else []}
        payload={'date':date,'dayType':'weekend' if local.weekday()>=5 else 'weekday','publishedAt':local.isoformat(),'learning':bundle.get('learning',{'level':'B2','locale':'nl-BE'}),'calendar':calendar,'todos':todos,'news':articles,'deals':offers,'sources':{k:{'status':bundle[k]['status'],'checkedAt':bundle[k].get('checkedAt')} for k in ('calendar','todos','news','deals')}}
        db.execute('INSERT INTO briefs VALUES (?,?,?,?) ON CONFLICT(date) DO UPDATE SET hash=excluded.hash,payload=excluded.payload,published_at=excluded.published_at',(date,content_hash,json.dumps(payload,ensure_ascii=False),local.isoformat()))
    return {'status':'published','date':date,'languageCalls':calls}
