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

def notify(store, config, now=None, sender=None):
    date=clock(now).date().isoformat()
    if not config.get('VAPID_PRIVATE_KEY'):
        return {'status':'disabled','sent':0}
    if sender is None:
        from pywebpush import webpush
        sender=webpush
    sent=0
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        brief=db.execute('SELECT published_at FROM briefs WHERE date=?',(date,)).fetchone()
        if not brief:
            return {'status':'no_current_brief','sent':0}
        subscriptions=db.execute('SELECT * FROM subscriptions').fetchall()
        for row in subscriptions:
            previous=db.execute('SELECT status FROM deliveries WHERE date=? AND subscription=?',(date,row['id'])).fetchone()
            if previous:
                continue
            # Claim before network I/O. At-most-once attempt avoids duplicate notifications
            # after ambiguous network failures; it cannot guarantee delivery to the device.
            db.execute('INSERT INTO deliveries VALUES (?,?,?)',(date,row['id'],'claimed'))
            db.commit()
            try:
                sub=validate_subscription(json.loads(row['payload']))
                sender(subscription_info=sub,data=json.dumps({'date':date}),vapid_private_key=config['VAPID_PRIVATE_KEY'],vapid_claims={'sub':config['VAPID_SUBJECT']},timeout=10,ttl=3600)
                status='sent'; sent+=1
            except Exception as error:
                response=getattr(error,'response',None)
                code=getattr(response,'status_code',None)
                if code in (404,410):
                    db.execute('DELETE FROM subscriptions WHERE id=?',(row['id'],))
                status='revoked' if code in (404,410) else 'failed'
            db.execute('UPDATE deliveries SET status=? WHERE date=? AND subscription=?',(status,date,row['id']))
    return {'status':'complete','sent':sent}
