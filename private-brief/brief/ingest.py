"""Safe deterministic ingestion helpers; no subscription access inferred from receipt."""
import email
import json
import xml.etree.ElementTree as ET
from email.policy import default
from urllib.request import Request,build_opener,HTTPRedirectHandler
from .adapters import SENDERS,canonical_url,newsletter_text

def mime_newsletter(raw):
    message=email.message_from_bytes(raw,policy=default)
    sender=email.utils.parseaddr(message.get('From',''))[1].lower()
    if sender not in SENDERS:raise ValueError('Unsupported editorial sender')
    body=message.get_body(preferencelist=('html','plain'))
    if body is None:raise ValueError('Newsletter body unavailable')
    text=body.get_content()
    if body.get_content_type()=='text/html':text=newsletter_text(sender,text)
    return {'publisher':SENDERS[sender][0],'newsletterName':SENDERS[sender][1],'receivedHeader':message.get('Date'),'text':text}

RSS_CANDIDATES={'Het Nieuwsblad':'https://www.nieuwsblad.be/rss'}
def rss(publisher,fetch=None):
    """Candidate feed, not assumed live. Caller caches results by source/date."""
    if publisher not in RSS_CANDIDATES:raise ValueError('Unconfigured source')
    def default_fetch(url):
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):return None
        with build_opener(NoRedirect).open(Request(url,headers={'User-Agent':'NabiBrief/1.0 (personal reader)'}),timeout=15) as response:
            # Refuse redirects outside known publisher hosts and oversized content.
            if not response.url.startswith('https://www.nieuwsblad.be/'):raise ValueError('Unexpected redirect')
            data=response.read(1000001)
            if len(data)>1000000:raise ValueError('Feed too large')
            return data
    data=(fetch or default_fetch)(RSS_CANDIDATES[publisher])
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():raise ValueError('Unsafe XML')
    root=ET.fromstring(data)
    return [{'publisher':publisher,'headline':i.findtext('title',''),'canonicalUrl':canonical_url(i.findtext('link'),publisher),'publishedAt':i.findtext('pubDate'),'contentScope':'headline','authorizedText':''} for i in root.findall('./channel/item')][:30]

def normalize_tasks(items):
    return [{'title':str(i['title'])[:300],'due':i.get('due'),'completed':False,'sourceId':str(i.get('sourceId',''))[:200]} for i in items if not i.get('completed')][:100]
def normalize_calendar(items):
    return [{'title':str(i['title'])[:300],'start':str(i['start'])[:80],'end':str(i.get('end',''))[:80],'sourceId':str(i.get('sourceId',''))[:200]} for i in items if not i.get('cancelled')][:100]

def cached_source(store,source,date,fetch,refresh=False):
    """Fetch once per source/date; explicit refresh allowed, never log response contents."""
    from datetime import datetime,timezone
    from .adapters import digest
    key=digest('ingest:v1:'+source+':'+date)
    if refresh:
        with store.connect() as db:db.execute('DELETE FROM cache WHERE key=?',(key,))
    def compute():
        try:
            items=fetch()
            return {'status':'verified','checkedAt':datetime.now(timezone.utc).isoformat(),'items':items}
        except Exception:
            return {'status':'unavailable','checkedAt':datetime.now(timezone.utc).isoformat(),'items':[]}
    return store.cached(key,compute)
