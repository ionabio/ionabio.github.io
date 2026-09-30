"""Optional private photos. Caller must verify license/permission, no remote image hotlinking."""
import re
from pathlib import Path
from .adapters import digest

def import_image(source,directory):
    data=Path(source).read_bytes()
    if not 0<len(data)<=10000000:raise ValueError('Image size invalid')
    if data.startswith(b'\x89PNG\r\n\x1a\n'):extension='png'
    elif data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9'):extension='jpg'
    elif data[:4]==b'RIFF' and data[8:12]==b'WEBP':extension='webp'
    else:raise ValueError('Only PNG, JPEG and WebP accepted')
    import hashlib
    identifier=hashlib.sha256(data).hexdigest()
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    target=directory/(identifier+'.'+extension)
    if not target.exists():target.write_bytes(data)
    return {'imageId':identifier,'extension':extension}

def media_record(raw):
    if not raw:return {'kind':'illustration','theme':'news'}
    if raw.get('kind')=='illustration':
        theme=raw.get('theme','news')
        return {'kind':'illustration','theme':theme if theme in ('news','health','science') else 'news'}
    if raw.get('kind')!='photo' or not raw.get('rightsVerified') or not raw.get('credit') or not raw.get('license'):
        raise ValueError('Photo requires verified rights and credit')
    if not re.fullmatch('[a-f0-9]{64}',raw.get('imageId','')) or raw.get('extension') not in ('png','jpg','webp'):
        raise ValueError('Invalid private image identifier')
    return {'kind':'photo','imageId':raw['imageId'],'extension':raw['extension'],'credit':str(raw['credit'])[:200],'license':str(raw['license'])[:200],'alt':str(raw.get('alt','Bijbehorend beeld'))[:300]}
