import hashlib
import math
from datetime import datetime
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit

SENDERS = {'dshelpdesk@mail.standaard.be': ('De Standaard', 'Helpdesk'), 'nieuwsblad@mail.nieuwsblad.be': ('Het Nieuwsblad', 'Gezondheid')}
PUBLISHERS = {'De Standaard': 'www.standaard.be', 'Het Nieuwsblad': 'www.nieuwsblad.be'}
def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()
def canonical_url(value, publisher=None):
    if not value:
        return None
    u = urlsplit(value)
    hosts = [PUBLISHERS[publisher]] if publisher in PUBLISHERS else list(PUBLISHERS.values())
    if u.scheme != 'https' or u.hostname not in hosts or u.username or u.port not in (None,443):
        return None
    if not re.fullmatch(r'/cnt/[A-Za-z0-9_-]+|/(?:[a-z0-9-]+/)+[0-9]+\.html', u.path):
        return None
    return urlunsplit(('https',u.hostname,u.path,'',''))
class NewsletterText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style','footer','nav'):
            self.skip += 1
    def handle_endtag(self, tag):
        if tag in ('script','style','footer','nav') and self.skip:
            self.skip -= 1
    def handle_data(self, data):
        if not self.skip and not re.search(r'uitschrijven|unsubscribe|account|bekijk.*browser',data,re.I):
            self.parts.append(data.strip())
def newsletter_text(sender, html):
    if sender.lower() not in SENDERS:
        raise ValueError('Unsupported editorial sender')
    parser = NewsletterText()
    parser.feed(html)
    return ' '.join(x for x in parser.parts if x)
def article(item):
    from .media import media_record
    publisher = item['publisher']
    if publisher not in PUBLISHERS:
        raise ValueError('Unsupported publisher')
    scope = item.get('contentScope','headline')
    if scope not in ('headline','teaser','full_authorized'):
        raise ValueError('Invalid scope')
    if scope == 'full_authorized' and not item.get('accessVerified'):
        raise ValueError('Full text requires verified access')
    headline = str(item['headline'])[:300]
    url = canonical_url(item.get('canonicalUrl'),publisher)
    return dict(media=media_record(item.get('media')),publisher=publisher,newsletterName=item.get('newsletterName'),receivedAt=item.get('receivedAt'),publishedAt=item.get('publishedAt'),headline=headline,canonicalUrl=url,contentScope=scope,sourceStatus='available' if url else 'canonical_unresolved',text=str(item.get('authorizedText',''))[:12000],reviewedLanguage=item.get('reviewedLanguage'),key=digest(url or publisher+':'+ ' '.join(headline.lower().split())))
def deal(item, date):
    required = ('retailer','product','price','quantity','unit','validFrom','validTo','sourceUrl','checkedAt','availability')
    if any(k not in item for k in required):
        raise ValueError('Deal missing verified fields')
    url = urlsplit(item['sourceUrl'])
    allowed = {'AH Belgium': ('www.ah.be',), 'Delhaize Belgium': ('www.delhaize.be',)}
    if url.scheme != 'https' or url.hostname not in allowed.get(item['retailer'],()) or url.username or url.port not in (None,443):
        raise ValueError('Untrusted Belgian deal source')
    for stamp in (item['validFrom'],item['validTo'],date):
        datetime.strptime(stamp,'%Y-%m-%d')
    if not item['validFrom'] <= date <= item['validTo']:
        return None
    if not math.isfinite(float(item['price'])) or not math.isfinite(float(item['quantity'])) or float(item['price']) <= 0 or float(item['quantity']) <= 0 or item['unit'] not in ('kg','l','stuk'):
        raise ValueError('Invalid price, quantity or unit')
    result = {k:item[k] for k in required}
    result['sourceUrl'] = urlunsplit(('https',url.hostname,url.path,'',''))
    result['conditions'] = str(item.get('conditions',''))[:500]
    result['unitPrice'] = round(float(item['price'])/float(item['quantity']),2)
    if item.get('referencePrice') and float(item['referencePrice']) > float(item['price']):
        result['referencePrice'] = float(item['referencePrice'])
        result['discountPercent'] = round(100*(1-float(item['price'])/float(item['referencePrice'])))
    return result
