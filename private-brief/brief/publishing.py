"""Narrow machine publishing API. Browser credentials never authorize these routes."""
import json
import re
import secrets
import time
from flask import Blueprint, abort, jsonify, request
from .adapters import digest
from .editorial import prepare, approve, publish_approved
from .pipeline import clock, run
from .push import notify

PREFIX = '/api/publisher/v1'
SCOPES = {'prepare', 'review', 'approve', 'publish', 'status', 'notify'}


def authorize(store):
    # Never interpret a browser cookie as machine auth or allow cross-origin browser use.
    if request.headers.get('Cookie') or request.headers.get('Origin'): abort(403)
    header = request.headers.get('Authorization','')
    token = header[7:] if header.startswith('Bearer ') else ''
    now = time.time()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM publishers WHERE token_hash=?', (digest(token),)).fetchone() if 40 <= len(token) <= 200 else None
        grant = None
        valid = row and not row['revoked'] and row['expires'] > now
        if valid:
            grant = db.execute('SELECT g.id,g.revoked,g.expires,g.absolute_expires FROM publisher_grants g JOIN publisher_grant_access a ON a.grant_id=g.id WHERE a.publisher_id=?',(row['id'],)).fetchone()
            if grant and (grant['revoked'] or min(grant['expires'],grant['absolute_expires']) <= now): valid = False
        bucket = ('grant:'+grant['id'] if grant else 'identity:'+row['id']) if valid else 'unauthenticated'
        previous = db.execute('SELECT count,until FROM publisher_rates WHERE id=?',(bucket,)).fetchone()
        count = previous['count'] if previous and previous['until'] > now else 0
        until = previous['until'] if previous and previous['until'] > now else now+60
        limited = count >= (60 if valid else 120)
        if not limited:
            db.execute('INSERT OR REPLACE INTO publisher_rates VALUES (?,?,?)',(bucket,count+1,until))
    if limited: abort(429)
    if not valid: abort(401)
    return set(json.loads(row['scopes']))


def register(app, store):
    api = Blueprint('publisher', __name__, url_prefix=PREFIX)
    def permission(scope):
        if scope not in authorize(store): abort(403)
    def date_check(date):
        if date != clock(app.config.get('PUBLISHER_NOW')).date().isoformat(): abort(409)
    def body(fields):
        if request.mimetype != 'application/json': abort(415)
        value = request.get_json()
        if not isinstance(value, dict) or set(value) != fields: abort(400)
        return value
    def hash_check(h):
        if not isinstance(h,str) or not re.fullmatch('[a-f0-9]{64}',h): abort(400)
    @api.errorhandler(ValueError)
    @api.errorhandler(TypeError)
    @api.errorhandler(KeyError)
    @api.errorhandler(AttributeError)
    def invalid(error):
        # No private values or exception messages in responses/logs.
        return jsonify(status='rejected',reason='invalid_or_changed_input'),409
    @api.post('/drafts')
    def draft_prepare():
        permission('prepare')
        value = body({'brief','baseHash'})
        if value['baseHash'] is not None: hash_check(value['baseHash'])
        return jsonify(prepare(store,value['brief'],now=app.config.get('PUBLISHER_NOW'),base_hash=value['baseHash'],compare=True))
    @api.get('/drafts/<date>')
    def review(date):
        permission('review'); date_check(date)
        with store.connect() as db:
            row = db.execute('SELECT * FROM drafts WHERE date=?',(date,)).fetchone()
        if not row: abort(404)
        return jsonify(date=date,hash=row['hash'],approved=row['hash']==row['approved_hash'],brief=json.loads(row['payload']))
    @api.post('/drafts/<date>/approve')
    def approval(date):
        permission('approve'); date_check(date)
        value=body({'hash'}); hash_check(value['hash'])
        return jsonify(approve(store,date,value['hash']))
    @api.post('/drafts/<date>/publish')
    def publication(date):
        permission('publish'); date_check(date)
        value=body({'hash'}); hash_check(value['hash'])
        return jsonify(publish_approved(store,date,value['hash'],now=app.config.get('PUBLISHER_NOW')))
    @api.get('/status/<date>')
    def status(date):
        permission('status'); date_check(date)
        with store.connect() as db:
            draft=db.execute('SELECT hash,approved_hash FROM drafts WHERE date=?',(date,)).fetchone()
            published=db.execute('SELECT hash,published_at FROM briefs WHERE date=?',(date,)).fetchone()
            deliveries=dict(db.execute('SELECT status,count(*) FROM deliveries WHERE date=? GROUP BY status',(date,)).fetchall())
        return jsonify(date=date,draftHash=draft[0] if draft else None,approved=bool(draft and draft[0]==draft[1]),publishedHash=published[0] if published else None,publishedAt=published[1] if published else None,notifications=deliveries,phoneDelivery='unknown')
    @api.post('/notifications/<date>')
    def notification(date):
        permission('notify'); date_check(date)
        value=body({'hash'}); hash_check(value['hash'])
        return jsonify(notify(store,app.config,now=app.config.get('PUBLISHER_NOW'),expected_hash=value['hash']))
    app.register_blueprint(api)
