import base64
import ipaddress
import json
from urllib.parse import urlsplit
from .adapters import digest
from .pipeline import clock

def validate_subscription(sub):
    # Restrict destinations before sending: subscription endpoint is attacker-controlled.
    u=urlsplit(sub.get('endpoint',''))
    allowed={'fcm.googleapis.com','updates.push.services.mozilla.com','web.push.apple.com'}
    if u.scheme!='https' or u.hostname not in allowed or u.username or u.port not in (None,443) or len(sub['endpoint'])>2048:
        raise ValueError('Unsupported push endpoint')
    for name, size in [('auth',16),('p256dh',65)]:
        value=sub.get('keys',{}).get(name,'')
        try:
            decoded=base64.urlsafe_b64decode(value+'='*((-len(value))%4))
        except Exception:
            raise ValueError('Invalid push key') from None
        if len(decoded)!=size:
            raise ValueError('Invalid push key')
    return {'endpoint':sub['endpoint'],'keys':{k:sub['keys'][k] for k in ('auth','p256dh')}}

def notify(store, config, now=None, sender=None, expected_hash=None):
    date=clock(now).date().isoformat()
    sent=0; attempted=0
    with store.connect() as db:
        brief=db.execute('SELECT hash FROM briefs WHERE date=?',(date,)).fetchone()
        if not brief:
            if expected_hash is not None: raise ValueError('No published brief')
            return {'status':'no_current_brief','sent':0}
        if expected_hash is not None and brief[0]!=expected_hash: raise ValueError('Published version changed')
        subscriptions=db.execute('SELECT * FROM subscriptions').fetchall()
    if not config.get('VAPID_PRIVATE_KEY'):
        return {'status':'disabled','sent':0,'providerAccepted':0,'phoneDelivery':'unknown'}
    if sender is None:
        from pywebpush import webpush
        sender=webpush
    for row in subscriptions:
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT hash FROM briefs WHERE date=?',(date,)).fetchone()
            if expected_hash is not None and (not current or current[0]!=expected_hash): raise ValueError('Published version changed')
            # Claim before network I/O. At-most-once attempt avoids duplicate notifications
            # after ambiguous network failures; it cannot guarantee delivery to the device.
            claimed=db.execute('INSERT OR IGNORE INTO deliveries VALUES (?,?,?)',(date,row['id'],'claimed')).rowcount
        if not claimed: continue
        attempted+=1; code=None
        try:
            sub=validate_subscription(json.loads(row['payload']))
            sender(subscription_info=sub,data=json.dumps({'date':date}),vapid_private_key=config['VAPID_PRIVATE_KEY'],vapid_claims={'sub':config['VAPID_SUBJECT']},timeout=10,ttl=3600)
            status='accepted'; sent+=1
        except Exception as error:
            response=getattr(error,'response',None)
            code=getattr(response,'status_code',None)
            status='revoked' if code in (404,410) else 'failed_or_unknown'
        with store.connect() as db:
            if code in (404,410): db.execute('DELETE FROM subscriptions WHERE id=?',(row['id'],))
            db.execute('UPDATE deliveries SET status=? WHERE date=? AND subscription=?',(status,date,row['id']))
    return {'status':'complete','sent':sent,'providerAccepted':sent,'attempted':attempted,'phoneDelivery':'unknown'}
