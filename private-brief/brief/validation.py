"""Shared, fail-closed validation for imported private content."""
import math
from datetime import datetime
from .adapters import article, deal
from .editorial import reviewed_language


def validate_bundle(bundle, now=None):
    from .pipeline import clock, limits
    local = clock(now)
    if not isinstance(bundle, dict) or bundle.get('date') != local.date().isoformat():
        raise ValueError('Input date must match current Brussels date')
    def walk(value, depth=0):
        if depth > 20: raise ValueError('Input nesting too deep')
        if isinstance(value, float) and not math.isfinite(value): raise ValueError('Nonfinite number')
        if isinstance(value, dict):
            for v in value.values(): walk(v, depth+1)
        elif isinstance(value, list):
            for v in value: walk(v, depth+1)
    walk(bundle)
    for key in ('preferences', 'learning'):
        if bundle.get(key) is not None and not isinstance(bundle[key], dict):
            raise ValueError('Invalid settings')
    limits(bundle.get('preferences') or {})
    def fresh(value):
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None or not 0 <= (local-stamp).total_seconds() <= 86400:
            raise ValueError('Source verification expired')
    for name in ('calendar', 'todos', 'news', 'deals'):
        source = bundle.get(name)
        if not isinstance(source, dict) or source.get('status') not in ('verified', 'unavailable'):
            raise ValueError('Source status required')
        items = source.get('items', [])
        if not isinstance(items, list) or len(items) > 100 or any(not isinstance(i, dict) for i in items):
            raise ValueError('Invalid source items')
        if source['status'] == 'unavailable':
            continue
        fresh(source['checkedAt'])
        for raw in items:
            if name in ('todos','calendar'):
                if not isinstance(raw.get('title'), str) or not raw['title'].strip() or len(raw['title']) > 300:
                    raise ValueError('Invalid title')
                if name == 'calendar' and not isinstance(raw.get('start'), str):
                    raise ValueError('Invalid calendar start')
            elif name == 'news':
                if not isinstance(raw.get('headline'), str) or not raw['headline'].strip(): raise ValueError('Invalid headline')
                if not isinstance(raw.get('authorizedText',''), str) or len(raw.get('authorizedText','')) > 12000:
                    raise ValueError('Source text too long')
                item = article(raw)
                if 'reviewedLanguage' in raw: reviewed_language(item, {})
            else:
                fresh(raw['checkedAt'])
                if deal(raw, bundle['date']) is None: raise ValueError('Expired deal')
    return bundle
