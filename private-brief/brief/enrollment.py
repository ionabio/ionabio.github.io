"""Fixed-client RFC 8628 phone authorization; hashed, expiring, revocable tokens.

No client registration, redirects, general identity administration, or token escrow.
Rotating refresh is atomic. An ambiguous consumed response requires reconnection.
"""
import json
import re
import secrets
import time
from flask import abort, jsonify, render_template, request
from .adapters import digest
from .publishing import PREFIX

CLIENT_ID = 'appgprj_6abe75379874819189b7c95bebf9e21c'
CLIENT_NAME = 'Private Connection Test · dottie'
CLIENT_ORIGIN = 'https://private-connection-test.nabio.chatgpt.site'
SCOPES = ['approve', 'prepare', 'publish', 'review', 'status']
ACCESS_SECONDS = 900
REFRESH_SECONDS = 30 * 86400
GRANT_SECONDS = 365 * 86400
DEVICE_SECONDS = 600
DEVICE_GRANT = 'urn:ietf:params:oauth:grant-type:device_code'
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def opaque(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{64}', value))


def revoke(db, grant_id):
    db.execute('UPDATE publisher_grants SET revoked=1 WHERE id=?', (grant_id,))
    db.execute('UPDATE publishers SET revoked=1 WHERE id IN (SELECT publisher_id FROM publisher_grant_access WHERE grant_id=?)', (grant_id,))


def register(app, store):
    def enabled():
        if not app.config['ENROLLMENT_ENABLED']:
            abort(404)

    def error(reason, code=400):
        return jsonify(error=reason), code

    def rate(bucket, maximum, seconds=60):
        now = time.time()
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT count,until FROM publisher_rates WHERE id=?', (bucket,)).fetchone()
            count = row['count'] if row and row['until'] > now else 0
            until = row['until'] if row and row['until'] > now else now + seconds
            if count >= maximum:
                return False
            db.execute('INSERT OR REPLACE INTO publisher_rates VALUES (?,?,?)', (bucket, count + 1, until))
        return True

    def machine(fields):
        enabled()
        if request.headers.get('Cookie') or request.headers.get('Origin'):
            abort(403)
        request.max_content_length = 4096
        if request.content_length and request.content_length > 4096:
            abort(413)
        if request.mimetype != 'application/x-www-form-urlencoded':
            abort(415)
        if set(request.form) != fields or any(len(request.form.getlist(k)) != 1 for k in fields):
            abort(400)
        if request.form.get('client_id') != CLIENT_ID:
            abort(400)
        if not rate('enrollment:machine', 120):
            abort(429)
        return request.form

    def issue(db, grant, now):
        access, refresh = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
        publisher_id = 'dottie:' + secrets.token_hex(16)
        refresh_expires = min(now + REFRESH_SECONDS, grant['absolute_expires'])
        access_expires = min(now + ACCESS_SECONDS, refresh_expires)
        db.execute('INSERT INTO publishers VALUES (?,?,?,0,?)', (publisher_id, digest(access), access_expires, json.dumps(SCOPES)))
        db.execute('INSERT INTO publisher_grant_access VALUES (?,?)', (publisher_id, grant['id']))
        db.execute('INSERT INTO publisher_refreshes VALUES (?,?,?,NULL)', (digest(refresh), grant['id'], refresh_expires))
        db.execute('UPDATE publisher_grants SET expires=? WHERE id=?', (refresh_expires, grant['id']))
        return jsonify(access_token=access, refresh_token=refresh, token_type='Bearer',
                       expires_in=max(0, int(access_expires - now)), scope=' '.join(SCOPES),
                       refresh_expires_at=refresh_expires, grant_id=grant['id'],
                       grant_expires_at=grant['absolute_expires'])

    @app.post(PREFIX + '/enrollment/device')
    def device():
        machine({'client_id'})
        if not rate('enrollment:device', 10, 3600):
            abort(429)
        device_code = secrets.token_urlsafe(48)
        user_code = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(10))
        now = time.time()
        with store.connect() as db:
            db.execute('DELETE FROM publisher_devices WHERE expires<?', (now,))
            db.execute('INSERT INTO publisher_devices(device_hash,user_hash,expires,status) VALUES (?,?,?,?)',
                       (digest(device_code), digest(user_code), now + DEVICE_SECONDS, 'pending'))
        return jsonify(device_code=device_code, user_code=user_code[:5] + '-' + user_code[5:],
                       verification_uri=app.config['PUBLIC_ORIGIN'] + '/connect', expires_in=DEVICE_SECONDS, interval=5)

    @app.post(PREFIX + '/enrollment/token')
    def token():
        enabled()
        request.max_content_length = 4096
        kind = request.form.get('grant_type')
        if kind == DEVICE_GRANT:
            body = machine({'client_id', 'grant_type', 'device_code'})
            if not opaque(body['device_code']):
                return error('invalid_grant')
            now, key = time.time(), digest(body['device_code'])
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT * FROM publisher_devices WHERE device_hash=?', (key,)).fetchone()
                if not row or row['expires'] <= now:
                    return error('expired_token')
                if row['status'] == 'denied':
                    return error('access_denied')
                if row['status'] == 'consumed':
                    return error('invalid_grant')
                if now - row['last_poll'] < row['interval']:
                    db.execute('UPDATE publisher_devices SET interval=interval+5,last_poll=? WHERE device_hash=?', (now, key))
                    return error('slow_down')
                db.execute('UPDATE publisher_devices SET last_poll=? WHERE device_hash=?', (now, key))
                if row['status'] != 'approved':
                    return error('authorization_pending')
                grant_id = secrets.token_hex(16)
                absolute_expires = now + GRANT_SECONDS
                db.execute('INSERT INTO publisher_grants VALUES (?,?,?,?,0)', (grant_id, CLIENT_ID, now + REFRESH_SECONDS, absolute_expires))
                grant = db.execute('SELECT * FROM publisher_grants WHERE id=?', (grant_id,)).fetchone()
                db.execute('UPDATE publisher_devices SET status=? WHERE device_hash=?', ('consumed', key))
                return issue(db, grant, now)
        if kind == 'refresh_token':
            body = machine({'client_id', 'grant_type', 'refresh_token'})
            if not opaque(body['refresh_token']):
                return error('invalid_grant')
            now, key = time.time(), digest(body['refresh_token'])
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT * FROM publisher_refreshes WHERE token_hash=?', (key,)).fetchone()
                if not row:
                    return error('invalid_grant')
                grant = db.execute('SELECT * FROM publisher_grants WHERE id=?', (row['grant_id'],)).fetchone()
                if not grant or grant['revoked'] or min(grant['expires'], grant['absolute_expires']) <= now:
                    return error('invalid_grant')
                if row['used_at'] is not None:
                    revoke(db, grant['id'])
                    return error('invalid_grant')
                if row['expires'] <= now:
                    return error('invalid_grant')
                db.execute('UPDATE publisher_refreshes SET used_at=? WHERE token_hash=?', (now, key))
                return issue(db, grant, now)
        machine({'client_id', 'grant_type'})
        return error('unsupported_grant_type')

    @app.route('/connect', methods=['GET', 'POST'])
    def connect():
        enabled()
        message = None
        if request.method == 'POST':
            if not rate('enrollment:browser', 20, 600):
                abort(429)
            action = request.form.get('action')
            if action == 'revoke':
                with store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    for row in db.execute('SELECT id FROM publisher_grants WHERE client_id=?', (CLIENT_ID,)).fetchall():
                        revoke(db, row['id'])
                message = 'De verbinding is ingetrokken. Dottie kan niet meer publiceren.'
            elif action in ('approve', 'deny'):
                code = request.form.get('userCode', '').replace('-', '').replace(' ', '').upper()
                if len(code) != 10 or any(c not in CODE_ALPHABET for c in code):
                    message = 'Ongeldige of verlopen code.'
                else:
                    with store.connect() as db:
                        db.execute('BEGIN IMMEDIATE')
                        row = db.execute('SELECT * FROM publisher_devices WHERE user_hash=?', (digest(code),)).fetchone()
                        if not row or row['expires'] <= time.time() or row['status'] != 'pending':
                            message = 'Ongeldige of verlopen code.'
                        else:
                            db.execute('UPDATE publisher_devices SET status=? WHERE user_hash=?', ('approved' if action == 'approve' else 'denied', digest(code)))
                            message = 'Goedgekeurd. Ga terug naar dottie om de verbinding af te ronden.' if action == 'approve' else 'Verbinding geweigerd.'
            else:
                abort(400)
        with store.connect() as db:
            active = db.execute('SELECT count(*) FROM publisher_grants WHERE client_id=? AND revoked=0 AND expires>? AND absolute_expires>?', (CLIENT_ID, time.time(), time.time())).fetchone()[0]
        return render_template('connect.html', client_name=CLIENT_NAME, client_origin=CLIENT_ORIGIN, message=message, active=active)
