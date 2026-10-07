"""Read only the approved, bounded summary; never access watchlists or acknowledge delivery."""
import json
import math
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from .pipeline import clock
from .adapters import deal

SUMMARY = Path('/var/lib/nabi-offerhunter-summary/top-offers.json')
HOSTS = {'www.ah.be', 'www.delhaize.be', 'www.action.com', 'www.maxizoo.be'}
RETAILERS = {'AH Belgium': 'www.ah.be', 'Albert Heijn Belgium': 'www.ah.be', 'Delhaize Belgium': 'www.delhaize.be', 'Action Belgium': 'www.action.com', 'Maxi Zoo Belgium': 'www.maxizoo.be'}


def stamp(value):
    at = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if at.tzinfo is None: raise ValueError('Timezone required')
    return at


def url(value):
    u = urlsplit(value)
    if u.scheme != 'https' or u.hostname not in HOSTS or u.username or u.password or u.port not in (None, 443):
        raise ValueError('Official source required')
    if u.hostname == 'www.action.com' and not u.path.startswith('/nl-be/'):
        raise ValueError('Belgian source required')
    return urlunsplit(('https', u.hostname, u.path, '', ''))


def fields(value, keys):
    if not isinstance(value, dict): raise ValueError('Object required')
    result = {k: value[k] for k in keys if k in value}
    if any(isinstance(v, (dict, list)) or isinstance(v, str) and len(v) > 1000 for v in result.values()):
        raise ValueError('Invalid summary field')
    return result


def evidence(value, now):
    result = fields(value, ('sourceUrl', 'retrievedAt', 'httpStatus', 'cacheAgeSeconds', 'sha256'))
    result['sourceUrl'] = url(result['sourceUrl'])
    if not 0 <= (now-stamp(result['retrievedAt'])).total_seconds() <= 86400:
        raise ValueError('Expired evidence')
    if result['httpStatus'] != 200 or not isinstance(result['cacheAgeSeconds'], int) or not 0 <= result['cacheAgeSeconds'] <= 86400 or not re.fullmatch('[a-f0-9]{64}', result['sha256']):
        raise ValueError('Unverified evidence')
    return result


def offer(value, now, upcoming=False):
    result = fields(value, ('id', 'retailer', 'productKey', 'name', 'brand', 'pack', 'variant', 'bundlePrice', 'bundleCount', 'quantity', 'unit', 'validFrom', 'validTo', 'checkedAt', 'conditions', 'sourceUrl', 'evidenceUrl', 'evidencePage', 'coverDatesVerified', 'exactVariant'))
    result['sourceUrl'] = url(result['sourceUrl'])
    if urlsplit(result['sourceUrl']).hostname != RETAILERS.get(result['retailer']): raise ValueError('Retailer mismatch')
    if result['retailer'] == 'Action Belgium' and not re.fullmatch(r'/nl-be/p/[0-9]+/[^?#]*', urlsplit(result['sourceUrl']).path): raise ValueError('Belgian product required')
    for key in ('validFrom', 'validTo'):
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', result[key]): raise ValueError('Invalid date')
        datetime.strptime(result[key], '%Y-%m-%d')
    today = now.date().isoformat()
    if result['validFrom'] > result['validTo'] or (not upcoming and not result['validFrom'] <= today <= result['validTo']) or (upcoming and not today < result['validFrom']):
        raise ValueError('Wrong offer date')
    if not 0 <= (now-stamp(result['checkedAt'])).total_seconds() <= 86400:
        raise ValueError('Expired offer')
    for key in ('bundlePrice', 'bundleCount', 'quantity'):
        if isinstance(result[key], bool) or not math.isfinite(float(result[key])) or float(result[key]) <= 0:
            raise ValueError('Invalid amount')
    if result['unit'] not in ('kg', 'l', 'item', 'stuk') or not result['name'].strip(): raise ValueError('Invalid product')
    if 'evidenceUrl' in result: result['evidenceUrl'] = url(result['evidenceUrl'])
    if 'evidence' in value:
        result['evidence'] = evidence(value['evidence'], now)
        if urlsplit(result['evidence']['sourceUrl']).hostname != RETAILERS[result['retailer']]: raise ValueError('Evidence retailer mismatch')
    elif result.get('coverDatesVerified') is not True or not isinstance(result.get('evidencePage'), int):
        raise ValueError('Missing evidence')
    # Multi-variant family cards retain their pack/conditions but cannot invent exact unit comparisons.
    if not upcoming and result.get('exactVariant', result['retailer'] != 'Action Belgium') is True:
        candidate = dict(retailer={'Albert Heijn Belgium': 'AH Belgium'}.get(result['retailer'], result['retailer']), product=result['name'], price=result['bundlePrice'], quantity=result['quantity'], unit='stuk' if result['unit'] == 'item' else result['unit'], validFrom=result['validFrom'], validTo=result['validTo'], sourceUrl=result['sourceUrl'], checkedAt=result['checkedAt'], availability='In-store availability may differ; see retailer', conditions=result.get('conditions', ''))
        result['briefDeal'] = deal(candidate, today)
    return result


def read_snapshot(now=None, path=SUMMARY):
    local = clock(now)
    start = local.replace(hour=6, minute=0, second=0, microsecond=0)
    def waiting(status): return {'status': status, 'date': local.date().isoformat(), 'retryAfterSeconds': 10}
    try:
        with Path(path).open('rb') as source:
            raw = source.read(1000001)
        if len(raw) > 1000000: raise ValueError('Oversized summary')
        data = json.loads(raw)
        def bounded(value, depth=0):
            if depth > 12 or isinstance(value, float) and not math.isfinite(value): raise ValueError('Invalid JSON value')
            if isinstance(value, dict):
                for child in value.values(): bounded(child, depth+1)
            elif isinstance(value, list):
                if len(value) > 100: raise ValueError('Oversized list')
                for child in value: bounded(child, depth+1)
        bounded(data)
        if data['schemaVersion'] != 2 or data['consumer'] != 'daily-brief' or not re.fullmatch('[a-f0-9]{64}', data['receipt']):
            raise ValueError('Wrong summary')
        prepared, finished = stamp(data['preparedAt']), stamp(data['coverage']['finishedAt'])
        if prepared > local or finished > prepared: raise ValueError('Future snapshot')
        if local < start or prepared < start or finished < start: return waiting('stale')
        selected, upcoming, rejected, seen = [], [], 0, set()
        for key in ('returning', 'topOffers', 'upcomingOffers', 'priceAlerts', 'newFolders'):
            if not isinstance(data[key], list) or len(data[key]) > 100: raise ValueError('Invalid list')
        for raw_offer in [r['offer'] for r in data['returning']] + data['topOffers']:
            try:
                item = offer(raw_offer, local)
                if item['id'] not in seen:
                    seen.add(item['id']); selected.append(item)
            except (ValueError, TypeError, KeyError, AttributeError): rejected += 1
        for raw_offer in data['upcomingOffers'][:5]:
            try: upcoming.append(offer(raw_offer, local, upcoming=True))
            except (ValueError, TypeError, KeyError, AttributeError): rejected += 1
        sources = []
        for source in data['coverage']['sources']:
            if source.get('private'): continue
            record = fields(source, ('retailer', 'state', 'detail', 'indexed', 'sourceUrl', 'productKey'))
            if 'sourceUrl' in record: record['sourceUrl'] = url(record['sourceUrl'])
            if 'evidence' in source: record['evidence'] = evidence(source['evidence'], local)
            sources.append(record)
        alerts = []
        for raw_alert in data['priceAlerts'][:8]:
            try:
                alert = fields(raw_alert, ('id', 'kind', 'previousPrice'))
                observation = fields(raw_alert['observation'], ('productKey', 'name', 'retailer', 'pack', 'variant', 'quantity', 'unit', 'price', 'currency', 'sourceUrl', 'observedAt', 'provenance', 'priceType', 'promotion', 'conditions'))
                observation['sourceUrl'] = url(observation['sourceUrl'])
                if urlsplit(observation['sourceUrl']).hostname != RETAILERS.get(observation['retailer']): raise ValueError('Retailer mismatch')
                observation['evidence'] = evidence(raw_alert['observation']['evidence'], local)
                if urlsplit(observation['evidence']['sourceUrl']).hostname != RETAILERS.get(observation['retailer']): raise ValueError('Evidence retailer mismatch')
                if not 0 <= (local-stamp(observation['observedAt'])).total_seconds() <= 86400 or observation.get('promotion') is not False or observation.get('currency') != 'EUR' or not math.isfinite(float(observation['price'])) or float(observation['price']) <= 0: raise ValueError('Invalid current price')
                alert['observation'] = observation; alerts.append(alert)
            except (ValueError, TypeError, KeyError, AttributeError): rejected += 1
        folders = []
        for raw_folder in data['newFolders'][:8]:
            try:
                folder = fields(raw_folder, ('retailer', 'url', 'validFrom', 'validTo', 'checkedAt', 'coverage', 'version'))
                folder['url'] = url(folder['url'])
                for key in ('validFrom', 'validTo'): datetime.strptime(folder[key], '%Y-%m-%d')
                if not folder['validFrom'] <= local.date().isoformat() <= folder['validTo'] or not 0 <= (local-stamp(folder['checkedAt'])).total_seconds() <= 86400: raise ValueError('Invalid folder')
                folders.append(folder)
            except (ValueError, TypeError, KeyError, AttributeError): rejected += 1
        return {'status': 'ready', 'date': local.date().isoformat(), 'offerhunter': {'url': 'https://offerhunter.nabi.be/', 'preparedAt': data['preparedAt'], 'receipt': data['receipt'], 'offers': selected[:8], 'priceAlerts': alerts, 'folders': folders, 'upcoming': upcoming, 'coverage': {'finishedAt': data['coverage']['finishedAt'], 'sources': sources, 'rejectedRecords': rejected}, 'deliveryAcknowledged': False}}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return waiting('unavailable')
